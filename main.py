"""
Entry point of the endurance test application.

    python main.py

Everything else lives in the duurtest package; this file only starts the
GUI and passes its exit code on to the shell.
"""

import sys

from duurtest.gui import main
from duurtest.logsetup import setup_logging

if __name__ == "__main__":
    setup_logging()
    sys.exit(main())
    