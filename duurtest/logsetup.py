"""
Logging for the endurance test.

Everything the PC exchanges with the two devices ends up in one file per
run: the commands sent to the STM32 relay and its answers, and every call to
the dispenser with how long it took and what came back. Each line carries a
timestamp down to the millisecond, so an error can be lined up with what
happened just before it.

The loggers used elsewhere in the package:

    duurtest.relay        traffic on the serial port to the STM32
    duurtest.dispenser    calls to the xmlrpc dispenser
    duurtest.testDriver   the test itself: start, dispenses, power cycles

setup_logging() is called once at start-up, from main.py or from the
__main__ block in testDriver.py.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

# Log files are written next to the application, not inside the package.
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"

# Milliseconds are worth having: a garbled reply and the command before it
# can be only a few tens of milliseconds apart.
LOG_FORMAT = "%(asctime)s.%(msecs)03d  %(name)-20s %(levelname)-7s %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: int = logging.INFO, to_console: bool = True) -> Path:
    """
    Start a new log file and route the package's loggers into it.

    Returns the path of the file, so the caller can show it. A new file is
    created per start of the application, named after the moment it started:

        logs/duurtest_2026-08-06_14-30-12.log

    Calling this twice replaces the handlers rather than doubling every line.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logfile = LOG_DIR / f"duurtest_{datetime.now():%Y-%m-%d_%H-%M-%S}.log"

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    handlers: list[logging.Handler] = [logging.FileHandler(logfile, encoding="utf-8")]
    if to_console:
        # Handy when running the driver without the GUI; the GUI itself has
        # no console, where this simply goes nowhere.
        handlers.append(logging.StreamHandler(sys.stdout))
    for handler in handlers:
        handler.setFormatter(formatter)

    root = logging.getLogger("duurtest")
    for old in list(root.handlers):
        root.removeHandler(old)
        old.close()
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level)
    # Keep the package's lines out of any logging the host program set up.
    root.propagate = False

    root.info("Log gestart: %s", logfile)
    return logfile
