# -*- mode: python ; coding: utf-8 -*-
import re
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
        'argon2', 'webview', 'webview.platforms', 'webview.platforms.qt',
        'qtpy', 'qtpy._utils', 'qtpy.enums_compat', 'qtpy.sip',
        'qtpy.QtCore', 'qtpy.QtGui', 'qtpy.QtWidgets',
        'qtpy.QtNetwork', 'qtpy.QtWebChannel',
        'qtpy.QtWebEngineCore', 'qtpy.QtWebEngineWidgets',
        'PyQt6', 'PyQt6.QtCore', 'PyQt6.QtWidgets', 'PyQt6.QtGui',
        'PyQt6.QtWebEngineWidgets', 'PyQt6.QtWebEngineCore',
        'PyQt6.QtWebChannel', 'PyQt6.QtNetwork',
        'packaging', 'packaging.version',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Python modules we never import
        'PyQt6.QtDesigner', 'PyQt6.QtQuick', 'PyQt6.QtQml',
        'PyQt6.QtBluetooth', 'PyQt6.QtNfc', 'PyQt6.QtSensors',
        'PyQt6.QtSerialPort', 'PyQt6.QtMultimedia', 'PyQt6.QtPdf',
        'PyQt6.QtRemoteObjects', 'PyQt6.QtTest', 'PyQt6.QtHelp',
        'PyQt6.QtSpatialAudio', 'PyQt6.QtTextToSpeech',
        'PyQt6.QtPositioning', 'PyQt6.QtDBus', 'PyQt6.QtSvg',
        'PyQt6.QtSql', 'PyQt6.QtXml', 'PyQt6.QtConcurrent',
    ],
    noarchive=False,
    optimize=0,
)

# ---------------------------------------------------------------------------
# Strip unnecessary Qt / Chromium binaries to shrink the single-file exe.
# Keep ONLY what QtWebEngine + Widgets needs.
# ---------------------------------------------------------------------------
_EXCLUDE_DLLS = re.compile(
    r'(?i)'
    r'('
    # ── DevTools resources (debug=False, not needed) ──────────
    r'qtwebengine_devtools_resources'
    # ── Software OpenGL fallback (>99 % of PCs have a GPU) ───
    r'|opengl32sw'
    # ── FFmpeg media codecs (we show web pages, not video) ────
    r'|avcodec|avformat|avutil|swscale|swresample'
    # ── Qt modules we never use ───────────────────────────────
    r'|Qt6Designer'
    r'|Qt6Quick|Qt6Qml|Qt6QmlModels|Qt6QmlMeta|Qt6QmlWorkerScript'
    r'|Qt6Quick3D|Qt6Pdf|Qt6PdfQuick|Qt6PdfWidgets'
    r'|Qt6Bluetooth|Qt6Nfc|Qt6Sensors|Qt6SensorsQuick'
    r'|Qt6Multimedia|Qt6MultimediaQuick|Qt6MultimediaWidgets'
    r'|Qt6RemoteObjects|Qt6SerialPort|Qt6TextToSpeech'
    r'|Qt6Test|Qt6Help|Qt6SpatialAudio|Qt6DBus'
    r'|Qt6Svg|Qt6SvgWidgets|Qt6Sql|Qt6Xml|Qt6Concurrent'
    r'|Qt6LabsPlatform|Qt6LabsSettings|Qt6LabsAnimation'
    r'|Qt6LabsFolderListModel|Qt6LabsSharedImage|Qt6LabsWavefrontMesh'
    r'|Qt6LabsQmlModels'
    r'|Qt6StateMachine|Qt6StateMachineQml'
    r'|Qt6ShaderTools'
    r'|Qt6Positioning|Qt6PositioningQuick'
    r'|Qt6WebEngineQuick|Qt6WebEngineQuickDelegatesQml'
    r'|Qt6WebChannelQuick'
    r'|Qt6QuickControls|Qt6QuickDialogs|Qt6QuickEffects'
    r'|Qt6QuickLayouts|Qt6QuickParticles|Qt6QuickShapes'
    r'|Qt6QuickTemplates|Qt6QuickTest|Qt6QuickTimeline'
    r'|Qt6QuickVectorImage|Qt6QuickWidgets'
    r')\b'
)

_before = len(a.binaries)
a.binaries = [b for b in a.binaries if not _EXCLUDE_DLLS.search(b[0])]
print(f'[spec] Excluded {_before - len(a.binaries)} binaries '
      f'({_before} -> {len(a.binaries)})')

# Also strip devtools .pak from datas
a.datas = [d for d in a.datas if 'devtools' not in d[0].lower()]

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
