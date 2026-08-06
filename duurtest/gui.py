"""
GUI layer for the endurance test (PySide6).

This module is deliberately thin. It does three things and nothing else:

  1. Fill the dropdowns (baudrates, connected COM ports).
  2. Read the state of the widgets into a TestSettings object.
  3. Poll the running test and show its status in the progress bar.

The test itself lives in testDriver.py, which knows nothing about Qt. The
layers only depend downwards:

    Duurtest_GUI.ui  <->  gui.py  <->  testDriver.py  <->  relay.py

Note on language: comments and docstrings are English, but text the operator
sees (dialogs, status bar messages) is Dutch, matching the labels in the
.ui file.

The application is started through main.py in the directory above:
    python main.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6 import QtCore, QtWidgets
from PySide6.QtUiTools import QUiLoader
from serial.tools import list_ports

from .testDriver import DuurTest, TestSettings

# The Qt Designer file sits next to this module, so it is found no matter
# which directory the application is started from.
UI_FILE = Path(__file__).with_name("Duurtest_GUI.ui")

# Baudrates offered in the dropdown. These are the standard values; the
# relay firmware runs at 115200, which is why that one is preselected.
BAUDRATES = [
    300, 600, 1200, 2400, 4800, 9600, 14400, 19200,
    28800, 38400, 57600, 115200, 230400, 460800, 921600,
]
DEFAULT_BAUDRATE = 115200

# Pages of the QStackedWidget, in the order they appear in the .ui file:
# "Main" holds the settings, "page_2" holds the progress bar.
PAGE_SETTINGS = 0
PAGE_PROGRESS = 1

# Timer intervals in milliseconds.
PORT_INTERVAL = 2000       # how often the COM port list is re-read
STATUS_INTERVAL = 500      # how often the running test is polled


class DuurtestGUI(QtCore.QObject):
    """
    Connects the widgets from Duurtest_GUI.ui to the driver.

    This is a QObject rather than a QMainWindow because QUiLoader cannot
    load a .ui file into an existing window; it builds and returns its own.
    The window is therefore held in self.ui, and every widget is reached by
    its Qt Designer object name, for example self.ui.pushButton.
    """

    def __init__(self):
        super().__init__()

        self.ui = self._load_ui()

        # The test currently running, or None when nothing is running.
        self.test: DuurTest | None = None

        # Device names of the COM ports currently in the dropdown, used to
        # detect whether the port list actually changed since the last poll.
        self._ports: list[str] = []

        # Every checkbox inside the "Unit List" group box is a dosing unit,
        # except for "Select all". Collecting them dynamically means units
        # added later in Qt Designer need no change here, as long as the
        # object name matches an entry in testDriver.UNITS.
        self.units = [cb for cb in self.ui.UnitSelect.findChildren(QtWidgets.QCheckBox)
                      if cb is not self.ui.SelectAll_checkBox]

        # Fill the baudrate dropdown. The integer is stored as item data, so
        # reading the setting later needs no conversion from the label text.
        for baud in BAUDRATES:
            self.ui.baud_comboBox.addItem(str(baud), baud)
        self.ui.baud_comboBox.setCurrentIndex(self.ui.baud_comboBox.findData(DEFAULT_BAUDRATE))
        self.refresh_ports()

        # Wire up the widgets. pushButton and pushButton_2 are the Start and
        # Stop buttons; those are the names Qt Designer generated.
        self.ui.pushButton.clicked.connect(self.start_test)     # Start Test
        self.ui.pushButton_2.clicked.connect(self.stop_test)    # Stop Test
        self.ui.SelectAll_checkBox.clicked.connect(self.select_all)
        for cb in self.units:
            cb.toggled.connect(self.update_select_all)
        self.ui.Const_radioButton.toggled.connect(self.update_dispense_fields)
        self.ui.Random_radioButton.toggled.connect(self.update_dispense_fields)

        # Looks for newly plugged in adapters. It is stopped while a test
        # runs, so the port list is not enumerated while the driver has one
        # of those ports open.
        self.port_timer = QtCore.QTimer(self, interval=PORT_INTERVAL, timeout=self.refresh_ports)
        self.port_timer.start()

        # Reads the status of the running test. Only active during a test.
        self.status_timer = QtCore.QTimer(self, interval=STATUS_INTERVAL, timeout=self.show_status)

        # The .ui file has no status bar, so QMainWindow creates one the
        # first time statusBar() is called. Do that here rather than during
        # the first status poll, otherwise the central widget would shrink
        # by the height of the bar at the moment the test starts and the
        # page would visibly jump.
        self.ui.statusBar()

        # Start on the settings page with an empty progress bar.
        self.ui.stackedWidget.setCurrentIndex(PAGE_SETTINGS)
        self.ui.progressBar.setValue(0)
        self.update_dispense_fields()

    @staticmethod
    def _load_ui() -> QtWidgets.QMainWindow:
        """
        Build the window from the .ui file at runtime.

        Loading the file directly means changes made in Qt Designer show up
        on the next start without running pyside6-uic first.
        """
        file = QtCore.QFile(str(UI_FILE))
        if not file.open(QtCore.QIODevice.OpenModeFlag.ReadOnly):
            raise RuntimeError(f"Kan {UI_FILE} niet openen: {file.errorString()}")
        try:
            return QUiLoader().load(file)
        finally:
            # Close the file even if loading raises, so no handle is leaked.
            file.close()

    def show(self) -> None:
        """Show the window; called from main()."""
        self.ui.show()

    # ------------------------------------------------------------------
    # Reading the state of the .ui
    # ------------------------------------------------------------------
    def read_settings(self) -> TestSettings:
        """
        Translate the current state of the widgets into settings the driver
        understands. This is the only place that knows both the widget names
        and the driver's settings object.
        """
        return TestSettings(
            # currentData() holds the value stored with addItem(); it is None
            # when the list shows the "no port found" placeholder, and the
            # driver rejects an empty port.
            port=self.ui.Com_comboBox.currentData() or "",
            baudrate=self.ui.baud_comboBox.currentData(),
            # The unit name comes from the object name (CX01_checkBox ->
            # CX01) rather than the label, because the object names are the
            # ones guaranteed to match testDriver.UNITS.
            units=[cb.objectName().replace("_checkBox", "").replace("checkBox", "")
                   for cb in self.units if cb.isChecked()],
            dispense_number=self.ui.DispenseNumber_spinBox.value(),
            power_cycle_interval=self.ui.PowerCycle_spinBox.value(),
            random_dispense=self.ui.Random_radioButton.isChecked(),
            dispense_amount=self.ui.DispenseAmount_spinBox.value(),
            dispense_min=self.ui.MinDispense_spinBox.value(),
            dispense_max=self.ui.MaxDispense_spinBox.value(),
        )

    # ------------------------------------------------------------------
    # Updating the widgets
    # ------------------------------------------------------------------
    def refresh_ports(self) -> None:
        """
        Fill the COM port dropdown with the ports currently connected.

        Called once at start-up and then on a timer, so an adapter plugged
        in later appears without restarting the application.
        """
        combo = self.ui.Com_comboBox
        ports = sorted(list_ports.comports(), key=lambda p: p.device)
        devices = [p.device for p in ports]

        # Rebuilding the list closes an open popup and briefly clears the
        # selection, so only do it when something actually changed and the
        # user is not looking at the list right now.
        if devices == self._ports or combo.view().isVisible():
            return
        self._ports = devices

        # Remember the selected port so it survives the rebuild.
        current = combo.currentData()
        combo.clear()
        for port in ports:
            # Shows e.g. "COM11 - STMicroelectronics Virtual COM Port", but
            # stores only "COM11" as the item data. rstrip() drops the dash
            # again for ports without a description.
            combo.addItem(f"{port.device} - {port.description}".rstrip(" -"), port.device)
        if not ports:
            # Keep the dropdown non-empty so it does not look broken. The
            # data is None, which fails validation when Start is pressed.
            combo.addItem("Geen poort gevonden", None)

        # findData() returns -1 when the previously selected port is gone;
        # fall back to the first entry in that case.
        combo.setCurrentIndex(max(0, combo.findData(current)))

    def select_all(self, checked: bool) -> None:
        """Tick or untick every unit; each one triggers update_select_all."""
        for cb in self.units:
            cb.setChecked(checked)

    def update_select_all(self) -> None:
        """
        Keep the "Select all" box in sync with the individual units: ticked
        when all are selected, empty when none are, and half filled in
        between.

        Tristate is only switched on for that half filled state. If it
        stayed on, clicking the box would cycle through the partial state
        instead of simply selecting or clearing everything.

        Signals are blocked because setCheckState() would otherwise emit
        clicked/toggled and run select_all() again from inside this method.
        """
        box = self.ui.SelectAll_checkBox
        checked = sum(cb.isChecked() for cb in self.units)
        state = QtCore.Qt.CheckState

        box.blockSignals(True)
        box.setTristate(0 < checked < len(self.units))
        box.setCheckState(state.Checked if checked == len(self.units)
                          else state.Unchecked if checked == 0
                          else state.PartiallyChecked)
        box.blockSignals(False)

    def update_dispense_fields(self) -> None:
        """
        Enable only the fields belonging to the selected dispense mode: the
        fixed amount for "Constant value", the min and max for "Random
        value". The spin boxes start out disabled in the .ui file, so this
        also runs once at start-up.
        """
        for w in (self.ui.DispenseAmount_spinBox, self.ui.label_3):
            w.setEnabled(self.ui.Const_radioButton.isChecked())
        for w in (self.ui.MinDispense_spinBox, self.ui.MaxDispense_spinBox,
                  self.ui.label_4, self.ui.label_5):
            w.setEnabled(self.ui.Random_radioButton.isChecked())

    # ------------------------------------------------------------------
    # Starting, stopping and showing the test
    # ------------------------------------------------------------------
    def start_test(self) -> None:
        """Hand the settings to the driver and switch to the progress page."""
        # The Start button sits on the settings page, so it cannot normally be
        # pressed while a test runs. Guard anyway: two tests at once would
        # fight over the relay and mix their log files.
        if self.test is not None and self.test.running:
            return

        settings = self.read_settings()

        # The driver decides what counts as valid; this layer only reports
        # the message, so the same rules apply when it runs without a GUI.
        error = settings.validate()
        if error:
            QtWidgets.QMessageBox.warning(self.ui, "Instellingen", error)
            return

        # start() returns immediately: the test runs in its own thread, so
        # the window keeps responding while it works.
        self.test = DuurTest(settings)
        self.test.start()

        self.ui.progressBar.setValue(0)
        self.ui.stackedWidget.setCurrentIndex(PAGE_PROGRESS)
        self.port_timer.stop()
        self.status_timer.start()

    def stop_test(self) -> None:
        """
        Ask the test to stop. It does not stop instantly: the driver checks
        the request between steps, so the current dispense finishes first
        and the relay is always switched off on the way out. show_status()
        handles the window once the thread has actually ended.
        """
        if self.test is not None and self.test.running:
            self.test.stop()
            self.ui.pushButton_2.setEnabled(False)   # prevents double clicks
        else:
            # Nothing running (for instance the test already failed), so just
            # go back to the settings.
            self.ui.stackedWidget.setCurrentIndex(PAGE_SETTINGS)

    def show_status(self) -> None:
        """
        Read the driver's status and put it in the window. Called by
        status_timer, so this always runs on the GUI thread even though the
        test itself runs on another one.
        """
        test = self.test
        if test is None:
            return

        self.ui.progressBar.setValue(test.percentage)
        self.ui.statusBar().showMessage(test.message)
        if test.running:
            return

        # From here on the test has ended: finished, stopped or failed.
        self.status_timer.stop()
        self.port_timer.start()
        self.ui.pushButton_2.setEnabled(True)
        self.ui.stackedWidget.setCurrentIndex(PAGE_SETTINGS)
        self.test = None

        # Report the outcome. A test that was stopped by the operator gets no
        # dialog: they already know, they pressed the button.
        if test.error:
            QtWidgets.QMessageBox.critical(self.ui, "Duurtest", test.error)
        elif test.completed:
            QtWidgets.QMessageBox.information(self.ui, "Duurtest", "Test afgerond.")

    def shutdown(self) -> None:
        """
        Stop a running test when the application closes, and wait for the
        thread so the relay is switched off before the process exits.
        Connected to QApplication.aboutToQuit in main().

        The wait has to outlast the driver's own settle pause before the
        relay goes off. The test thread is a daemon, so a shorter wait would
        let Python kill it halfway through switching off, leaving the units
        powered.
        """
        if self.test is not None and self.test.running:
            self.test.stop()
            self.test.wait(30)


def main() -> int:
    """
    Create the application, show the window and run the Qt event loop.
    Returns the exit code; called from main.py in the directory above.
    """
    app = QtWidgets.QApplication(sys.argv)
    window = DuurtestGUI()
    app.aboutToQuit.connect(window.shutdown)
    window.show()
    return app.exec()
