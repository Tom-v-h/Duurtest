# -*- mode: python ; coding: utf-8 -*-
"""
Build instructions for PyInstaller: how the .exe is put together.

    pip install pyinstaller
    pyinstaller duurtest.spec

The result ends up in dist/. Everything the application needs is in there,
including Python and Qt, so the PC running the test needs nothing installed.

The spec file exists because four things about this project need saying, and
none of them can be worked out from the import statements alone:

  * Duurtest_GUI.ui and VDl_logo.png are read at runtime rather than imported,
    so they have to be carried along as data.
  * vimbus is imported by plain name from control_board.py while it lives
    inside the package, which PyInstaller cannot follow.
  * the encryption comes from pycryptodome, which is a compiled library.
  * it is a window, not a console program.

Rebuild after changing anything in duurtest/: the .exe carries a copy of the
code, it does not read the files next to it. Changing a value in config.py
therefore means building again, which is the one thing a build costs you.
"""

# One file or one folder.
#
#   True  -> dist/Duurtest.exe, a single file to copy onto a test PC. It
#            unpacks itself into a temporary directory at every start, which
#            takes a few seconds before the window appears.
#   False -> dist/Duurtest/ with the exe and its libraries beside it. Starts
#            straight away, and is the better choice if the folder can simply
#            stay where it is.
#
# Either way the logs are written to a logs folder next to the exe, not inside
# the bundle; see LOG_DIRECTORY in config.py.
ONEFILE = True

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    # (file on disk, folder inside the bundle). The .ui file has to land in the
    # duurtest folder, because gui.py looks for it next to itself; the logo at
    # the top, because the .ui file names it without a folder.
    datas=[
        ('duurtest/Duurtest_GUI.ui', 'duurtest'),
        ('VDl_logo.png', '.'),
    ],
    # control_board.py says "import vimbus" while the file sits in the package,
    # so nothing points PyInstaller at duurtest/vimbus.py. Naming it here puts
    # it in the bundle; duurtest/__init__.py then registers it under the plain
    # name the import asks for.
    hiddenimports=['duurtest.vimbus', 'duurtest.control_board'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # PySide6 is one large family of libraries and only a handful are used.
    # Leaving out the heaviest keeps the build a few hundred megabytes smaller.
    # Remove a name from this list if something turns out to need it.
    excludes=[
        'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
        'PySide6.Qt3DCore', 'PySide6.QtMultimedia', 'PySide6.QtQuick',
        'PySide6.QtQml', 'PySide6.QtCharts', 'PySide6.QtDataVisualization',
        'tkinter', 'matplotlib', 'numpy',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries if ONEFILE else [],
    a.datas if ONEFILE else [],
    [],
    name='Duurtest',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # No console window: it is a GUI. Note that this also throws away
    # everything printed to the screen, which is why the run is logged to a
    # file as well. Set to True while hunting down a build that will not
    # start: the error then stays on screen instead of flashing past.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon='VDl_logo.ico',   # a .png will not do, Windows wants an .ico
)

if not ONEFILE:
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name='Duurtest',
    )
