"""
Duurtest driver.

Bevat de testloop en draait die in een eigen thread, zodat de GUI alleen
maar hoeft te starten, te stoppen en de status uit te lezen. Geen Qt hier:
gewone Python, dus ook los te gebruiken.

    test = DuurTest(TestSettings(port="COM11", units=["CX01"], ...))
    test.start()
    while test.running:
        print(test.percentage, test.message)

Het blok onderaan draait alleen als je deze module zelf start, handig om
zonder GUI te testen of de relay en de dispenser reageren:
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

# Unitnaam -> nummer, zoals de dispenser ze kent.
UNITS: dict[str, int] = {
    'CX01': 1, 'MH01': 2, 'YH04': 3, 'RH01': 4,
    'YX01': 5, 'WX01': 6, 'CH01': 7, 'GH01': 8,
    'BH01': 9, 'OH01': 10, 'RX01': 11, 'YH01': 12,
    'GX01': 13, 'BX01': 14, 'YH02': 15, 'DISP16': 16,
}

# Verbinding met de dispenser (xmlrpc server), los van de relay-poort.
DISPENSER_URL = "http://localhost:9111/"
DISPENSER_PORT = "COM12"
DISPENSER_ADDRESS = "0x0002"
DISPENSER_BAUDRATE = 19200

FILL_LEVEL = 3800          # waarde voor correctFillLevel
POWER_ON_DELAY = 10.0      # wachten na inschakelen tot de unit opgestart is
DISPENSE_DELAY = 60.0      # wachten na een dispense tot die klaar is
POWER_OFF_DELAY = 5.0      # hoe lang de spanning eraf blijft bij een powercycle


@dataclass
class TestSettings:
    """Alles wat in de GUI wordt ingevuld."""

    port: str = ""                     # com-poort van de relay/STM32
    baudrate: int = 115200
    units: list[str] = field(default_factory=list)
    dispense_number: int = 0           # totaal aantal dispenses in de test
    power_cycle_interval: int = 0      # powercycle na elke N dispenses, 0 = uit
    random_dispense: bool = False      # False = vaste hoeveelheid, True = random
    dispense_amount: int = 0           # bij random_dispense = False
    dispense_min: int = 0              # bij random_dispense = True
    dispense_max: int = 0

    def validate(self) -> Optional[str]:
        """Geeft een foutmelding terug, of None als de instellingen kloppen."""
        if not self.port:
            return "Selecteer een com-port."
        if not self.units:
            return "Selecteer minimaal één unit."
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
        """De hoeveelheid ml voor de volgende dispense."""
        if self.random_dispense:
            return random.randint(self.dispense_min, self.dispense_max)
        return self.dispense_amount


class _Stopped(Exception):
    """Intern: er is op Stop gedrukt."""


class DuurTest:
    """
    De duurtest zelf.

    start() zet de test in een aparte thread aan, stop() vraagt hem te
    stoppen. De GUI leest ondertussen running, percentage en message uit.
    """

    def __init__(self, settings: TestSettings):
        self.settings = settings

        # Status die de GUI uitleest.
        self.running = False
        self.completed = False              # True = alle dispenses gedaan
        self.percentage = 0
        self.message = ""
        self.error: Optional[str] = None

        self._stop = False
        self._thread: Optional[threading.Thread] = None

    # -- besturing --------------------------------------------------
    def start(self) -> None:
        error = self.settings.validate()
        if error:
            raise ValueError(error)
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stoppen na de stap die nu bezig is."""
        self._stop = True

    def wait(self, timeout: Optional[float] = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    # -- de test ----------------------------------------------------
    def _run(self) -> None:
        try:
            self._loop()
            self.completed = True
            self.message = "Test afgerond"
        except _Stopped:
            self.message = "Test gestopt"
        except Exception as exc:                 # noqa: BLE001 - naar GUI melden
            self.error = str(exc)
            self.message = f"Fout: {exc}"
        finally:
            self.running = False

    def _loop(self) -> None:
        settings = self.settings
        total = settings.dispense_number
        interval = settings.power_cycle_interval

        config = RelayControllerConfig()
        config.port = settings.port
        config.baudrate = settings.baudrate
        relay = RelayController(config)

        self.message = f"Verbinden met relay op {settings.port}..."
        relay.connect()

        server = xmlrpc.client.ServerProxy(DISPENSER_URL, allow_none=True)

        try:
            self._power_on(relay, server)

            for done in range(1, total + 1):
                self._check_stop()

                unit = random.choice(settings.units)
                amount = settings.next_amount()
                self.message = f"Dispense {done}/{total}: {unit}, {amount} ml"

                server.poll()
                server.correctFillLevel(unit, UNITS[unit], FILL_LEVEL)
                server.prepareUnitForDispense(unit, amount)
                server.dispenseAllPreparedUnits()
                self._sleep(DISPENSE_DELAY)

                self.percentage = int(done / total * 100)

                # Powercycle tussendoor, maar niet na de laatste dispense:
                # daar gaat de spanning er hieronder toch af.
                if interval and done % interval == 0 and done < total:
                    self.message = f"Power cycle na {done} dispenses"
                    self._power_off(relay)
                    self._sleep(POWER_OFF_DELAY)
                    self._power_on(relay, server)
        finally:
            self._power_off(relay)
            relay.disconnect()

    # -- hulpjes ----------------------------------------------------
    def _power_on(self, relay, server) -> None:
        """Spanning erop en de dispenser opnieuw verbinden."""
        relay.turn_on()
        self._sleep(POWER_ON_DELAY)
        self.message = f"Verbinden met dispenser op {DISPENSER_PORT}..."
        server.connect(DISPENSER_PORT, DISPENSER_ADDRESS, DISPENSER_BAUDRATE)
        server.poll()

    @staticmethod
    def _power_off(relay) -> None:
        """Spanning eraf; faalt nooit, zodat afsluiten altijd doorgaat."""
        try:
            if relay.is_connected:
                relay.turn_off()
        except Exception:                        # noqa: BLE001 - stoppen gaat voor
            pass

    def _check_stop(self) -> None:
        if self._stop:
            raise _Stopped

    def _sleep(self, seconds: float) -> None:
        """Wachten in stapjes, zodat Stop niet pas na 60 seconden aankomt."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self._check_stop()
            time.sleep(min(0.1, deadline - time.monotonic()))


if __name__ == "__main__":
    # Los draaien zonder GUI: python -m duurtest.testDriver
    # Deze instellingen vervangen dan wat je normaal in het venster invult.
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
    while test.running:
        print(f"{test.percentage:3d}%  {test.message}")
        time.sleep(1)
    print(test.message)
