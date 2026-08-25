"""
Switch the relay on and off a number of times, and nothing else.

For checking the relay and the machine's behaviour around power on its own,
without a dispense in sight: does the control board come back every time, does
the relay answer every command, does anything get warm.

    python power_test.py

Everything is set with the constants below. Ctrl+C stops it and leaves the
relay switched off.
"""

import sys
import time

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


def switch(relay: RelayController, on: bool) -> None:
    """Switch the relay and print what it answered."""
    answer = relay.turn_on() if on else relay.turn_off()
    print(f"{stamp()}  {'ON ' if on else 'OFF'}  ->  {answer or '<geen antwoord>'}")


def main() -> int:
    config = RelayControllerConfig()
    config.port = RELAY_PORT
    config.baudrate = RELAY_BAUDRATE

    print(f"{stamp()}  Relay op {RELAY_PORT} @ {RELAY_BAUDRATE} baud, "
          f"{CYCLES} cycles van {ON_SECONDS:.0f} s aan en {OFF_SECONDS:.0f} s uit")

    relay = RelayController(config)
    relay.connect()
    switched_off = False
    try:
        for cycle in range(1, CYCLES + 1):
            print(f"{stamp()}  --- cycle {cycle}/{CYCLES} ---")
            switch(relay, True)
            time.sleep(ON_SECONDS)
            switch(relay, False)
            # No wait after the last one: the run is over, and the relay is
            # already off.
            if cycle < CYCLES:
                time.sleep(OFF_SECONDS)
        switched_off = True
    except KeyboardInterrupt:
        print(f"\n{stamp()}  Afgebroken")
    finally:
        # A run that ended on its own already switched off on its last cycle.
        # Anything else did not, and walking away leaving the machine powered
        # is the one thing this must not do.
        if not switched_off:
            try:
                switch(relay, False)
            except Exception as exc:             # noqa: BLE001 - shutting down wins
                print(f"{stamp()}  Uitschakelen mislukte: {exc}")
        relay.disconnect()
        print(f"{stamp()}  Klaar, relay uit en poort gesloten")

    return 0


if __name__ == "__main__":
    sys.exit(main())
