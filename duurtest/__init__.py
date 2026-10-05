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

In a .exe built with PyInstaller there are no .py files on disk to find, so
the import path does not help there; the module is registered under its plain
name instead, which comes to the same thing for "import vimbus".
"""

import sys as _sys
from pathlib import Path as _Path

if getattr(_sys, "frozen", False):
    # Imported as duurtest.vimbus, which is the name it is bundled under, and
    # then also made known as plain "vimbus" so control_board finds it.
    from . import vimbus as _vimbus
    _sys.modules.setdefault("vimbus", _vimbus)
else:
    _HERE = str(_Path(__file__).resolve().parent)
    if _HERE not in _sys.path:
        _sys.path.insert(0, _HERE)

__version__ = "1.0.0"
