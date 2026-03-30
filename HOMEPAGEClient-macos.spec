# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

# Collect pywebview's JS helpers and native libs
_wv_datas = collect_data_files('webview', subdir='js') + collect_data_files('webview', subdir='lib')
_wv_bins  = collect_dynamic_libs('webview')

a = Analysis(
    ['client_app.py'],
    pathex=[],
    binaries=_wv_bins,
    datas=_wv_datas,
    hiddenimports=[
        'argon2',
        'webview', 'webview.platforms', 'webview.platforms.cocoa',
        'objc', 'Foundation', 'WebKit', 'AppKit', 'CoreFoundation',
        'packaging', 'packaging.version',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PyQt6', 'PyQt5', 'PySide6', 'PySide2', 'qtpy',
        'gi', 'gtk',  # Not needed on macOS
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='HomepageClient',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=False,      # UPX not common on macOS
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=True,
    upx=False,
    name='HomepageClient',
)

app = BUNDLE(
    coll,
    name='HomepageClient.app',
    icon=None,
    bundle_identifier='com.homepage.client',
    info_plist={
        'CFBundleName': 'HOMEPAGE Client',
        'CFBundleDisplayName': 'HOMEPAGE Client',
        'CFBundleShortVersionString': '1.0.0',
        'NSHighResolutionCapable': True,
    },
)
