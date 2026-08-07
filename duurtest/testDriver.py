"""
Endurance test driver.

Holds the test loop and runs it in its own thread, so the GUI only has to
start it, stop it and read its status. There is no Qt code in here on
purpose: this module is plain Python and can be used on its own.

    test = DuurTest(TestSettings(port="COM11", units=["CX01"], ...))
    test.start()
    while test.running:
        print(test.percentage, test.message)

One test run consists of:

    relay on -> wait -> open the serial port to the machine
    for every dispense:
        pick a unit and an amount, dispense it
        wait until the machine reports IDLE again
        after every power_cycle_interval dispenses: relay off, on, reconnect
    relay off -> disconnect

The block at the bottom only runs when this module is started directly,
which is handy to check whether the relay and the machine respond without
involving the GUI:
    python -m duurtest.testDriver
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import serial

from .control_board import ControlBoard, ResultCode
from .logsetup import start_run_log, stop_run_log
from .relay import RelayController, RelayControllerConfig

log = logging.getLogger(__name__)                       # the test itself
machine_log = logging.getLogger("duurtest.machine")     # traffic to the control board

# Unit name -> number, as the machine knows them. The names match the
# object names of the checkboxes in Duurtest_GUI.ui.
UNITS: dict[str, int] = {
    'CX01': 1, 'MH01': 2, 'YH04': 3, 'RH01': 4,
    'YX01': 5, 'WX01': 6, 'CH01': 7, 'GH01': 8,
    'BH01': 9, 'OH01': 10, 'RX01': 11, 'YH01': 12,
    'GX01': 13, 'BX01': 14, 'YH02': 15, 'DISP16': 16,
}

# Connection to the machine's control board. The driver talks to it directly
# over serial, using the VIMBus protocol in control_board.py; there is no
# xmlrpc service in between any more. The port is picked in the GUI, the rest
# is fixed by the protocol.
MACHINE_ADDRESS = 0x0002
MACHINE_BAUDRATE = 19200
MACHINE_ENCRYPTION = True
MACHINE_TIMEOUT = 5.0        # seconds to wait for a reply, so a silent board
                             # cannot block the test thread forever
DISPENSE_CALL_TIMEOUT = 120.0  # longer window for dispense_all, which may only
                               # answer once the machine has picked the job up

# dispense_nl() works in nanolitres while the window asks for millilitres.
NL_PER_ML = 1_000_000

FILL_LEVEL = 3800          # value passed to correct_fill_level
POWER_ON_DELAY = 10.0      # wait after switching on until the unit has booted
POWER_OFF_DELAY = 5.0      # how long the power stays off during a power cycle

# Instead of guessing how long a dispense takes, the machine is asked:
# get_status() answers "IDLE" or "DISPENSING", and the next round starts
# once it is idle again. The answer covers the whole machine, not one unit.
STATUS_IDLE = "IDLE"
STATUS_POLL_INTERVAL = 0.5   # how often the machine is asked for its status

# The machine does not report DISPENSING the instant the command is sent, so
# an IDLE answer right after starting means "not started yet" rather than
# "finished". Only after this long without ever seeing it busy is a dispense
# taken to be done. Waiting costs nothing when the machine does report
# DISPENSING in time, so this is generous on purpose; if the log keeps saying a
# dispense was never seen running, raise it.
DISPENSE_START_GRACE = 5.0

# A machine that never returns to IDLE would keep the test waiting forever.
DISPENSE_TIMEOUT = 600.0

POWER_OFF_ATTEMPTS = 3     # tries to get the relay off before giving up

# Stop has to be able to cut the power even while the test thread is busy,
# so both threads take _relay_lock before touching the relay. Commands to the
# relay are bounded by the timeouts in relay.py, about a second, so the lock
# is never held long; this is how long Stop waits for it.
RELAY_LOCK_TIMEOUT = 3.0

# A long dispense keeps the unit busy for a while, and the reply frame that
# comes back afterwards is sometimes unreadable. Rather than ending the test,
# the driver reconnects and tries the call again this many times.
DISPENSER_RETRIES = 3
RESYNC_DELAY = 2.0         # pause before reconnecting, lets the line go quiet


@dataclass
class TestSettings:
    """
    Everything the operator fills in in the GUI. gui.read_settings() builds
    one of these; running without a GUI means filling it in by hand.
    """

    port: str = ""                     # COM port of the relay/STM32
    baudrate: int = 115200
    machine_port: str = ""             # COM port of the machine's control board
    units: list[str] = field(default_factory=list)   # names from UNITS
    dispense_number: int = 0           # total number of dispenses in the test
    power_cycle_interval: int = 0      # power cycle every N dispenses, 0 = off
    random_dispense: bool = False      # False = fixed amount, True = random
    dispense_amount: int = 0           # used when random_dispense is False
    dispense_min: int = 0              # used when random_dispense is True
    dispense_max: int = 0

    def validate(self) -> Optional[str]:
        """
        Check the settings before a test is started.

        Returns a message describing the first problem found, or None when
        everything is in order. The message is shown as-is by the GUI, which
        is why it is Dutch.
        """
        if not self.port:
            return "Selecteer een com-port voor de relay."
        if not self.machine_port:
            return "Selecteer een com-port voor de machine."
        if self.machine_port == self.port:
            return "De relay en de machine kunnen niet op dezelfde com-port zitten."
        if not self.units:
            return "Selecteer minimaal één unit."
        # Catches a checkbox whose object name does not appear in UNITS,
        # which would otherwise only fail halfway through the test.
        unknown = [u for u in self.units if u not in UNITS]
        if unknown:
            return f"Onbekende unit(s): {', '.join(unknown)}"
        if self.dispense_number <= 0:
            return "Aantal dispenses moet groter zijn dan 0."
        if self.random_dispense:
            if self.dispense_max <= 0:
                return "Vul een max dispense waarde in."
            if self.dispense_min > self.dispense_max:
                return "Min dispense mag niet groter zijn dan max."
        elif self.dispense_amount <= 0:
            return "Vul een dispense hoeveelheid in."
        return None

    def next_amount(self) -> int:
        """Amount in ml for the next dispense, fixed or drawn at random."""
        if self.random_dispense:
            return random.randint(self.dispense_min, self.dispense_max)
        return self.dispense_amount


class _LoggedBoard:
    """
    Wraps ControlBoard so every exchange with the machine is logged: the
    command with its arguments, how long it took, and the variables that came
    back or the error it raised.

    Wrapping rather than logging at each call site means a command added later
    is logged too, and the loop stays readable. vimbus does log the raw frames
    itself, but on the root logger and at debug level.
    """

    # Waiting for a dispense asks the machine for its status twice a second,
    # which would bury everything else: a dispense of a hundred seconds is four
    # hundred lines saying the same thing. Those go to debug level, so the file
    # keeps the story ("CX01 is begonnen", "CX01 is klaar na 98.5 s") while
    # every single exchange is still there when logging runs at DEBUG.
    # Failures are always logged, however often the command is sent.
    QUIET_CALLS = {"get_status"}

    def __init__(self, board: ControlBoard):
        self.board = board

    def __getattr__(self, name: str):
        method = getattr(self.board, name)
        level = logging.DEBUG if name in self.QUIET_CALLS else logging.INFO

        def call(*args, **kwargs):
            arguments = ", ".join(repr(a) for a in args)
            machine_log.log(level, "TX  %s(%s)", name, arguments)
            started = time.time()
            try:
                result = method(*args, **kwargs)
            except Exception as exc:                 # noqa: BLE001 - log, then pass on
                machine_log.error("RX  %s mislukt na %.0f ms: %s",
                                  name, (time.time() - started) * 1000, exc)
                raise
            # A Command carries its decoded variables; show those rather than
            # the object, since that is what the machine actually answered.
            answer = getattr(result, "vars", result)
            machine_log.log(level, "RX  %s -> %s   (%.0f ms)",
                            name, answer, (time.time() - started) * 1000)
            return result

        return call


class _Stopped(Exception):
    """
    Raised internally when stop() has been called.

    Using an exception means the wait helper can break out from anywhere in
    the loop, while the finally block still switches the relay off.
    """


class DuurTest:
    """
    A single run of the endurance test.

    start() runs the loop on a separate thread and returns immediately;
    stop() asks it to end. While it runs, the caller reads the status
    attributes below. Those are plain values written by the test thread and
    read by the GUI thread, which needs no lock: each is written in one
    assignment, and the GUI only displays them.
    """

    def __init__(self, settings: TestSettings):
        self.settings = settings

        # Status, read by the GUI.
        self.running = False                # True between start() and the end
        self.completed = False              # True when all dispenses were done
        self.percentage = 0                 # 0..100, for the progress bar
        self.message = ""                   # last status line, for the status bar
        self.error: Optional[str] = None    # set when the test failed
        self.resyncs = 0                    # times the machine had to be reconnected
        self.warnings = 0                   # warning codes the machine returned
        self.errors = 0                     # error codes the machine returned
        self.logfile: Optional[Path] = None  # log file of this run

        self.relay = None                              # created by _loop()
        self.machine = None                            # created by _open_machine()
        self._machine_serial: Optional[serial.Serial] = None
        self._log_handler: Optional[logging.Handler] = None
        self._stop = False                             # stop requested
        self._thread: Optional[threading.Thread] = None
        # Held by whichever thread is talking to the relay, so stop() can cut
        # the power without landing in the middle of a command.
        self._relay_lock = threading.Lock()

    # -- control ----------------------------------------------------
    def start(self) -> None:
        """
        Validate the settings and start the test on its own thread.

        Raises ValueError when the settings are not usable. The thread is a
        daemon so a forgotten test can never keep the process alive; closing
        the window still calls stop() and waits, so the relay is switched
        off properly.
        """
        error = self.settings.validate()
        if error:
            log.error("Test niet gestart: %s", error)
            raise ValueError(error)

        # The log file of this run, opened before the first line is written
        # so the whole run ends up in it.
        self.logfile, self._log_handler = start_run_log()

        s = self.settings
        log.info("=" * 70)
        log.info("Test gestart: %d dispenses over %d units", s.dispense_number, len(s.units))
        log.info("  relay      : %s @ %s baud", s.port, s.baudrate)
        log.info("  machine    : %s @ %s baud, adres 0x%04X",
                 s.machine_port, MACHINE_BAUDRATE, MACHINE_ADDRESS)
        log.info("  units      : %s", ", ".join(s.units))
        log.info("  hoeveelheid: %s",
                 f"{s.dispense_min}-{s.dispense_max} ml random" if s.random_dispense
                 else f"{s.dispense_amount} ml vast")
        log.info("  powercycle : %s",
                 f"elke {s.power_cycle_interval} dispenses" if s.power_cycle_interval else "uit")

        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """
        Ask the test to stop after the step that is currently running.

        Sets the flag the loop checks between steps, and switches the power
        off right here rather than leaving that to the test thread.

        That last part matters: if the test thread is stuck in a call to the
        machine it never reaches its own shutdown, and the machine would
        stay powered for as long as that call hangs. Stop has to work then
        too, so it cuts the power itself.

        Doing that from the GUI thread is safe because both threads take
        _relay_lock before touching the relay. Waiting for the lock takes at
        most about a second: relay commands are bounded by the timeouts in
        relay.py. Closing the port is still left to the test thread, which
        owns it.
        """
        log.info("Stop ingedrukt")
        self._stop = True
        if not self._power_off_now():
            # The test thread is holding the relay; its own shutdown will
            # switch off in a moment.
            log.warning("Relay was bezig, uitschakelen wordt door de testthread gedaan")
            self.message = "Stoppen, relay is bezig..."

    def wait(self, timeout: Optional[float] = None) -> None:
        """Block until the test thread has ended, or until timeout seconds."""
        if self._thread is not None:
            self._thread.join(timeout)

    # -- the test ---------------------------------------------------
    def _run(self) -> None:
        """
        Thread body: run the loop and record how it ended.

        Everything is caught here. An exception on this thread would
        otherwise disappear into the console while the GUI kept waiting, so
        it is stored in self.error instead and shown by the GUI.
        """
        try:
            self._loop()
            self.completed = True
            self.message = self._summary()
            log.info(self.message)
        except _Stopped:
            self.message = "Test gestopt"
            log.info("Test gestopt door de gebruiker bij %d%%", self.percentage)
        except Exception as exc:                 # noqa: BLE001 - report to the GUI
            self.error = str(exc)
            self.message = f"Fout: {exc}"
            # exc_info puts the traceback in the file, which says a lot more
            # than the one line the GUI shows.
            log.exception("Test afgebroken door een fout: %s", exc)
        finally:
            log.info("Log van deze run: %s", self.logfile)
            # Close this run's file before running is cleared, so the file is
            # complete by the time the GUI reports the test as finished.
            stop_run_log(self._log_handler)
            self._log_handler = None
            # Set last, so the GUI sees the final status in the same poll in
            # which it notices the test has ended.
            self.running = False

    def _summary(self) -> str:
        """
        How the run went, in one line: not just that it finished, but what
        the machine reported along the way.
        """
        parts = [
            f"{self.warnings} waarschuwing" + ("en" if self.warnings != 1 else ""),
            f"{self.errors} fout" + ("en" if self.errors != 1 else ""),
        ]
        if self.resyncs:
            parts.append(f"{self.resyncs}x opnieuw verbonden")
        return "Test afgerond: " + ", ".join(parts)

    def _loop(self) -> None:
        """The test itself: connect, dispense, power cycle, disconnect."""
        settings = self.settings
        total = settings.dispense_number
        interval = settings.power_cycle_interval

        # RelayControllerConfig is a plain class, so it is created empty and
        # the port picked in the GUI is assigned onto it. The other fields
        # keep the defaults from relay.py.
        config = RelayControllerConfig()
        config.port = settings.port
        config.baudrate = settings.baudrate
        self.relay = RelayController(config)

        self.message = f"Verbinden met relay op {settings.port}..."
        with self._relay_lock:
            self.relay.connect()

        try:
            self._power_on(self.relay)

            for done in range(1, total + 1):
                self._check_stop()

                unit = random.choice(settings.units)
                amount = settings.next_amount()
                self.message = f"Dispense {done}/{total}: {unit}, {amount} ml"
                log.info("--- Dispense %d/%d: %s, %d ml ---", done, total, unit, amount)

                self._prepare_dispense(unit, amount)
                garbled = self._start_dispense()
                if garbled:
                    # The reply was unreadable, so the connection is out of
                    # step. Resync before asking the machine anything else.
                    self._resync()

                self._wait_until_idle(unit)

                self.percentage = int(done / total * 100)

                # Power cycle in between, but not after the last dispense:
                # the finally block switches the power off anyway.
                if interval and done % interval == 0 and done < total:
                    self.message = f"Power cycle na {done} dispenses"
                    log.info("Power cycle na %d dispenses", done)
                    self._power_off_now()
                    self._sleep(POWER_OFF_DELAY)
                    self._power_on(self.relay)
        finally:
            # Runs on a normal finish, on stop and on an error, so the units
            # are never left powered. stop() may have switched off already;
            # sending OFF twice does no harm.
            self._settle()
            self._power_off_now()
            with self._relay_lock:
                self.relay.disconnect()
            self._close_machine()

    # -- talking to the machine -------------------------------------
    def _open_machine(self) -> None:
        """
        Open the serial port to the control board and greet it with a poll.

        The port carries a timeout, unlike the example in temp.py: without one
        a board that stops answering would leave readline() waiting for as
        long as the application runs.
        """
        port = self.settings.machine_port
        self.message = f"Verbinden met de machine op {port}..."
        log.info("Machine: poort %s openen op %d baud, adres 0x%04X",
                 port, MACHINE_BAUDRATE, MACHINE_ADDRESS)

        self._machine_serial = serial.Serial(port, baudrate=MACHINE_BAUDRATE,
                                             timeout=MACHINE_TIMEOUT)
        self.machine = _LoggedBoard(ControlBoard(MACHINE_ADDRESS, self._machine_serial,
                                                 machine_log, MACHINE_ENCRYPTION))
        self.machine.poll()

    def _close_machine(self) -> None:
        """Close the serial port to the control board. Never raises."""
        try:
            if self._machine_serial is not None and self._machine_serial.is_open:
                self._machine_serial.close()
                log.info("Machine: poort gesloten")
        except Exception:                        # noqa: BLE001 - shutting down wins
            pass
        self.machine = None
        self._machine_serial = None

    def _check_result(self, command, what: str) -> None:
        """
        Look at the result_code the machine returned and keep score.

        The machine answers with a code rather than an exception: Ok, a
        warning such as "unit almost empty", or an error such as "fill level
        too low to dispense". None of them stop the test, they are counted and
        logged, and the tally ends up in the final message.
        """
        code = getattr(command, "vars", {}).get("result_code")
        if code is None or code == ResultCode.OK:
            return

        name = code.name if isinstance(code, ResultCode) else str(code)
        if name.startswith("WARN_"):
            self.warnings += 1
            log.warning("%s: %s", what, name)
        else:
            self.errors += 1
            log.error("%s: %s", what, name)

    def _prepare_dispense(self, unit: str, amount_ml: int) -> None:
        """
        Correct the fill level and queue the unit for the coming dispense.

        Both only read and adjust state, so they may be repeated safely. A
        failure here is nearly always a garbled reply frame, such as

            Expected encrypted string '0071;0A08;416A00265D\\n'
            to contain 4 parts, got 3

        which means the serial connection is out of step: bytes were lost or
        a leftover fragment was read as if it were a new frame. Reopening the
        port starts a fresh frame and the same command then succeeds, so the
        test survives it instead of ending on the second dispense.
        """
        for attempt in range(1, DISPENSER_RETRIES + 1):
            try:
                self._check_result(
                    self.machine.correct_fill_level(unit, UNITS[unit], FILL_LEVEL),
                    f"correct_fill_level({unit})")
                self._check_result(
                    self.machine.dispense_nl(unit, amount_ml * NL_PER_ML),
                    f"dispense_nl({unit}, {amount_ml} ml)")
                return
            except _Stopped:
                raise
            except Exception as exc:             # noqa: BLE001 - vimbus raises plain Exception
                # Out of attempts: let it end the test, with the machine's own
                # message so it is clear where it came from.
                if attempt == DISPENSER_RETRIES:
                    raise
                self.resyncs += 1
                self.message = (f"Machine antwoordde onleesbaar, poging "
                                f"{attempt} van {DISPENSER_RETRIES}: {exc}")
                log.warning("Onleesbaar antwoord (poging %d/%d), opnieuw verbinden: %s",
                            attempt, DISPENSER_RETRIES, exc)
                self._resync()

    def _start_dispense(self) -> bool:
        """
        Trigger the queued dispense. Returns True when the reply was
        unreadable, so the caller knows a resync is needed.

        Deliberately not retried: a failure means the answer was unreadable,
        not that nothing happened. The machine may well be dispensing already,
        and repeating the command could dispense the amount twice.

        The call gets a longer timeout of its own, since dispense_all may only
        answer once the machine has taken the job on.
        """
        try:
            with self.machine.board.override_timeout(DISPENSE_CALL_TIMEOUT):
                self._check_result(self.machine.dispense_all(), "dispense_all")
            return False
        except _Stopped:
            raise
        except Exception as exc:                 # noqa: BLE001 - vimbus raises plain Exception
            self.resyncs += 1
            self.message = (f"Antwoord op de dispense was onleesbaar ({exc}); "
                            f"niet herhaald, de machine kan al bezig zijn")
            log.warning("Antwoord op dispense_all onleesbaar, NIET herhaald: %s", exc)
            return True

    def _wait_until_idle(self, unit: str) -> None:
        """
        Wait until the machine reports IDLE again, rather than guessing how
        long a dispense of this size takes.

        get_status() takes no arguments and describes the machine as a whole;
        unit is only used to say in the log which dispense was being waited
        on.

        Two things make this more than a single check. The machine needs a
        moment to start, so an IDLE answer straight after the command means it
        has not begun yet: only once DISPENSE_START_GRACE has passed without
        ever seeing it busy is the dispense taken to be finished. And a
        garbled reply is not fatal here, since asking for a status changes
        nothing: the connection is resynced and the next poll tries again.
        """
        started = time.monotonic()
        seen_busy = False
        status = "?"

        while True:
            self._check_stop()

            try:
                status = str(self.machine.get_status().vars["status"]).strip().upper()
            except _Stopped:
                raise
            except Exception as exc:             # noqa: BLE001 - vimbus raises plain Exception
                # Unreadable answer; resync and ask again on the next poll.
                self.resyncs += 1
                log.warning("Onleesbaar antwoord op get_status(), opnieuw verbinden: %s", exc)
                self._resync()
                status = "?"
            else:
                if status == STATUS_IDLE:
                    if seen_busy:
                        log.info("%s is klaar na %.1f s", unit, time.monotonic() - started)
                        return
                    if time.monotonic() - started >= DISPENSE_START_GRACE:
                        # Never seen busy. Either the dispense was over before
                        # the first poll, or the machine takes longer than the
                        # grace period to report it. Worth saying out loud:
                        # in the second case the test moves on too early.
                        log.warning("Machine meldde nooit DISPENSING binnen %.1f s; "
                                    "dispense van %s als klaar beschouwd",
                                    DISPENSE_START_GRACE, unit)
                        return
                elif not seen_busy:
                    seen_busy = True
                    log.info("%s is begonnen (machinestatus %s)", unit, status)

            if time.monotonic() - started > DISPENSE_TIMEOUT:
                raise TimeoutError(
                    f"De machine staat na {DISPENSE_TIMEOUT:.0f} s nog niet op "
                    f"{STATUS_IDLE} (laatste status: {status})"
                )

            self.message = f"Wachten op {unit}... ({time.monotonic() - started:.0f} s)"
            self._sleep(STATUS_POLL_INTERVAL)

    def _resync(self) -> None:
        """
        Close and reopen the serial port to the machine, so a half-read frame
        is discarded and the next command starts on a clean line.

        Failures are swallowed: if reopening does not work either, the next
        attempt reports the problem, and after DISPENSER_RETRIES tries the
        test ends with the machine's own message.
        """
        self._sleep(RESYNC_DELAY)
        try:
            self._close_machine()
            self._open_machine()
        except _Stopped:
            raise
        except Exception as exc:                 # noqa: BLE001 - next attempt reports it
            log.warning("Opnieuw verbinden met de machine mislukte: %s", exc)

    # -- helpers ----------------------------------------------------
    def _power_on(self, relay) -> None:
        """
        Switch the power on and connect to the machine.

        The control board loses power together with the units, so after every
        power cycle the serial port is opened again from scratch.
        """
        with self._relay_lock:
            relay.turn_on()
        self._sleep(POWER_ON_DELAY)
        self._close_machine()
        self._open_machine()

    def _settle(self) -> None:
        """
        Short pause before the power is cut, giving a unit a moment to come
        to rest: pulling the mains on a busy machine is what leaves it in a
        glitched state.

        Uses time.sleep() rather than the loop's own wait helper on purpose.
        That one raises as soon as Stop has been pressed, which would skip
        exactly the wait that matters here.

        Note that this is a fixed pause. A run that finishes normally has
        already waited for the unit to report IDLE, so nothing is running by
        then; pressing Stop halfway through a dispense does cut the power
        while the unit is busy. Waiting for it would mean calling
        _wait_until_idle() here, at the cost of a Stop that takes as long as
        the dispense still needs.
        """
        self.message = "Spanning gaat eraf..."
        time.sleep(POWER_OFF_DELAY)

    def _power_off_now(self) -> bool:
        """
        Take the relay lock and switch the power off.

        Called by the test thread when it shuts down or power cycles, and by
        stop() from the GUI thread. Returns False when the lock could not be
        taken within RELAY_LOCK_TIMEOUT, which means the other thread is busy
        with the relay and will finish its own shutdown shortly.
        """
        if self.relay is None:
            return True
        if not self._relay_lock.acquire(timeout=RELAY_LOCK_TIMEOUT):
            return False
        try:
            self._power_off(self.relay)
        finally:
            self._relay_lock.release()
        return True

    @staticmethod
    def _power_off(relay) -> None:
        """
        Switch the power off, retrying a few times. Call through
        _power_off_now() so the relay lock is held.

        Never raises: this also runs while handling an error, and a second
        failure here would hide the original one. It does try more than once,
        because silently giving up leaves the machine powered, which is worse
        than a slow shutdown.
        """
        for attempt in range(POWER_OFF_ATTEMPTS):
            try:
                if not relay.is_connected:
                    return
                relay.turn_off()
                return
            except Exception:                    # noqa: BLE001 - shutting down wins
                time.sleep(0.2)

    def _check_stop(self) -> None:
        """Raise _Stopped when stop() has been called."""
        if self._stop:
            raise _Stopped

    def _sleep(self, seconds: float) -> None:
        """
        Wait, but in steps of at most 0.1 seconds while checking for a stop
        request. A plain time.sleep(60) would make the Stop button appear
        dead for up to a minute.
        """
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self._check_stop()
            time.sleep(min(0.1, deadline - time.monotonic()))


if __name__ == "__main__":
    # Run without the GUI: python -m duurtest.testDriver
    # These settings stand in for what is normally filled in in the window.
    from .logsetup import setup_logging

    setup_logging()
    test = DuurTest(TestSettings(
        port="COM3",              # relay
        baudrate=115200,
        machine_port="COM6",      # control board
        units=["CX01", "MH01", "YH04"],
        dispense_number=10,
        power_cycle_interval=5,
        random_dispense=True,
        dispense_min=1,
        dispense_max=10,
    ))
    test.start()
    # Same status attributes the GUI polls, printed once a second.
    while test.running:
        print(f"{test.percentage:3d}%  {test.message}")
        time.sleep(1)
    print(test.message)
