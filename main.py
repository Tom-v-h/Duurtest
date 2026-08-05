"""
Entry point of the endurance test application.

    python main.py

Everything else lives in the duurtest package; this file only starts the
GUI and passes its exit code on to the shell.
"""

import sys

from duurtest.gui import main

if __name__ == "__main__":
    sys.exit(main())
