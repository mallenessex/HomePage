# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

# Collect pywebview's JS helpers and native libs so the frozen exe can find them
_wv_datas = collect_data_files('webview', subdir='js') + collect_data_files('webview', subdir='lib')
_wv_bins  = collect_dynamic_libs('webview')

a = Analysis(
    ['client_app.py'],
    pathex=[],
    binaries=_wv_bins,
    datas=_wv_datas,
    hiddenimports=[
        'argon2',
        'webview', 'webview.platforms', 'webview.platforms.gtk',
        'gi', 'gi.repository.Gtk', 'gi.repository.Gdk',
        'gi.repository.GLib', 'gi.repository.WebKit2',
        'packaging', 'packaging.version',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PyQt6', 'PyQt5', 'PySide6', 'PySide2', 'qtpy',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

# onedir EXE — do NOT bundle binaries/datas into the exe itself
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='HOMEPAGEClient-linux',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
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

# COLLECT puts all files into a directory (required for AppImage packaging)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=True,
    upx=True,
    upx_exclude=[],
    name='HOMEPAGEClient-linux',
)
