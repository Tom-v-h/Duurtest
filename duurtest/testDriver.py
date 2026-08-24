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
from serial.tools import list_ports

# Every setting lives in config.py; imported by name so the code below reads
# the same as before.
from .config import (
    CHECK_DISPENSED_AMOUNT,
    DISPENSE_ALL_TIMEOUT,
    DISPENSE_STEP_ML,
    DISPENSE_TOLERANCE_ML,
    FILL_LEVEL_NL,
    IDLE_TIMEOUT,
    MACHINE_ADDRESS,
    MACHINE_BAUDRATE,
    MACHINE_CONNECT_RETRY_INTERVAL,
    MACHINE_CONNECT_TIMEOUT,
    MACHINE_ENCRYPTION,
    MACHINE_REPLY_TIMEOUT,
    MACHINE_RESYNC_DELAY,
    MACHINE_RETRY_COUNT,
    MACHINE_STATUS_IDLE,
    MIN_DISPENSE_ML,
    NL_PER_ML,
    POWER_CYCLE_ATTEMPTS,
    POWER_OFF_ATTEMPTS,
    POWER_OFF_DELAY,
    POWER_ON_DELAY,
    READ_SOLENOID_TEMPERATURE,
    RELAY_LOCK_TIMEOUT,
    RETRY_POWER_CYCLE,
    STATUS_POLL_INTERVAL,
    UNIT_ADDRESSES,
)
from .control_board import ControlBoard, ResultCode
from .logsetup import start_run_log, stop_run_log
from .relay import RelayController, RelayControllerConfig

log = logging.getLogger(__name__)                       # the test itself
machine_log = logging.getLogger("duurtest.machine")     # traffic to the control board



def _visible_ports() -> str:
    """The com ports the PC can see right now, for a failure message."""
    try:
        return ", ".join(p.device for p in list_ports.comports()) or "geen"
    except Exception as exc:                     # noqa: BLE001 - only used to explain
        return f"onbekend ({exc})"


def set_machine_power(port: str, baudrate: int, on: bool) -> None:
    """
    Switch the machine's power on or off outside a test.

    This exists for the button in the window. An older control board only
    shows up as a com port while it has power, so you cannot pick its port
    before switching the machine on; a newer one stays visible either way.

    The relay is opened, switched and closed again, so the port is free by
    the time a test starts and claims it. Do not call this while a test is
    running: the test thread owns the relay then.
    """
    config = RelayControllerConfig()
    config.port = port
    config.baudrate = baudrate

    relay = RelayController(config)
    log.info("Machine %s zetten via de relay op %s", "aan" if on else "uit", port)
    relay.connect()
    try:
        relay.turn_on() if on else relay.turn_off()
    finally:
        relay.disconnect()


