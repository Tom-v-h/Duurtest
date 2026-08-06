"""
Logging for the endurance test.

Every test run gets its own file in logs/, named after the moment it
started:

    logs/duurtest_2026-08-06_14-31-05.log

In it goes everything the PC exchanges with the two devices: the commands
sent to the STM32 relay and its answers, and every call to the dispenser
with how long it took and what came back. Each line carries a timestamp down
to the millisecond, so an error can be lined up with what happened just
before it.

Nothing is written between runs. The file is opened when a test starts and
closed when it ends, so a finished run is a complete file on its own.

The loggers used elsewhere in the package:

    duurtest.relay        traffic on the serial port to the STM32
    duurtest.dispenser    calls to the xmlrpc dispenser
    duurtest.testDriver   the test itself: start, dispenses, power cycles

setup_logging() is called once at start-up, from main.py or from the
__main__ block in testDriver.py; it only decides the format and the level.
start_run_log() and stop_run_log() are called by the driver around each run
and are what actually write a file.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Log files are written next to the application, not inside the package.
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"

# Milliseconds are worth having: a garbled reply and the command before it
# can be only a few tens of milliseconds apart.
LOG_FORMAT = "%(asctime)s.%(msecs)03d  %(name)-20s %(levelname)-7s %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)


def setup_logging(level: int = logging.INFO, to_console: bool = True) -> None:
    """
    Prepare the package's logging. Writes no file itself: that happens per
    test run, in start_run_log().

    Calling this twice replaces the handlers rather than doubling every line.
    """
    root = logging.getLogger("duurtest")
    for old in list(root.handlers):
        root.removeHandler(old)
        old.close()

    if to_console:
        # Handy when running the driver without the GUI; the GUI itself has
        # no console, where this simply goes nowhere.
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(_formatter)
        root.addHandler(console)

    root.setLevel(level)
    # Keep the package's lines out of any logging the host program set up.
    root.propagate = False


def start_run_log() -> tuple[Path, logging.Handler]:
    """
    Open the log file for one test run and return its path along with the
    handler writing it. Pass that handler to stop_run_log() when the run has
    ended.

    Two runs started within the same second would share a name and end up in
    one file, so a counter is added when the name is already taken.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now():%Y-%m-%d_%H-%M-%S}"

    logfile = LOG_DIR / f"duurtest_{stamp}.log"
    counter = 2
    while logfile.exists():
        logfile = LOG_DIR / f"duurtest_{stamp}_{counter}.log"
        counter += 1

    handler = logging.FileHandler(logfile, encoding="utf-8")
    handler.setFormatter(_formatter)
    logging.getLogger("duurtest").addHandler(handler)
    return logfile, handler


def stop_run_log(handler: Optional[logging.Handler]) -> None:
    """Close the log file of a run. Does nothing when there is none."""
    if handler is None:
        return
    logger = logging.getLogger("duurtest")
    logger.removeHandler(handler)
    handler.close()
