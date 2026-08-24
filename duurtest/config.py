"""
Every setting of the endurance test that might ever need changing, in one
place. Nothing here talks to hardware; the other modules import from it.

Change a value, save, restart the application. Anything the operator picks
per run (ports, amounts, number of dispenses) belongs in the window instead,
not here.
"""

from pathlib import Path

# ----------------------------------------------------------------------
# The units of the machine
# ----------------------------------------------------------------------
# Unit name -> address the machine knows it by. The names have to match the
# object names of the checkboxes in Duurtest_GUI.ui: a checkbox named
# CX01_checkBox looks for "CX01" in this table.
UNIT_ADDRESSES: dict[str, int] = {
    'CX01': 0x0A01, 'MH01': 0x0A02, 'YH04': 0x0A03, 'RH01': 0x0A04,
    'YX01': 0x0A05, 'WX01': 0x0A06, 'CH01': 0x0A07, 'GH01': 0x0A08,
    'BH01': 0x0A09, 'OH01': 0x0A10, 'RX01': 0x0A11, 'YH01': 0x0A12,
    'GX01': 0x0A13, 'BX01': 0x0A14, 'YH02': 0x0A15, 'WX02': 0x0A16,
}

# ----------------------------------------------------------------------
# Connection to the machine's control board
# ----------------------------------------------------------------------
# The com port is picked in the window; the rest is fixed by the protocol.
MACHINE_ADDRESS = 0x0002        # VIMBus address of the control board
MACHINE_BAUDRATE = 19200
MACHINE_ENCRYPTION = True       # the control board expects encrypted frames

# Seconds to wait for a reply. Without this a board that goes silent would
# leave the test thread waiting for as long as the application runs.
MACHINE_REPLY_TIMEOUT = 5.0

# dispense_all may only answer once the machine has taken the job on, so that
# one command gets a longer window than the rest.
DISPENSE_ALL_TIMEOUT = 120.0

# After a power cycle the control board needs time to come back: an older board
# disappears from the com port list altogether while it has no power, and it
# takes Windows a moment to enumerate it again. Opening the port is therefore
# retried until it works, or until this long has passed.
MACHINE_CONNECT_TIMEOUT = 60.0
MACHINE_CONNECT_RETRY_INTERVAL = 1.0

# Now and then the control board does not come up at all after the power is
# restored: it never appears as a com port and never will, until it is made
# properly powerless once more. With this on, the driver does what you would do
# by hand and switches the machine off and on again rather than ending the run.
# That works around the behaviour, it does not cure it; if it happens often,
# raising POWER_OFF_DELAY is the thing to try first.
#
# Set to False to have a run end at the first failure instead.
RETRY_POWER_CYCLE = True
POWER_CYCLE_ATTEMPTS = 3        # whole off-and-on-again tries before giving up

# ----------------------------------------------------------------------
# Connection to the relay (STM32) that switches the machine's power
# ----------------------------------------------------------------------
# Port and baudrate are picked in the window. The timeouts of the serial port
# itself live in relay.py.
#
# Stop has to be able to cut the power while the test thread is busy, so both
# threads take the relay lock first. Relay commands are bounded by the
# timeouts in relay.py, about a second, so the lock is never held long; this
# is how long Stop waits for it before giving up and leaving the switching to
# the test thread.
RELAY_LOCK_TIMEOUT = 3.0

# Switching off is retried, because silently giving up leaves the machine
# powered, which is worse than a slow shutdown.
POWER_OFF_ATTEMPTS = 3

# ----------------------------------------------------------------------
# Amounts
# ----------------------------------------------------------------------
# dispense_nl() works in nanolitres while the window asks for millilitres.
NL_PER_ML = 1_000_000

# Smallest amount the machine can dispense, and the step the amount fields in
# the window work in. Random amounts are drawn in the same step, so a value
# from the log can also be typed in by hand. Changing these two also means
# changing the minimum and the step of the three amount fields in
# Duurtest_GUI.ui, which Qt Designer holds separately.
MIN_DISPENSE_ML = 0.8
DISPENSE_STEP_ML = 0.1

# Passed to correct_fill_level before every dispense, in nanolitres.
# 3_800_000_000 nl is 3.8 litre.
FILL_LEVEL_NL = 3_800_000_000

# Check what was actually dispensed: the fill level is read once more after the
# machine reports idle, and the drop is compared with what was asked for. Costs
# one extra command per unit per round; set to False to leave it out.
CHECK_DISPENSED_AMOUNT = True

# Read the solenoid temperature of every unit before it dispenses and put it in
# the log. A machine whose firmware does not know the command answers with a
# NACK, which comes back as "Wrong code in command string"; that is logged and
# the round carries on, but if it happens every time this is the switch to
# turn it off.
READ_SOLENOID_TEMPERATURE = True

# How far the measured amount may be off before the log calls it out. A
# deviation is only reported, it never ends the run, and the number of them is
# named in the closing message.
DISPENSE_TOLERANCE_ML = 0.2

# ----------------------------------------------------------------------
# Timing of a test run
# ----------------------------------------------------------------------
POWER_ON_DELAY = 10.0       # after switching on, until the machine has booted
POWER_OFF_DELAY = 15.0       # how long the power stays off during a power cycle

# After a dispense the machine is asked for its status until it is idle again.
MACHINE_STATUS_IDLE = "IDLE"    # the answer that means "done"
STATUS_POLL_INTERVAL = 0.5      # how often to ask

# A machine that never returns to IDLE would keep the test waiting forever.
IDLE_TIMEOUT = 600.0

# ----------------------------------------------------------------------
# Recovering from a garbled reply
# ----------------------------------------------------------------------
# Now and then a reply frame comes back unreadable ("Expected encrypted
# string ... to contain 4 parts, got 3"), which means the serial connection is
# out of step. Rather than ending the test, the driver closes and reopens the
# port and tries the round again this many times.
MACHINE_RETRY_COUNT = 3
MACHINE_RESYNC_DELAY = 2.0      # pause before reopening, lets the line go quiet

# ----------------------------------------------------------------------
# The window
# ----------------------------------------------------------------------
# Baudrates offered for the relay. The relay firmware runs at 115200, which is
# why that one is preselected.
BAUDRATES = [
    300, 600, 1200, 2400, 4800, 9600, 14400, 19200,
    28800, 38400, 57600, 115200, 230400, 460800, 921600,
]
DEFAULT_BAUDRATE = 115200

# Milliseconds between refreshes.
PORT_REFRESH_INTERVAL_MS = 2000     # re-reading the list of com ports
STATUS_REFRESH_INTERVAL_MS = 500    # reading the status of a running test

# The window's stylesheet paints a dark blue background, which the status bar
# and the dialogs inherit while their text stays the default black.
TEXT_COLOUR = "rgb(255, 255, 255)"

# ----------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------
# One file per test run, written next to the application.
LOG_DIRECTORY = Path(__file__).resolve().parent.parent / "logs"

# Milliseconds are worth having: a garbled reply and the command before it can
# be only a few tens of milliseconds apart.
LOG_LINE_FORMAT = "%(asctime)s.%(msecs)03d  %(name)-20s %(levelname)-7s %(message)s"
LOG_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