@dataclass
class TestSettings:
    """
    Everything the operator fills in in the GUI. gui.read_settings() builds
    one of these; running without a GUI means filling it in by hand.
    """

    port: str = ""                     # COM port of the relay/STM32
    baudrate: int = 115200
    machine_port: str = ""             # COM port of the machine's control board
    units: list[str] = field(default_factory=list)   # names from UNIT_ADDRESSES
    dispense_number: int = 0           # total number of dispenses in the test
    power_cycle_interval: int = 0      # power cycle every N dispenses, 0 = off
    random_dispense: bool = False      # False = fixed amount, True = random
    dispense_amount: float = 0.0       # ml, used when random_dispense is False
    dispense_min: float = 0.0          # ml, used when random_dispense is True
    dispense_max: float = 0.0
    max_units: int = 1                 # up to this many units dispense together

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
        # Catches a checkbox whose object name does not appear in UNIT_ADDRESSES,
        # which would otherwise only fail halfway through the test.
        unknown = [u for u in self.units if u not in UNIT_ADDRESSES]
        if unknown:
            return f"Onbekende unit(s): {', '.join(unknown)}"
        if self.dispense_number <= 0:
            return "Aantal dispenses moet groter zijn dan 0."
        if self.max_units < 1:
            return "Max units per dispense moet minimaal 1 zijn."
        if self.random_dispense:
            if self.dispense_min < MIN_DISPENSE_ML:
                return f"Min dispense moet minimaal {MIN_DISPENSE_ML} ml zijn."
            if self.dispense_min > self.dispense_max:
                return "Min dispense mag niet groter zijn dan max."
        elif self.dispense_amount < MIN_DISPENSE_ML:
            return f"Dispense hoeveelheid moet minimaal {MIN_DISPENSE_ML} ml zijn."
        return None

    def next_amount(self) -> float:
        """
        Amount in ml for one unit: the fixed value, or one drawn at random
        between min and max.

        The draw is in whole steps of DISPENSE_STEP_ML rather than anywhere in
        between, so an amount from the log is one you could also have typed
        into the window yourself.
        """
        if not self.random_dispense:
            return self.dispense_amount
        low = round(self.dispense_min / DISPENSE_STEP_ML)
        high = round(self.dispense_max / DISPENSE_STEP_ML)
        return round(random.randint(low, high) * DISPENSE_STEP_ML, 1)

    def next_units(self) -> list[str]:
        """
        The units for the next round: between one and max_units of them, drawn
        at random from the ones ticked in the window. So max_units = 1 always
        gives a single unit, and 6 gives anywhere from one to six.

        Never more than are ticked, and never the same unit twice in a round:
        a unit queued twice would dispense twice.
        """
        ceiling = min(self.max_units, len(self.units))
        return random.sample(self.units, random.randint(1, ceiling))


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
        self.power_retries = 0              # extra power cycles to get the board up
        self.dispense_deviations = 0        # rounds where a unit dispensed the wrong amount
        self.temperature_failures = 0       # solenoid readings that could not be taken
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
                 f"{s.dispense_min:.1f}-{s.dispense_max:.1f} ml random per unit"
                 if s.random_dispense else f"{s.dispense_amount:.1f} ml vast")
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
        if self.power_retries:
            parts.append(f"{self.power_retries}x machine opnieuw opgestart")
        if self.dispense_deviations:
            parts.append(f"{self.dispense_deviations}x afwijkende hoeveelheid")
        if self.temperature_failures:
            parts.append(f"{self.temperature_failures}x temperatuur niet gelezen")
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

                # Every unit in the round draws its own amount, so in random
                # mode they do not all dispense the same thing.
                portions = [(unit, settings.next_amount())
                            for unit in settings.next_units()]
                namen = ", ".join(f"{unit} {ml:.1f} ml" for unit, ml in portions)
                self.message = f"Dispense {done}/{total}: {namen}"
                log.info("--- Dispense %d/%d: %s ---", done, total, namen)

                levels_before = self._prepare_dispense(portions)
                garbled = self._start_dispense()
                if garbled:
                    # The reply was unreadable, so the connection is out of
                    # step. Resync before asking the machine anything else.
                    self._resync()

                self._wait_until_idle(namen)
                self._check_dispensed(portions, levels_before)

                self.percentage = int(done / total * 100)

                # Power cycle in between, but not after the last dispense:
                # the finally block switches the power off anyway.
                if interval and done % interval == 0 and done < total:
                    self.message = f"Power cycle na {done} dispenses"
                    log.info("Power cycle na %d dispenses", done)
                    # Eerst loslaten, dan pas schakelen: zie _close_machine().
                    self._close_machine()
                    self._power_off_now()
                    self._sleep(POWER_OFF_DELAY)
                    self._power_on(self.relay)
        finally:
            # Runs on a normal finish, on stop and on an error, so the units
            # are never left powered. stop() may have switched off already;
            # sending OFF twice does no harm.
            self._settle()
            self._close_machine()
            self._power_off_now()
            with self._relay_lock:
                self.relay.disconnect()

    # -- talking to the machine -------------------------------------
    def _open_machine(self) -> None:
        """
        Open the serial port to the control board and greet it with a poll,
        retrying until that works.

        Straight after a power cycle the board is often not there yet: an older
        one drops off the com port list entirely while it has no power, and
        Windows needs a moment to enumerate it again. So instead of failing on
        the first attempt, this keeps trying every
        MACHINE_CONNECT_RETRY_INTERVAL seconds and carries on as soon as the
        board answers. Only after MACHINE_CONNECT_TIMEOUT does it give up and
        end the test.

        The poll is part of what is retried: the port can come back before the
        board is ready to answer, and a port that opens but says nothing is no
        use either.

        The port carries a timeout, unlike the example in temp.py: without one
        a board that stops answering would leave readline() waiting for as
        long as the application runs.
        """
        port = self.settings.machine_port
        log.info("Machine: poort %s openen op %d baud, adres 0x%04X",
                 port, MACHINE_BAUDRATE, MACHINE_ADDRESS)

        started = time.monotonic()
        deadline = started + MACHINE_CONNECT_TIMEOUT
        attempt = 0

        while True:
            attempt += 1
            self._check_stop()
            self.message = f"Verbinden met de machine op {port}..."
            try:
                self._machine_serial = serial.Serial(port, baudrate=MACHINE_BAUDRATE,
                                                     timeout=MACHINE_REPLY_TIMEOUT)
                self.machine = _LoggedBoard(ControlBoard(MACHINE_ADDRESS, self._machine_serial,
                                                         machine_log, MACHINE_ENCRYPTION))
                self.machine.poll()
            except _Stopped:
                raise
            except Exception as exc:             # noqa: BLE001 - keep trying, then report
                # Drop whatever half-open state there is, so the next attempt
                # starts from nothing.
                self._close_machine()

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ConnectionError(
                        f"Geen verbinding met de machine op {port} na "
                        f"{MACHINE_CONNECT_TIMEOUT:.0f} s ({attempt} pogingen). "
                        f"Zichtbare poorten: {_visible_ports()}. Laatste fout: {exc}"
                    ) from exc

                # The first failure is worth a line; the ones after it would
                # only repeat themselves, so those go to debug level. The list
                # of ports says which of two things went wrong: the board is
                # not back yet, or it is back under a different name.
                if attempt == 1:
                    log.info("Machine op %s nog niet beschikbaar, blijven proberen "
                             "(maximaal %.0f s). Zichtbare poorten: %s. Fout: %s",
                             port, MACHINE_CONNECT_TIMEOUT, _visible_ports(), exc)
                else:
                    log.debug("Poging %d om %s te openen mislukte: %s", attempt, port, exc)

                self.message = f"Wachten op {port}... (nog {remaining:.0f} s)"
                self._sleep(MACHINE_CONNECT_RETRY_INTERVAL)
            else:
                if attempt > 1:
                    log.info("Machine op %s verbonden na %d pogingen (%.1f s)",
                             port, attempt, time.monotonic() - started)
                return

    def _close_machine(self) -> None:
        """
        Close the serial port to the control board. Never raises.

        Call this before switching the power off, never after. Cutting the
        mains while the port is still open makes the USB device disappear from
        under an open handle, and Windows then keeps the port name reserved for
        an instance that no longer exists. The board comes back but cannot have
        its old name, so opening it fails with "cannot find the file" until the
        handle is finally let go. Closing first avoids that race entirely.

        A close that fails is logged rather than swallowed: that is exactly the
        case that leaves a handle behind, and it should not be invisible.
        """
        try:
            if self._machine_serial is not None and self._machine_serial.is_open:
                self._machine_serial.close()
                log.info("Machine: poort gesloten")
        except Exception as exc:                 # noqa: BLE001 - shutting down wins
            log.warning("Machine: poort sluiten mislukte, handle kan blijven hangen: %s", exc)
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

    def _prepare_dispense(self, portions: list[tuple[str, float]]) -> dict[str, int]:
        """
        Correct the fill level of every unit in this round and queue them all
        with their own amount. dispense_all() then sets them off together.

        Returns the fill level each unit starts from, in nanolitres, so
        _check_dispensed() can see afterwards how much really came out.

        A failure here is nearly always a garbled reply frame, such as

            Expected encrypted string '0071;0A08;416A00265D\\n'
            to contain 4 parts, got 3

        which means the serial connection is out of step: bytes were lost or
        a leftover fragment was read as if it were a new frame. Reopening the
        port starts a fresh frame and the same commands then succeed, so the
        test survives it instead of ending on the second dispense.

        A round is queued again from the first unit, not continued halfway.
        Since part of it may already be queued, and a unit queued twice would
        dispense twice, the queue is cleared first.
        """
        self._check_result(
            self.machine.dispense_cancel(),
            f"dispense_cancel()")

        for attempt in range(1, MACHINE_RETRY_COUNT + 1):
            try:
                levels: dict[str, int] = {}
                for unit, amount_ml in portions:

                    self._read_solenoid_temperature(unit)
                    self._check_result(
                        self.machine.get_fill_level(unit),
                        f"get_fill_level({unit})")
                    self._check_result(
                        self.machine.correct_fill_level(unit, UNIT_ADDRESSES[unit], FILL_LEVEL_NL),
                        f"correct_fill_level({unit})")
                    # This second reading is the level the unit starts the
                    # dispense from, so it is kept for the check afterwards.
                    reading = self.machine.get_fill_level(unit)
                    self._check_result(reading, f"get_fill_level({unit})")
                    levels[unit] = reading.vars.get("fill_level")
                    # dispense_nl takes whole nanolitres, the window millilitres.
                    self._check_result(
                        self.machine.dispense_nl(unit, round(amount_ml * NL_PER_ML)),
                        f"dispense_nl({unit}, {amount_ml:.1f} ml)")
                return levels
            except _Stopped:
                raise
            except Exception as exc:             # noqa: BLE001 - vimbus raises plain Exception
                # Out of attempts: let it end the test, with the machine's own
                # message so it is clear where it came from.
                if attempt == MACHINE_RETRY_COUNT:
                    raise
                self.resyncs += 1
                self.message = (f"Machine antwoordde onleesbaar, poging "
                                f"{attempt} van {MACHINE_RETRY_COUNT}: {exc}")
                log.warning("Onleesbaar antwoord (poging %d/%d), opnieuw verbinden: %s",
                            attempt, MACHINE_RETRY_COUNT, exc)
                self._resync()
                self._cancel_queue()

    def _read_solenoid_temperature(self, unit: str) -> None:
        """
        Read the solenoid temperature of a unit and put it in the log.

        Never lets a round fail over it. It is a measurement, not a step of
        the dispense, and it sits inside the retry path: a failure here used
        to close and reopen the port, clear the queue, retry the whole round
        three times and then end the test. One unreadable reading therefore
        produced a burst of errors and could kill a night's run.

        Two things make it fail. The machine answers with a NACK when its
        firmware does not know the command, which comes back as "Wrong code in
        command string, expected 2981, got 2"; and get_solenoid_temperature()
        raises by itself on a reading of zero. Neither says anything about the
        dispense that follows.
        """
        if not READ_SOLENOID_TEMPERATURE:
            return

        try:
            celsius = self.machine.get_solenoid_temperature(UNIT_ADDRESSES[unit])
        except _Stopped:
            raise
        except Exception as exc:                 # noqa: BLE001 - only a measurement
            self.temperature_failures += 1
            log.warning("Solenoïdetemperatuur van %s niet te lezen: %s", unit, exc)
            return

        log.info("%s: solenoïde %.2f °C", unit, celsius)

    def _check_dispensed(self, portions: list[tuple[str, float]],
                         levels_before: dict[str, int]) -> None:
        """
        Read the fill level once more, now that the machine is idle, and
        compare the drop with the amount that was asked for.

        Note which two readings this uses. The pair taken in
        _prepare_dispense() both sit before the dispense, around
        correct_fill_level, so their difference says how far the level was off
        before it was corrected, not how much came out. What was dispensed is
        the level after the correction minus the level once the unit has
        finished, which is what this measures.

        A unit that is off by more than DISPENSE_TOLERANCE_ML is logged as a
        warning and counted, but never ends the run: over a long test the
        pattern in the log says more than any single round. Reading a level is
        harmless, so a failure to read one only costs this one check.
        """
        if not CHECK_DISPENSED_AMOUNT:
            return

        for unit, amount_ml in portions:
            start = levels_before.get(unit)
            if start is None:
                continue

            try:
                reading = self.machine.get_fill_level(unit)
            except _Stopped:
                raise
            except Exception as exc:             # noqa: BLE001 - only a check
                log.warning("Vulniveau van %s na de dispense niet te lezen: %s", unit, exc)
                continue

            self._check_result(reading, f"get_fill_level({unit})")
            end = reading.vars.get("fill_level")
            if end is None:
                continue

            dispensed_ml = (start - end) / NL_PER_ML
            difference_ml = dispensed_ml - amount_ml

            if abs(difference_ml) <= DISPENSE_TOLERANCE_ML:
                log.info("%s: gevraagd %.2f ml, gedispenst %.2f ml (verschil %+.2f ml)",
                         unit, amount_ml, dispensed_ml, difference_ml)
            else:
                self.dispense_deviations += 1
                log.warning("%s: gevraagd %.2f ml, gedispenst %.2f ml "
                            "(verschil %+.2f ml, buiten de tolerantie van %.2f ml)",
                            unit, amount_ml, dispensed_ml, difference_ml,
                            DISPENSE_TOLERANCE_ML)

    def _cancel_queue(self) -> None:
        """
        Clear whatever is queued for dispensing. Best effort: on a connection
        that is already unhappy this may fail too, and the retry after it will
        report that.
        """
        try:
            self._check_result(self.machine.dispense_cancel(), "dispense_cancel")
        except _Stopped:
            raise
        except Exception as exc:                 # noqa: BLE001 - retry reports it
            log.warning("Wachtrij leegmaken mislukte: %s", exc)

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
            with self.machine.board.override_timeout(DISPENSE_ALL_TIMEOUT):
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

        The first IDLE ends the wait. There is no grace period for the
        machine to get going: dispense_all() only returns after it has spent
        two seconds collecting the machine's log messages, so by the time the
        first status is asked the machine has long since started. Whether it
        was ever seen busy is still worth a word in the log, since a dispense
        that was already over says something about how short it was.

        A garbled reply is not fatal here, since asking for a status changes
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
                if status == MACHINE_STATUS_IDLE:
                    log.info("%s is klaar na %.1f s%s", unit, time.monotonic() - started,
                             "" if seen_busy else " (was al klaar bij de eerste controle)")
                    return
                elif not seen_busy:
                    seen_busy = True
                    log.info("%s is begonnen (machinestatus %s)", unit, status)

            if time.monotonic() - started > IDLE_TIMEOUT:
                raise TimeoutError(
                    f"De machine staat na {IDLE_TIMEOUT:.0f} s nog niet op "
                    f"{MACHINE_STATUS_IDLE} (laatste status: {status})"
                )

            self.message = f"Wachten op {unit}... ({time.monotonic() - started:.0f} s)"
            self._sleep(STATUS_POLL_INTERVAL)

    def _resync(self) -> None:
        """
        Close and reopen the serial port to the machine, so a half-read frame
        is discarded and the next command starts on a clean line.

        Failures are swallowed: if reopening does not work either, the next
        attempt reports the problem, and after MACHINE_RETRY_COUNT tries the
        test ends with the machine's own message.
        """
        self._sleep(MACHINE_RESYNC_DELAY)
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
        Switch the power on and connect to the machine, switching it off and
        on again if the board does not come up.

        The control board loses power together with the units, so after every
        power cycle the serial port is opened again from scratch. Now and then
        the board does not appear at all: it never shows up as a com port and
        never will, until it is made properly powerless once more. Rather than
        ending a run over it, this does what the operator would do and cycles
        the power again, up to POWER_CYCLE_ATTEMPTS times.

        That is a workaround, not a cure. RETRY_POWER_CYCLE turns it off, and
        a run then ends at the first failure.
        """
        attempts = POWER_CYCLE_ATTEMPTS if RETRY_POWER_CYCLE else 1

        for attempt in range(1, attempts + 1):
            with self._relay_lock:
                relay.turn_on()
            self._sleep(POWER_ON_DELAY)
            self._close_machine()

            try:
                self._open_machine()
            except _Stopped:
                raise
            except ConnectionError as exc:
                # Out of tries: let the message from _open_machine(), which
                # names the ports that were visible, end the test.
                if attempt == attempts:
                    raise

                self.power_retries += 1
                log.warning("Machine kwam niet op na inschakelen (%s); "
                            "machine opnieuw uit- en aanzetten, poging %d van %d",
                            exc, attempt + 1, attempts)
                self.message = (f"Machine kwam niet op; opnieuw opstarten "
                                f"({attempt + 1}/{attempts})")

                # Port first, then the power: see _close_machine().
                self._close_machine()
                self._power_off_now()
                self._sleep(POWER_OFF_DELAY)
            else:
                if attempt > 1:
                    log.info("Machine kwam op na %d keer opnieuw opstarten", attempt - 1)
                return

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
        dispense_min=0.8,
        dispense_max=10.0,
        max_units=3,
    ))
    test.start()
    # Same status attributes the GUI polls, printed once a second.
    while test.running:
        print(f"{test.percentage:3d}%  {test.message}")
        time.sleep(1)
    print(test.message)
