# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['skin_tracker.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'matplotlib.backends.backend_tkagg',
    ],
    hookspath=[],
    hooksconfig={'matplotlib': {'backends': ['TkAgg']}},
    runtime_hooks=[],
    excludes=[
        'PySide6',
        'PyQt6',
        'PyQt5',
        'matplotlib.backends.backend_qtagg',
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
    name='SkinTracker',
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
