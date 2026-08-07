"""
Duurtest: endurance test for the dispenser units.

    Duurtest_GUI.ui  <->  gui.py  <->  testDriver.py  <->  relay.py

gui.py reads the settings from the window and shows the status,
testDriver.py runs the test loop and relay.py talks to the STM32 relay.

Start the application with main.py in the directory above.

control_board.py and vimbus.py come from the machine's own toolchain and
import each other by plain name ("import vimbus"), which does not resolve
inside a package. Putting this directory on the import path makes that work
without editing those two files, so a newer version of them can simply be
dropped in place.
"""

import sys as _sys
from pathlib import Path as _Path

_HERE = str(_Path(__file__).resolve().parent)
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)

__version__ = "1.0.0"
