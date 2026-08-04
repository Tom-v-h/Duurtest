"""
Duurtest driver.

Bevat de testloop zelf en weet niets van de GUI: geen Qt imports, alleen
gewone Python. De GUI (gui.py) vult een TestSettings en roept run_test() aan
in een aparte thread; voortgang en meldingen gaan via callbacks terug.

Los draaien kan ook:
    python testDriver.py
"""

from __future__ import annotations

import random
import time
import xmlrpc.client
from dataclasses import dataclass, field
from typing import Callable, Optional

from relay import RelayController, RelayControllerConfig

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
DISPENSER_VERBOSE = False

FILL_LEVEL = 3800          # waarde voor correctFillLevel
POWER_ON_DELAY = 10.0      # wachten na inschakelen voor de unit opgestart is
DISPENSE_DELAY = 60.0      # wachten na een dispense voor die klaar is
POWER_OFF_DELAY = 5.0      # hoe lang de spanning eraf blijft bij een powercycle


@dataclass
class TestSettings:
    """Alles wat de gebruiker in de GUI instelt."""

    port: str = ""                     # com-poort van de relay/STM32
    baudrate: int = 115200
    units: list[str] = field(default_factory=list)
    dispense_number: int = 0           # totaal aantal dispenses in de test
    power_cycle_interval: int = 0      # powercycle na elke N dispenses, 0 = uit
    random_dispense: bool = False      # False = vaste hoeveelheid, True = random
    dispense_amount: int = 0           # gebruikt bij random_dispense = False
    dispense_min: int = 0              # gebruikt bij random_dispense = True
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
        if self.power_cycle_interval < 0:
            return "Power cycle interval mag niet negatief zijn."
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


# Callback-types: de GUI hangt hier zijn signals aan, los draaien gebruikt prints.
ProgressCallback = Callable[[int, int], None]   # (gedaan, totaal)
MessageCallback = Callable[[str], None]
StopCallback = Callable[[], bool]               # True = stoppen


class TestAborted(Exception):
    """Wordt intern gebruikt als de gebruiker op Stop drukt."""


def run_test(
    settings: TestSettings,
    on_progress: Optional[ProgressCallback] = None,
    on_message: Optional[MessageCallback] = None,
    should_stop: Optional[StopCallback] = None,
) -> bool:
    """
    Draait de duurtest.

    Doet settings.dispense_number dispenses en schakelt na elke
    settings.power_cycle_interval dispenses de spanning even uit en weer aan.

    Geeft True terug als de test helemaal is afgerond, False als er onderweg
    is gestopt. Fouten (geen verbinding, dispenser niet bereikbaar) komen als
    exception naar boven; de aanroeper handelt die af.
    """
    progress = on_progress or (lambda done, total: None)
    message = on_message or (lambda text: None)
    stop = should_stop or (lambda: False)

    error = settings.validate()
    if error:
        raise ValueError(error)

    relay = RelayController(RelayControllerConfig(
        port=settings.port,
        baudrate=settings.baudrate,
    ))

    message(f"Verbinden met relay op {settings.port}...")
    relay.connect()

    server = xmlrpc.client.ServerProxy(
        DISPENSER_URL, allow_none=True, verbose=DISPENSER_VERBOSE
    )

    total = settings.dispense_number
    interval = settings.power_cycle_interval

    try:
        _power_on(relay, server, message, stop)

        for done in range(1, total + 1):
            _check_stop(stop)

            unit = random.choice(settings.units)
            amount = settings.next_amount()
            message(f"Dispense {done}/{total}: {unit}, {amount} ml")

            server.poll()
            server.correctFillLevel(unit, UNITS[unit], FILL_LEVEL)
            server.prepareUnitForDispense(unit, amount)
            server.dispenseAllPreparedUnits()
            _sleep(DISPENSE_DELAY, stop)

            progress(done, total)

            # Powercycle tussendoor, maar niet na de laatste dispense: daar
            # gaat de spanning er in de finally toch af.
            if interval and done % interval == 0 and done < total:
                message(f"Power cycle na {done} dispenses")
                _power_off(relay)
                _sleep(POWER_OFF_DELAY, stop)
                _power_on(relay, server, message, stop)

        message("Test afgerond")
        return True

    except TestAborted:
        message("Test gestopt")
        return False

    finally:
        _power_off(relay)
        relay.disconnect()


# ----------------------------------------------------------------------
# Hulpfuncties
# ----------------------------------------------------------------------
def _power_on(relay, server, message: MessageCallback, stop: StopCallback) -> None:
    """Spanning erop en de dispenser opnieuw verbinden."""
    relay.turn_on()
    _sleep(POWER_ON_DELAY, stop)
    message(f"Verbinden met dispenser op {DISPENSER_PORT}...")
    server.connect(DISPENSER_PORT, DISPENSER_ADDRESS, DISPENSER_BAUDRATE)
    server.poll()


def _power_off(relay) -> None:
    """Spanning eraf; faalt nooit, zodat afsluiten altijd doorgaat."""
    try:
        if relay.is_connected:
            relay.turn_off()
    except Exception:                      # noqa: BLE001 - stoppen gaat voor
        pass


def _check_stop(stop: StopCallback) -> None:
    if stop():
        raise TestAborted


def _sleep(seconds: float, stop: StopCallback) -> None:
    """
    Wachten in kleine stapjes, zodat op Stop drukken niet pas na 60 seconden
    effect heeft.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _check_stop(stop)
        time.sleep(min(0.1, deadline - time.monotonic()))


if __name__ == "__main__":
    # Los draaien zonder GUI, handig om de driver te testen.
    demo = TestSettings(
        port="COM11",
        baudrate=115200,
        units=["CX01", "MH01", "YH04"],
        dispense_number=10,
        power_cycle_interval=5,
        random_dispense=True,
        dispense_min=1,
        dispense_max=500,
    )
    run_test(
        demo,
        on_progress=lambda done, total: print(f"[{done}/{total}]"),
        on_message=print,
    )
