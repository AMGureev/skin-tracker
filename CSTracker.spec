# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['qt_tracker.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'matplotlib.backends.backend_qtagg',
        'PySide6.QtSvg',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'customtkinter',
        'matplotlib.backends.backend_tkagg',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='CSTracker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
