# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for HOMEPAGE Server — Windows single-file .exe.

Build:
    pyinstaller HOMEPAGEServer-windows.spec --noconfirm

Output:
    dist/HOMEPAGEServer-windows.exe   (~40-60 MB)
"""
import os
from pathlib import Path

REPO = Path(SPECPATH)

# ── Data files to bundle alongside the frozen exe ──────────────────────────
# These get extracted into the _MEIPASS temp dir at runtime, then the launcher
# copies them into the writable user data directory on first run.

_datas = [
    # App package (source — the launcher copies this into DATA_DIR)
    (str(REPO / 'app'), 'app'),
    # Jinja2 templates
    (str(REPO / 'templates'), 'templates'),
    # Static assets (htmx, fonts)
    (str(REPO / 'static'), 'static'),
    # Module manifests (voice.json, mail.json)
    (str(REPO / 'manifests'), 'manifests'),
    # Alembic migrations
    (str(REPO / 'alembic'), 'alembic'),
    (str(REPO / 'alembic.ini'), '.'),
    # Tray icon
    (str(REPO / 'resources' / 'icon.png'), 'resources'),
    # Default enabled-modules / runtime flags
    (str(REPO / 'data' / 'enabled_modules.json'), 'data'),
]

# Only include data dirs that actually exist
_datas = [(src, dst) for src, dst in _datas if os.path.exists(src)]

a = Analysis(
    [str(Path(SPECPATH) / 'server_launcher.py')],
    pathex=[str(REPO)],
    binaries=[],
    datas=_datas,
    hiddenimports=[
        # ── FastAPI / Starlette / Pydantic ──
        'fastapi',
        'fastapi.responses',
        'fastapi.staticfiles',
        'fastapi.templating',
        'starlette',
        'starlette.responses',
        'starlette.staticfiles',
        'starlette.templating',
        'starlette.middleware',
        'starlette.middleware.cors',
        'pydantic',
        'pydantic_settings',
        # ── Uvicorn ──
        'uvicorn',
        'uvicorn.config',
        'uvicorn.main',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.loops.asyncio',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.http.httptools_impl',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.protocols.websockets.websockets_impl',
        'uvicorn.protocols.websockets.wsproto_impl',
        # ── SQLAlchemy ──
        'sqlalchemy',
        'sqlalchemy.ext.asyncio',
        'sqlalchemy.dialects.sqlite',
        'sqlalchemy.dialects.sqlite.aiosqlite',
        'aiosqlite',
        # ── Templating ──
        'jinja2',
        'jinja2.ext',
        # ── Auth / crypto ──
        'passlib',
        'passlib.handlers',
        'passlib.handlers.argon2',
        'argon2',
        'argon2.low_level',
        'argon2._ffi',
        'jose',
        'jose.jwt',
        'cryptography',
        # ── HTTP client ──
        'httpx',
        'httpx._transports',
        'httpx._transports.default',
        'httpcore',
        'h11',
        'anyio',
        'anyio._backends',
        'anyio._backends._asyncio',
        'sniffio',
        # ── Form parsing ──
        'multipart',
        'python_multipart',
        # ── Tray ──
        'pystray',
        'pystray._win32',
        'PIL',
        'PIL.Image',
        # ── Email validation ──
        'email_validator',
        # ── Google auth (optional, for calendar sync) ──
        'google.auth',
        'google.auth.transport',
        'google.oauth2',
        'google_auth_oauthlib',
        'googleapiclient',
        # ── Alembic (DB migrations) ──
        'alembic',
        'alembic.config',
        'alembic.command',
        # ── All app routers (ensure PyInstaller finds them) ──
        'app',
        'app.main',
        'app.config',
        'app.database',
        'app.models',
        'app.schemas',
        'app.server_models',
        'app.server_utils',
        'app.server_identity',
        'app.auth_utils',
        'app.crud_users',
        'app.crud_posts',
        'app.content_filter',
        'app.federation_utils',
        'app.media_utils',
        'app.permissions',
        'app.chat_permissions',
        'app.module_manager',
        'app.module_manifest',
        'app.restore_modules',
        'app.wikipedia_indexer',
        'app.wikipedia_utils',
        'app.routers.auth',
        'app.routers.posts',
        'app.routers.media',
        'app.routers.federation',
        'app.routers.community',
        'app.routers.users',
        'app.routers.admin',
        'app.routers.setup',
        'app.routers.calendar',
        'app.routers.games',
        'app.routers.wikipedia',
        'app.routers.discussions',
        'app.routers.voice',
        'app.routers.mail',
        'app.routers.chat',
        'app.routers.chat_ex',
        'app.routers.create',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # GUI toolkits we definitely don't need on the server
        'tkinter', '_tkinter',
        'PyQt5', 'PyQt6',
        'PySide2', 'PySide6',
        'wx', 'gtk',
        'webview', 'qtpy',
        # Test frameworks
        'pytest', 'unittest',
        # Unused stdlib
        'test', 'distutils',
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
    name='HOMEPAGEServer-windows',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # No console window — tray icon only
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(Path(SPECPATH) / 'resources' / 'icon.png'),
)
