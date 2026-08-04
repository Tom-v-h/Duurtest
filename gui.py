"""
GUI template voor Duurtest_GUI.ui

Laadt het Qt Designer bestand direct in (geen pyuic nodig) en koppelt alle
knoppen, checkboxes en dropdowns aan Python code.

Benodigd:
    pip install PyQt5 pyserial

Starten:
    python gui.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt5 import QtCore, QtWidgets, uic
from serial.tools import list_ports

UI_FILE = Path(__file__).with_name("Duurtest_GUI.ui")

# Standaard baudrates; de tweede waarde is de default selectie.
BAUDRATES = [
    300, 600, 1200, 2400, 4800, 9600, 14400, 19200,
    28800, 38400, 57600, 115200, 230400, 460800, 921600,
]
DEFAULT_BAUDRATE = 115200

# Indexen van de QStackedWidget pagina's ("Main" en "page_2").
PAGE_SETTINGS = 0
PAGE_PROGRESS = 1


class TestWorker(QtCore.QThread):
    """Draait de duurtest in een aparte thread zodat de GUI blijft reageren."""

    progress = QtCore.pyqtSignal(int)        # 0..100
    message = QtCore.pyqtSignal(str)
    finished_ok = QtCore.pyqtSignal(bool)    # True = netjes klaar, False = gestopt/fout

    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._stop_requested = False

    def stop(self) -> None:
        """Vraag de test om te stoppen (wordt tussen dispenses gecontroleerd)."""
        self._stop_requested = True

    def run(self) -> None:
        total = max(1, int(self.settings["dispense_number"]))
        try:
            # TODO: hier de RelayController / xmlrpc logica uit testDriver.py aanroepen.
            #   relay = RelayController(RelayControllerConfig(
            #       port=self.settings["port"], baudrate=self.settings["baudrate"]))
            #   relay.connect()
            for i in range(total):
                if self._stop_requested:
                    self.message.emit("Test gestopt door gebruiker")
                    self.finished_ok.emit(False)
                    return

                # TODO: één dispense-cyclus uitvoeren.
                self.msleep(200)  # placeholder

                self.progress.emit(int((i + 1) / total * 100))
                self.message.emit(f"Dispense {i + 1} van {total}")

            self.finished_ok.emit(True)
        except Exception as exc:                     # noqa: BLE001 - naar GUI melden
            self.message.emit(f"Fout: {exc}")
            self.finished_ok.emit(False)
        finally:
            pass  # TODO: relay.turn_off() / relay.disconnect()


class DuurtestWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        uic.loadUi(UI_FILE, self)

        self.worker: TestWorker | None = None

        # Alle unit-checkboxes uit de "Unit List" groupbox, behalve 'Select all'.
        self.unit_checkboxes = [
            cb for cb in self.UnitSelect.findChildren(QtWidgets.QCheckBox)
            if cb is not self.SelectAll_checkBox
        ]

        self._setup_comboboxes()
        self._connect_signals()

        self.stackedWidget.setCurrentIndex(PAGE_SETTINGS)
        self.progressBar.setValue(0)
        self._update_dispense_fields()

    # ------------------------------------------------------------------
    # Opbouw
    # ------------------------------------------------------------------
    def _setup_comboboxes(self) -> None:
        """Vul de baudrate-lijst en de com-port lijst."""
        for baud in BAUDRATES:
            self.baud_comboBox.addItem(str(baud), baud)
        index = self.baud_comboBox.findData(DEFAULT_BAUDRATE)
        if index >= 0:
            self.baud_comboBox.setCurrentIndex(index)

        self.refresh_ports()

        # Lijst opnieuw uitlezen zodra de gebruiker de dropdown opent,
        # zodat net aangesloten adapters meteen zichtbaar zijn.
        self.Com_comboBox.showPopup = self._com_show_popup

    def _com_show_popup(self) -> None:
        self.refresh_ports()
        QtWidgets.QComboBox.showPopup(self.Com_comboBox)

    def _connect_signals(self) -> None:
        self.pushButton.clicked.connect(self.start_test)        # Start Test
        self.pushButton_2.clicked.connect(self.stop_test)       # Stop Test

        self.SelectAll_checkBox.clicked.connect(self.on_select_all)
        for cb in self.unit_checkboxes:
            cb.toggled.connect(self._update_select_all_state)

        self.Const_radioButton.toggled.connect(self._update_dispense_fields)
        self.Random_radioButton.toggled.connect(self._update_dispense_fields)

    # ------------------------------------------------------------------
    # Com-poorten
    # ------------------------------------------------------------------
    def refresh_ports(self) -> None:
        """Lees de aangesloten seriële poorten uit en houd de selectie vast."""
        current = self.Com_comboBox.currentData()

        self.Com_comboBox.blockSignals(True)
        self.Com_comboBox.clear()
        for port in sorted(list_ports.comports(), key=lambda p: p.device):
            # Toont bv. "COM11 - STMicroelectronics Virtual COM Port"
            label = f"{port.device} - {port.description}" if port.description else port.device
            self.Com_comboBox.addItem(label, port.device)

        if self.Com_comboBox.count() == 0:
            self.Com_comboBox.addItem("Geen poort gevonden", None)

        index = self.Com_comboBox.findData(current)
        if index >= 0:
            self.Com_comboBox.setCurrentIndex(index)
        self.Com_comboBox.blockSignals(False)

    # ------------------------------------------------------------------
    # Checkboxes / radiobuttons
    # ------------------------------------------------------------------
    def on_select_all(self, checked: bool) -> None:
        for cb in self.unit_checkboxes:
            cb.blockSignals(True)
            cb.setChecked(checked)
            cb.blockSignals(False)
        self._update_select_all_state()

    def _update_select_all_state(self) -> None:
        """Zet 'Select all' op aan/uit/half afhankelijk van de units."""
        checked = sum(cb.isChecked() for cb in self.unit_checkboxes)
        self.SelectAll_checkBox.blockSignals(True)
        if checked == 0:
            self.SelectAll_checkBox.setTristate(False)
            self.SelectAll_checkBox.setCheckState(QtCore.Qt.Unchecked)
        elif checked == len(self.unit_checkboxes):
            self.SelectAll_checkBox.setTristate(False)
            self.SelectAll_checkBox.setCheckState(QtCore.Qt.Checked)
        else:
            # Tristate alleen aan om 'deels geselecteerd' te tonen; een klik
            # van de gebruiker zet daarna weer alles aan of uit.
            self.SelectAll_checkBox.setTristate(True)
            self.SelectAll_checkBox.setCheckState(QtCore.Qt.PartiallyChecked)
        self.SelectAll_checkBox.blockSignals(False)

    def _update_dispense_fields(self) -> None:
        """Enable alleen de velden die bij de gekozen dispense-modus horen."""
        constant = self.Const_radioButton.isChecked()
        random_mode = self.Random_radioButton.isChecked()

        for widget in (self.DispenseAmount_spinBox, self.label_3):
            widget.setEnabled(constant)
        for widget in (self.MinDispense_spinBox, self.MaxDispense_spinBox,
                       self.label_4, self.label_5):
            widget.setEnabled(random_mode)

    # ------------------------------------------------------------------
    # Instellingen uitlezen
    # ------------------------------------------------------------------
    def selected_units(self) -> list[str]:
        """Namen van de aangevinkte units, bv. ['CX01', 'MH01']."""
        return [cb.text() for cb in self.unit_checkboxes if cb.isChecked()]

    def get_settings(self) -> dict:
        return {
            "port": self.Com_comboBox.currentData(),
            "baudrate": self.baud_comboBox.currentData(),
            "units": self.selected_units(),
            "dispense_number": self.DispenseNumber_spinBox.value(),
            "power_cycle_interval": self.PowerCycle_spinBox.value(),
            "random_dispense": self.Random_radioButton.isChecked(),
            "dispense_amount": self.DispenseAmount_spinBox.value(),
            "dispense_min": self.MinDispense_spinBox.value(),
            "dispense_max": self.MaxDispense_spinBox.value(),
        }

    def validate(self, settings: dict) -> str | None:
        """Geeft een foutmelding terug, of None als alles klopt."""
        if not settings["port"]:
            return "Selecteer een com-port."
        if not settings["units"]:
            return "Selecteer minimaal één unit."
        if settings["dispense_number"] <= 0:
            return "Aantal dispenses moet groter zijn dan 0."
        if settings["random_dispense"]:
            if settings["dispense_min"] > settings["dispense_max"]:
                return "Min dispense mag niet groter zijn dan max."
            if settings["dispense_max"] <= 0:
                return "Vul een max dispense waarde in."
        elif settings["dispense_amount"] <= 0:
            return "Vul een dispense hoeveelheid in."
        return None

    # ------------------------------------------------------------------
    # Knoppen
    # ------------------------------------------------------------------
    def start_test(self) -> None:
        settings = self.get_settings()

        error = self.validate(settings)
        if error:
            QtWidgets.QMessageBox.warning(self, "Instellingen", error)
            return

        self.progressBar.setValue(0)
        self.stackedWidget.setCurrentIndex(PAGE_PROGRESS)

        self.worker = TestWorker(settings, parent=self)
        self.worker.progress.connect(self.progressBar.setValue)
        self.worker.message.connect(self.statusBar().showMessage)
        self.worker.finished_ok.connect(self.on_test_finished)
        self.worker.start()

    def stop_test(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
            self.pushButton_2.setEnabled(False)   # voorkomt dubbel klikken
        else:
            self.stackedWidget.setCurrentIndex(PAGE_SETTINGS)

    def on_test_finished(self, completed: bool) -> None:
        self.pushButton_2.setEnabled(True)
        self.worker = None
        self.stackedWidget.setCurrentIndex(PAGE_SETTINGS)
        if completed:
            QtWidgets.QMessageBox.information(self, "Duurtest", "Test afgerond.")

    def closeEvent(self, event) -> None:
        """Zorg dat de test-thread netjes stopt bij het sluiten van het venster."""
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(5000)
        super().closeEvent(event)


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    window = DuurtestWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
