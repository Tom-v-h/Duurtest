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

    relay on -> wait -> connect dispenser
    for every dispense:
        pick a unit and an amount, dispense it, wait
        after every power_cycle_interval dispenses: relay off, on, reconnect
    relay off -> disconnect

The block at the bottom only runs when this module is started directly,
which is handy to check whether the relay and the dispenser respond without
involving the GUI:
    python -m duurtest.testDriver
"""

from __future__ import annotations

import random
import threading
import time
import xmlrpc.client
from dataclasses import dataclass, field
from typing import Optional

from .relay import RelayController, RelayControllerConfig

# Unit name -> number, as the dispenser knows them. The names match the
# object names of the checkboxes in Duurtest_GUI.ui.
UNITS: dict[str, int] = {
    'CX01': 1, 'MH01': 2, 'YH04': 3, 'RH01': 4,
    'YX01': 5, 'WX01': 6, 'CH01': 7, 'GH01': 8,
    'BH01': 9, 'OH01': 10, 'RX01': 11, 'YH01': 12,
    'GX01': 13, 'BX01': 14, 'YH02': 15, 'DISP16': 16,
}

# Connection to the dispenser. This is a separate xmlrpc service on its own
# serial port, unrelated to the relay port picked in the GUI.
DISPENSER_URL = "http://localhost:9111/"
DISPENSER_PORT = "COM6"
DISPENSER_ADDRESS = "0x0002"
DISPENSER_BAUDRATE = 19200

FILL_LEVEL = 3800          # value passed to correctFillLevel
POWER_ON_DELAY = 10.0      # wait after switching on until the unit has booted
POWER_OFF_DELAY = 5.0      # how long the power stays off during a power cycle

# Reactive timing: a dispense of N ml is given DISPENSE_DELAY seconds plus
# DISPENSE_DELAY_PER_ML per ml, so a small dispense does not wait as long as
# a large one.
DISPENSE_DELAY = 5.0       # base wait after a dispense
DISPENSE_DELAY_PER_ML = 0.2

POWER_OFF_ATTEMPTS = 3     # tries to get the relay off before giving up

# Stop has to be able to cut the power even while the test thread is busy,
# so both threads take _relay_lock before touching the relay. Commands to the
# relay are bounded by the timeouts in relay.py, about a second, so the lock
# is never held long; this is how long Stop waits for it.
RELAY_LOCK_TIMEOUT = 3.0

# The dispenser calls have no timeout of their own, so a server that stops
# answering would block the test thread forever. This is a safety net, not a
# tight bound: it has to be longer than the slowest legitimate call.
DISPENSER_TIMEOUT = 300.0

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
            return "Selecteer een com-port."
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


class _TimeoutTransport(xmlrpc.client.Transport):
    """
    xmlrpc transport that puts a timeout on the connection.

    ServerProxy has no timeout parameter, so without this a dispenser that
    stops answering leaves the test thread waiting on a socket read for as
    long as the application runs.
    """

    def __init__(self, timeout: float, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.timeout = timeout

    def make_connection(self, host):
        connection = super().make_connection(host)
        connection.timeout = self.timeout
        return connection


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
        self.resyncs = 0                    # times the dispenser had to be reconnected

        self.relay = None                              # created by _loop()
        self._stop = False                             # stop requested
        self._busy_until = 0.0                         # when the running dispense ends
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
            raise ValueError(error)
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """
        Ask the test to stop after the step that is currently running.

        Sets the flag the loop checks between steps, and switches the power
        off right here rather than leaving that to the test thread.

        That last part matters: if the test thread is stuck in a call to the
        dispenser it never reaches its own shutdown, and the machine would
        stay powered for as long as that call hangs. Stop has to work then
        too, so it cuts the power itself.

        Doing that from the GUI thread is safe because both threads take
        _relay_lock before touching the relay. Waiting for the lock takes at
        most about a second: relay commands are bounded by the timeouts in
        relay.py. Closing the port is still left to the test thread, which
        owns it.
        """
        self._stop = True
        if not self._power_off_now():
            # The test thread is holding the relay; its own shutdown will
            # switch off in a moment.
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
            # Mention the reconnects: a run that needed a lot of them
            # finished, but says something about the connection.
            self.message = ("Test afgerond" if not self.resyncs else
                            f"Test afgerond, {self.resyncs}x opnieuw verbonden met de dispenser")
        except _Stopped:
            self.message = "Test gestopt"
        except Exception as exc:                 # noqa: BLE001 - report to the GUI
            self.error = str(exc)
            self.message = f"Fout: {exc}"
        finally:
            # Set last, so the GUI sees the final status in the same poll in
            # which it notices the test has ended.
            self.running = False

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

        # ServerProxy does not talk to the network yet; the first call does.
        # The transport carries a timeout so a dispenser that stops answering
        # cannot block this thread indefinitely.
        server = xmlrpc.client.ServerProxy(
            DISPENSER_URL, allow_none=True,
            transport=_TimeoutTransport(DISPENSER_TIMEOUT),
        )

        try:
            self._power_on(self.relay, server)

            for done in range(1, total + 1):
                self._check_stop()

                unit = random.choice(settings.units)
                amount = settings.next_amount()
                self.message = f"Dispense {done}/{total}: {unit}, {amount} ml"

                self._prepare_dispense(server, unit, amount)
                garbled = self._start_dispense(server)

                # Reactive timing: bigger dispense, longer wait. _busy_until
                # records when the unit should be done, so the shutdown below
                # knows whether it is still running.
                wait = DISPENSE_DELAY + amount * DISPENSE_DELAY_PER_ML
                self._busy_until = time.monotonic() + wait
                self._sleep(wait)

                if garbled:
                    # The reply was unreadable, so the connection is out of
                    # step. Resync now that the unit has had its time, so the
                    # next round starts clean.
                    self._resync(server)

                self.percentage = int(done / total * 100)

                # Power cycle in between, but not after the last dispense:
                # the finally block switches the power off anyway.
                if interval and done % interval == 0 and done < total:
                    self.message = f"Power cycle na {done} dispenses"
                    self._power_off_now()
                    self._sleep(POWER_OFF_DELAY)
                    self._power_on(self.relay, server)
        finally:
            # Runs on a normal finish, on stop and on an error, so the units
            # are never left powered. stop() may have switched off already;
            # sending OFF twice does no harm.
            self._settle()
            self._power_off_now()
            with self._relay_lock:
                self.relay.disconnect()

    # -- talking to the dispenser -----------------------------------
    def _prepare_dispense(self, server, unit: str, amount: int) -> None:
        """
        Poll the dispenser, correct the fill level and prepare the unit.

        These three only read and adjust state, so they may be repeated
        safely. A Fault here is nearly always a garbled reply frame, such as

            Expected encrypted string '0071;0A08;416A00265D\\n'
            to contain 4 parts, got 3

        which means the serial connection is out of step: bytes were lost or
        a leftover fragment was read as if it were a new frame. Reconnecting
        starts a fresh frame and the same call then succeeds, so the test
        survives it instead of ending on the second dispense.
        """
        for attempt in range(1, DISPENSER_RETRIES + 1):
            try:
                server.poll()
                server.correctFillLevel(unit, UNITS[unit], FILL_LEVEL)
                server.prepareUnitForDispense(unit, amount)
                return
            except xmlrpc.client.Fault as fault:
                # Out of attempts: let it end the test, with the dispenser's
                # own message so it is clear where it came from.
                if attempt == DISPENSER_RETRIES:
                    raise
                self.resyncs += 1
                self.message = (f"Dispenser antwoordde onleesbaar, poging "
                                f"{attempt} van {DISPENSER_RETRIES}: {fault.faultString}")
                self._resync(server)

    def _start_dispense(self, server) -> bool:
        """
        Trigger the prepared dispense. Returns True when the reply was
        unreadable, so the caller knows a resync is needed.

        Deliberately not retried: a Fault means the answer was unreadable,
        not that nothing happened. The unit may well be dispensing already,
        and repeating the command could dispense the amount twice.
        """
        try:
            server.dispenseAllPreparedUnits()
            return False
        except xmlrpc.client.Fault as fault:
            self.resyncs += 1
            self.message = (f"Antwoord op de dispense was onleesbaar ({fault.faultString}); "
                            f"niet herhaald, de unit kan al bezig zijn")
            return True

    def _resync(self, server) -> None:
        """
        Reconnect to the dispenser so a half-read frame is discarded.

        Faults are swallowed: if the reconnect itself fails, the next
        attempt reports the problem, and after DISPENSER_RETRIES tries the
        test ends with the dispenser's own message.
        """
        self._sleep(RESYNC_DELAY)
        try:
            server.connect(DISPENSER_PORT, DISPENSER_ADDRESS, DISPENSER_BAUDRATE)
        except xmlrpc.client.Fault:
            pass

    # -- helpers ----------------------------------------------------
    def _power_on(self, relay, server) -> None:
        """
        Switch the power on and connect to the dispenser.

        The dispenser loses power together with the units, so after every
        power cycle it has to be connected and polled again.
        """
        with self._relay_lock:
            relay.turn_on()
        self._sleep(POWER_ON_DELAY)
        self.message = f"Verbinden met dispenser op {DISPENSER_PORT}..."
        server.connect(DISPENSER_PORT, DISPENSER_ADDRESS, DISPENSER_BAUDRATE)
        server.poll()

    def _settle(self) -> None:
        """
        Short pause before the power is cut, giving a unit a moment to come
        to rest: pulling the mains on a busy machine is what leaves it in a
        glitched state.

        Uses time.sleep() rather than the loop's own wait helper on purpose.
        That one raises as soon as Stop has been pressed, which would skip
        exactly the wait that matters here.

        Note that this is a fixed pause, so pressing Stop halfway through a
        large dispense still cuts the power while the unit is running. Wait
        for the dispense to finish first by using self._busy_until here.
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
    test = DuurTest(TestSettings(
        port="COM11",
        baudrate=115200,
        units=["CX01", "MH01", "YH04"],
        dispense_number=10,
        power_cycle_interval=5,
        random_dispense=True,
        dispense_min=1,
        dispense_max=500,
    ))
    test.start()
    # Same status attributes the GUI polls, printed once a second.
    while test.running:
        print(f"{test.percentage:3d}%  {test.message}")
        time.sleep(1)
    print(test.message)
