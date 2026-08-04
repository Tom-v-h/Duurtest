"""
GUI template voor Duurtest_GUI.ui (PySide6)

Laadt het Qt Designer bestand op runtime in met QUiLoader, zodat je in
Designer kunt blijven werken zonder telkens te compileren, en koppelt alle
knoppen, checkboxes en dropdowns aan Python code.

Benodigd:
    pip install PySide6 pyserial

Starten:
    python gui.py

Alternatief (als je liever autocompletion op de widgets hebt):
    pyside6-uic Duurtest_GUI.ui -o ui_duurtest.py
en dan `from ui_duurtest import Ui_MainWindow` gebruiken in plaats van QUiLoader.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6 import QtCore, QtWidgets
from PySide6.QtUiTools import QUiLoader
from serial.tools import list_ports

UI_FILE = Path(__file__).with_name("Duurtest_GUI.ui")

# Standaard baudrates; DEFAULT_BAUDRATE wordt bij het opstarten geselecteerd.
BAUDRATES = [
    300, 600, 1200, 2400, 4800, 9600, 14400, 19200,
    28800, 38400, 57600, 115200, 230400, 460800, 921600,
]
DEFAULT_BAUDRATE = 115200

# Indexen van de QStackedWidget pagina's ("Main" en "page_2").
PAGE_SETTINGS = 0
PAGE_PROGRESS = 1

# Hoe vaak de lijst met com-poorten opnieuw wordt uitgelezen (ms).
PORT_REFRESH_INTERVAL = 2000


class TestWorker(QtCore.QThread):
    """Draait de duurtest in een aparte thread zodat de GUI blijft reageren."""

    progress = QtCore.Signal(int)          # 0..100
    message = QtCore.Signal(str)
    finished_ok = QtCore.Signal(bool)      # True = netjes klaar, False = gestopt/fout

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

                # TODO: één dispense-cyclus uitvoeren met self.settings["units"].
                self.msleep(200)  # placeholder

                self.progress.emit(int((i + 1) / total * 100))
                self.message.emit(f"Dispense {i + 1} van {total}")

            self.finished_ok.emit(True)
        except Exception as exc:                     # noqa: BLE001 - naar GUI melden
            self.message.emit(f"Fout: {exc}")
            self.finished_ok.emit(False)
        finally:
            pass  # TODO: relay.turn_off() / relay.disconnect()


class DuurtestWindow(QtCore.QObject):
    """
    Controller rond het geladen .ui bestand.

    QUiLoader geeft een compleet opgebouwd venster terug (het kan niet in een
    bestaande QMainWindow laden), dus dit is geen QMainWindow-subclass; het
    venster zelf zit in self.ui en alle widgets zijn self.ui.<objectnaam>.
    """

    def __init__(self):
        super().__init__()

        self.ui = self._load_ui()
        self.worker: TestWorker | None = None

        # Alle unit-checkboxes uit de "Unit List" groupbox, behalve 'Select all'.
        self.unit_checkboxes = [
            cb for cb in self.ui.UnitSelect.findChildren(QtWidgets.QCheckBox)
            if cb is not self.ui.SelectAll_checkBox
        ]

        self._setup_comboboxes()
        self._connect_signals()

        self.ui.stackedWidget.setCurrentIndex(PAGE_SETTINGS)
        self.ui.progressBar.setValue(0)
        self._update_dispense_fields()

    @staticmethod
    def _load_ui() -> QtWidgets.QMainWindow:
        loader = QUiLoader()
        ui_file = QtCore.QFile(str(UI_FILE))
        if not ui_file.open(QtCore.QIODevice.OpenModeFlag.ReadOnly):
            raise RuntimeError(f"Kan {UI_FILE} niet openen: {ui_file.errorString()}")
        try:
            window = loader.load(ui_file)
        finally:
            ui_file.close()
        if window is None:
            raise RuntimeError(f"Kan {UI_FILE} niet laden: {loader.errorString()}")
        return window

    def show(self) -> None:
        self.ui.show()

    # ------------------------------------------------------------------
    # Opbouw
    # ------------------------------------------------------------------
    def _setup_comboboxes(self) -> None:
        """Vul de baudrate-lijst en de com-port lijst."""
        for baud in BAUDRATES:
            self.ui.baud_comboBox.addItem(str(baud), baud)
        index = self.ui.baud_comboBox.findData(DEFAULT_BAUDRATE)
        if index >= 0:
            self.ui.baud_comboBox.setCurrentIndex(index)

        self.refresh_ports()

        # Poll de poorten, zodat een adapter die je later inplugt vanzelf
        # in de lijst verschijnt zonder de applicatie te herstarten.
        self.port_timer = QtCore.QTimer(self)
        self.port_timer.timeout.connect(self.refresh_ports)
        self.port_timer.start(PORT_REFRESH_INTERVAL)

    def _connect_signals(self) -> None:
        self.ui.pushButton.clicked.connect(self.start_test)        # Start Test
        self.ui.pushButton_2.clicked.connect(self.stop_test)       # Stop Test

        self.ui.SelectAll_checkBox.clicked.connect(self.on_select_all)
        for cb in self.unit_checkboxes:
            cb.toggled.connect(self._update_select_all_state)

        self.ui.Const_radioButton.toggled.connect(self._update_dispense_fields)
        self.ui.Random_radioButton.toggled.connect(self._update_dispense_fields)

    # ------------------------------------------------------------------
    # Com-poorten
    # ------------------------------------------------------------------
    def refresh_ports(self) -> None:
        """Lees de aangesloten seriële poorten uit en houd de selectie vast."""
        combo = self.ui.Com_comboBox

        # Niet aanpassen terwijl de gebruiker de lijst open heeft staan.
        if combo.view().isVisible():
            return

        ports = sorted(list_ports.comports(), key=lambda p: p.device)
        devices = [p.device for p in ports]
        if devices == [combo.itemData(i) for i in range(combo.count())]:
            return  # niets veranderd

        current = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for port in ports:
            # Toont bv. "COM11 - STMicroelectronics Virtual COM Port"
            label = f"{port.device} - {port.description}" if port.description else port.device
            combo.addItem(label, port.device)

        if combo.count() == 0:
            combo.addItem("Geen poort gevonden", None)

        index = combo.findData(current)
        if index >= 0:
            combo.setCurrentIndex(index)
        combo.blockSignals(False)

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
        select_all = self.ui.SelectAll_checkBox
        checked = sum(cb.isChecked() for cb in self.unit_checkboxes)

        select_all.blockSignals(True)
        if checked == 0:
            select_all.setTristate(False)
            select_all.setCheckState(QtCore.Qt.CheckState.Unchecked)
        elif checked == len(self.unit_checkboxes):
            select_all.setTristate(False)
            select_all.setCheckState(QtCore.Qt.CheckState.Checked)
        else:
            # Tristate alleen aan om 'deels geselecteerd' te tonen; een klik
            # van de gebruiker zet daarna weer alles aan of uit.
            select_all.setTristate(True)
            select_all.setCheckState(QtCore.Qt.CheckState.PartiallyChecked)
        select_all.blockSignals(False)

    def _update_dispense_fields(self) -> None:
        """Enable alleen de velden die bij de gekozen dispense-modus horen."""
        constant = self.ui.Const_radioButton.isChecked()
        random_mode = self.ui.Random_radioButton.isChecked()

        for widget in (self.ui.DispenseAmount_spinBox, self.ui.label_3):
            widget.setEnabled(constant)
        for widget in (self.ui.MinDispense_spinBox, self.ui.MaxDispense_spinBox,
                       self.ui.label_4, self.ui.label_5):
            widget.setEnabled(random_mode)

    # ------------------------------------------------------------------
    # Instellingen uitlezen
    # ------------------------------------------------------------------
    def selected_units(self) -> list[str]:
        """
        Namen van de aangevinkte units, bv. ['CX01', 'MH01'].

        De naam komt uit de objectnaam van de checkbox (CX01_checkBox -> CX01)
        en niet uit het label, omdat de labels in de .ui nog niet allemaal
        uniek zijn.
        """
        return [
            cb.objectName().replace("_checkBox", "").replace("checkBox", "")
            for cb in self.unit_checkboxes
            if cb.isChecked()
        ]

    def get_settings(self) -> dict:
        return {
            "port": self.ui.Com_comboBox.currentData(),
            "baudrate": self.ui.baud_comboBox.currentData(),
            "units": self.selected_units(),
            "dispense_number": self.ui.DispenseNumber_spinBox.value(),
            "power_cycle_interval": self.ui.PowerCycle_spinBox.value(),
            "random_dispense": self.ui.Random_radioButton.isChecked(),
            "dispense_amount": self.ui.DispenseAmount_spinBox.value(),
            "dispense_min": self.ui.MinDispense_spinBox.value(),
            "dispense_max": self.ui.MaxDispense_spinBox.value(),
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
            QtWidgets.QMessageBox.warning(self.ui, "Instellingen", error)
            return

        self.ui.progressBar.setValue(0)
        self.ui.stackedWidget.setCurrentIndex(PAGE_PROGRESS)

        self.worker = TestWorker(settings, parent=self)
        self.worker.progress.connect(self.ui.progressBar.setValue)
        self.worker.message.connect(self.ui.statusBar().showMessage)
        self.worker.finished_ok.connect(self.on_test_finished)
        self.worker.start()

    def stop_test(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
            self.ui.pushButton_2.setEnabled(False)   # voorkomt dubbel klikken
        else:
            self.ui.stackedWidget.setCurrentIndex(PAGE_SETTINGS)

    def on_test_finished(self, completed: bool) -> None:
        self.ui.pushButton_2.setEnabled(True)
        self.worker = None
        self.ui.stackedWidget.setCurrentIndex(PAGE_SETTINGS)
        if completed:
            QtWidgets.QMessageBox.information(self.ui, "Duurtest", "Test afgerond.")

    def shutdown(self) -> None:
        """Stop de test-thread netjes bij het afsluiten van de applicatie."""
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(5000)


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    window = DuurtestWindow()
    app.aboutToQuit.connect(window.shutdown)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
