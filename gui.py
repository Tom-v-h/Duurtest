"""
GUI laag voor de duurtest (PySide6).

Deze module doet niets meer dan de .ui uitlezen en bijwerken: dropdowns
vullen, de stand van de widgets doorgeven aan de driver, en tijdens de test
de status van de driver in de progressbar zetten. De test zelf staat in
testDriver.py.

    Duurtest_GUI.ui  <->  gui.py  <->  testDriver.py  <->  relay.py

Benodigd:
    pip install PySide6 pyserial
Starten:
    python gui.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6 import QtCore, QtWidgets
from PySide6.QtUiTools import QUiLoader
from serial.tools import list_ports

from testDriver import DuurTest, TestSettings

UI_FILE = Path(__file__).with_name("Duurtest_GUI.ui")

BAUDRATES = [
    300, 600, 1200, 2400, 4800, 9600, 14400, 19200,
    28800, 38400, 57600, 115200, 230400, 460800, 921600,
]
DEFAULT_BAUDRATE = 115200

# Pagina's van de QStackedWidget ("Main" en "page_2").
PAGE_SETTINGS = 0
PAGE_PROGRESS = 1

PORT_INTERVAL = 2000       # com-poorten opnieuw uitlezen (ms)
STATUS_INTERVAL = 500      # status van de test uitlezen (ms)


class DuurtestGUI(QtCore.QObject):
    """Koppelt de widgets uit Duurtest_GUI.ui aan de driver."""

    def __init__(self):
        super().__init__()
        self.ui = self._load_ui()
        self.test: DuurTest | None = None
        self._ports: list[str] = []

        # Alle unit-checkboxes uit de "Unit List" groupbox, behalve 'Select all'.
        self.units = [cb for cb in self.ui.UnitSelect.findChildren(QtWidgets.QCheckBox)
                      if cb is not self.ui.SelectAll_checkBox]

        for baud in BAUDRATES:
            self.ui.baud_comboBox.addItem(str(baud), baud)
        self.ui.baud_comboBox.setCurrentIndex(self.ui.baud_comboBox.findData(DEFAULT_BAUDRATE))
        self.refresh_ports()

        self.ui.pushButton.clicked.connect(self.start_test)     # Start Test
        self.ui.pushButton_2.clicked.connect(self.stop_test)    # Stop Test
        self.ui.SelectAll_checkBox.clicked.connect(self.select_all)
        for cb in self.units:
            cb.toggled.connect(self.update_select_all)
        self.ui.Const_radioButton.toggled.connect(self.update_dispense_fields)
        self.ui.Random_radioButton.toggled.connect(self.update_dispense_fields)

        # Zoekt nieuwe com-poorten; staat stil zolang de test loopt.
        self.port_timer = QtCore.QTimer(self, interval=PORT_INTERVAL, timeout=self.refresh_ports)
        self.port_timer.start()
        # Leest de status van de test; loopt alleen tijdens de test.
        self.status_timer = QtCore.QTimer(self, interval=STATUS_INTERVAL, timeout=self.show_status)

        self.ui.stackedWidget.setCurrentIndex(PAGE_SETTINGS)
        self.ui.progressBar.setValue(0)
        self.update_dispense_fields()

    @staticmethod
    def _load_ui() -> QtWidgets.QMainWindow:
        """QUiLoader kan niet in een bestaande QMainWindow laden en geeft er zelf een terug."""
        file = QtCore.QFile(str(UI_FILE))
        if not file.open(QtCore.QIODevice.OpenModeFlag.ReadOnly):
            raise RuntimeError(f"Kan {UI_FILE} niet openen: {file.errorString()}")
        try:
            return QUiLoader().load(file)
        finally:
            file.close()

    def show(self) -> None:
        self.ui.show()

    # ------------------------------------------------------------------
    # De stand van de .ui uitlezen
    # ------------------------------------------------------------------
    def read_settings(self) -> TestSettings:
        return TestSettings(
            port=self.ui.Com_comboBox.currentData() or "",
            baudrate=self.ui.baud_comboBox.currentData(),
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
    # Widgets bijwerken
    # ------------------------------------------------------------------
    def refresh_ports(self) -> None:
        """Vul de com-port dropdown met de aangesloten poorten."""
        combo = self.ui.Com_comboBox
        ports = sorted(list_ports.comports(), key=lambda p: p.device)
        devices = [p.device for p in ports]
        if devices == self._ports or combo.view().isVisible():
            return  # niets veranderd, of de gebruiker heeft de lijst open
        self._ports = devices

        current = combo.currentData()
        combo.clear()
        for port in ports:
            # Toont bv. "COM11 - STMicroelectronics Virtual COM Port"
            combo.addItem(f"{port.device} - {port.description}".rstrip(" -"), port.device)
        if not ports:
            combo.addItem("Geen poort gevonden", None)
        combo.setCurrentIndex(max(0, combo.findData(current)))

    def select_all(self, checked: bool) -> None:
        for cb in self.units:
            cb.setChecked(checked)

    def update_select_all(self) -> None:
        """Zet 'Select all' op aan, uit of half, afhankelijk van de units."""
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
        """Enable alleen de velden die bij de gekozen dispense-modus horen."""
        for w in (self.ui.DispenseAmount_spinBox, self.ui.label_3):
            w.setEnabled(self.ui.Const_radioButton.isChecked())
        for w in (self.ui.MinDispense_spinBox, self.ui.MaxDispense_spinBox,
                  self.ui.label_4, self.ui.label_5):
            w.setEnabled(self.ui.Random_radioButton.isChecked())

    # ------------------------------------------------------------------
    # Test starten, stoppen en de status tonen
    # ------------------------------------------------------------------
    def start_test(self) -> None:
        settings = self.read_settings()
        error = settings.validate()          # de driver bepaalt wat geldig is
        if error:
            QtWidgets.QMessageBox.warning(self.ui, "Instellingen", error)
            return

        self.test = DuurTest(settings)
        self.test.start()

        self.ui.progressBar.setValue(0)
        self.ui.stackedWidget.setCurrentIndex(PAGE_PROGRESS)
        self.port_timer.stop()
        self.status_timer.start()

    def stop_test(self) -> None:
        if self.test is not None and self.test.running:
            self.test.stop()
            self.ui.pushButton_2.setEnabled(False)   # voorkomt dubbel klikken
        else:
            self.ui.stackedWidget.setCurrentIndex(PAGE_SETTINGS)

    def show_status(self) -> None:
        """Leest de status van de driver en zet die in het venster."""
        test = self.test
        if test is None:
            return

        self.ui.progressBar.setValue(test.percentage)
        self.ui.statusBar().showMessage(test.message)
        if test.running:
            return

        # Klaar: terug naar de instellingen en melden hoe het is afgelopen.
        self.status_timer.stop()
        self.port_timer.start()
        self.ui.pushButton_2.setEnabled(True)
        self.ui.stackedWidget.setCurrentIndex(PAGE_SETTINGS)
        self.test = None
        if test.error:
            QtWidgets.QMessageBox.critical(self.ui, "Duurtest", test.error)
        elif test.completed:
            QtWidgets.QMessageBox.information(self.ui, "Duurtest", "Test afgerond.")

    def shutdown(self) -> None:
        """Stop de test netjes bij het afsluiten van de applicatie."""
        if self.test is not None and self.test.running:
            self.test.stop()
            self.test.wait(10)


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    window = DuurtestGUI()
    app.aboutToQuit.connect(window.shutdown)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
