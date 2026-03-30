# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

_wv_datas = collect_data_files('webview', subdir='js') + collect_data_files('webview', subdir='lib')
_wv_bins  = collect_dynamic_libs('webview')

a = Analysis(
    ['client_app.py'],
    pathex=[],
    binaries=_wv_bins,
    datas=_wv_datas,
    hiddenimports=[
        'argon2',
        'webview', 'webview.platforms', 'webview.platforms.edgechromium',
        'packaging', 'packaging.version',
        'clr', 'pythonnet',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PyQt6', 'PyQt5', 'PySide6', 'PySide2', 'qtpy',
        'gi', 'gtk',
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
    name='HOMEPAGEClient-windows',
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
