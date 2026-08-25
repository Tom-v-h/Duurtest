"""
Switch the relay on and off a number of times, and nothing else.

For checking the relay and the machine's behaviour around power on its own,
without a dispense in sight: does the control board come back every time, does
the relay answer every command, does anything get warm.

    python power_test.py

A small window shows what it is doing and has two buttons: one stops the
cycling, the other switches the relay by hand. Everything else is set with the
constants below. Closing the window leaves the relay switched off.
"""

import sys
import time

from PySide6 import QtCore, QtWidgets

from duurtest.relay import RelayController, RelayControllerConfig

# ----------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------
RELAY_PORT = "COM3"         # com port of the relay/STM32
RELAY_BAUDRATE = 115200

CYCLES = 50                 # how many times to switch off and on again

# The interval is split in two, because the two halves do different work: the
# off time is what lets the board discharge, the on time is what lets it boot.
# For an evenly spaced interval, give them the same value.
ON_SECONDS = 10.0
OFF_SECONDS = 15.0


def stamp() -> str:
    """Time of day, so a line can be matched with what the machine did."""
    return time.strftime("%H:%M:%S")


class PowerTest(QtWidgets.QWidget):
    """
    Window with a line of text and two buttons.

    The cycling runs on a timer rather than a thread: switching is the only
    work there is, so there is nothing to keep a thread busy with, and this
    way the relay only ever has one user.
    """

    def __init__(self, relay: RelayController):
        super().__init__()
        self.relay = relay
        self.relay_on = False
        self.cycle = 0
        self.cycling = False

        self.setWindowTitle("Power test")
        self.status = QtWidgets.QLabel()
        self.stop_button = QtWidgets.QPushButton("Stop")
        self.manual_button = QtWidgets.QPushButton("Handmatig aan")

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.status)
        layout.addWidget(self.stop_button)
        layout.addWidget(self.manual_button)

        self.stop_button.clicked.connect(self.stop_cycling)
        self.manual_button.clicked.connect(self.switch_by_hand)

        # Single shot: every step schedules the one after it, so the interval
        # can differ between switching on and switching off.
        self.timer = QtCore.QTimer(self, singleShot=True, timeout=self.step)

    # -- switching --------------------------------------------------
    def switch(self, on: bool) -> None:
        """Switch the relay and report what it answered."""
        try:
            answer = self.relay.turn_on() if on else self.relay.turn_off()
        except Exception as exc:                 # noqa: BLE001 - report and stop
            self.stop_cycling()
            self.show_status(f"Schakelen mislukte: {exc}")
            return

        self.relay_on = on
        print(f"{stamp()}  {'ON ' if on else 'OFF'}  ->  {answer or '<geen antwoord>'}")
        self.manual_button.setText("Handmatig uit" if on else "Handmatig aan")

    # -- the cycle --------------------------------------------------
    def start_cycling(self) -> None:
        self.cycle = 0
        self.cycling = True
        self.step()

    def step(self) -> None:
        """One transition, then schedule the next."""
        if not self.cycling:
            return

        if self.relay_on:
            self.switch(False)
            if self.cycle >= CYCLES:
                self.cycling = False
                self.show_status(f"Klaar: {CYCLES} cycles gedaan, relay uit")
                return
            self.show_status(f"Cycle {self.cycle}/{CYCLES}: uit")
            self.timer.start(int(OFF_SECONDS * 1000))
        else:
            self.cycle += 1
            self.switch(True)
            self.show_status(f"Cycle {self.cycle}/{CYCLES}: aan")
            self.timer.start(int(ON_SECONDS * 1000))

    def stop_cycling(self) -> None:
        """Stop the cycling and leave the relay off."""
        was_running = self.cycling
        self.cycling = False
        self.timer.stop()
        if self.relay_on:
            self.switch(False)
        if was_running:
            self.show_status(f"Gestopt na {self.cycle} cycles, relay uit")

    # -- by hand ----------------------------------------------------
    def switch_by_hand(self) -> None:
        """
        Switch the relay by hand. Stops the cycling first, so there is never a
        timer switching it back a moment later.
        """
        if self.cycling:
            self.stop_cycling()
        self.switch(not self.relay_on)
        self.show_status("Handmatig: relay " + ("aan" if self.relay_on else "uit"))

    # -- the rest ---------------------------------------------------
    def show_status(self, text: str) -> None:
        self.status.setText(f"{stamp()}  {text}")
        print(f"{stamp()}  {text}")

    def closeEvent(self, event) -> None:
        """Never walk away leaving the machine powered."""
        self.cycling = False
        self.timer.stop()
        if self.relay_on:
            self.switch(False)
        super().closeEvent(event)


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)

    config = RelayControllerConfig()
    config.port = RELAY_PORT
    config.baudrate = RELAY_BAUDRATE
    relay = RelayController(config)

    print(f"{stamp()}  Relay op {RELAY_PORT} @ {RELAY_BAUDRATE} baud, "
          f"{CYCLES} cycles van {ON_SECONDS:.0f} s aan en {OFF_SECONDS:.0f} s uit")
    try:
        relay.connect()
    except Exception as exc:                     # noqa: BLE001 - nothing to run without
        QtWidgets.QMessageBox.critical(None, "Power test", str(exc))
        return 1

    window = PowerTest(relay)
    window.show()
    window.start_cycling()
    try:
        return app.exec()
    finally:
        relay.disconnect()
        print(f"{stamp()}  Klaar, poort gesloten")


if __name__ == "__main__":
    sys.exit(main())
