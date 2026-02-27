# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for HOMEPAGE Server — Linux (onedir for AppImage).

Build:
    pyinstaller HOMEPAGEServer-linux.spec --noconfirm

Then package with appimagetool — see build_server_linux.sh.
"""
import os
from pathlib import Path

REPO = Path(SPECPATH).parent  # spec lives in server_build/ — go up to project root

_datas = [
    (str(REPO / 'app'), 'app'),
    (str(REPO / 'templates'), 'templates'),
    (str(REPO / 'static'), 'static'),
    (str(REPO / 'manifests'), 'manifests'),
    (str(REPO / 'alembic'), 'alembic'),
    (str(REPO / 'alembic.ini'), '.'),
    (str(Path(SPECPATH) / 'resources' / 'icon.png'), 'resources'),
    (str(REPO / 'data' / 'enabled_modules.json'), 'data'),
]
_datas = [(src, dst) for src, dst in _datas if os.path.exists(src)]

a = Analysis(
    [str(Path(SPECPATH) / 'server_launcher.py')],
    pathex=[str(REPO)],
    binaries=[],
    datas=_datas,
    hiddenimports=[
        # FastAPI / Starlette / Pydantic
        'fastapi', 'fastapi.responses', 'fastapi.staticfiles', 'fastapi.templating',
        'starlette', 'starlette.responses', 'starlette.staticfiles', 'starlette.templating',
        'starlette.middleware', 'starlette.middleware.cors',
        'pydantic', 'pydantic_settings',
        # Uvicorn
        'uvicorn', 'uvicorn.config', 'uvicorn.main',
        'uvicorn.lifespan', 'uvicorn.lifespan.on',
        'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto', 'uvicorn.loops.asyncio',
        'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl', 'uvicorn.protocols.http.httptools_impl',
        'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
        'uvicorn.protocols.websockets.websockets_impl',
        'uvicorn.protocols.websockets.wsproto_impl',
        # SQLAlchemy
        'sqlalchemy', 'sqlalchemy.ext.asyncio',
        'sqlalchemy.dialects.sqlite', 'sqlalchemy.dialects.sqlite.aiosqlite', 'aiosqlite',
        # Jinja2
        'jinja2', 'jinja2.ext',
        # Auth / crypto
        'passlib', 'passlib.handlers', 'passlib.handlers.argon2',
        'argon2', 'argon2.low_level', 'argon2._ffi',
        'jose', 'jose.jwt', 'cryptography',
        # HTTP
        'httpx', 'httpx._transports', 'httpx._transports.default',
        'httpcore', 'h11', 'anyio', 'anyio._backends', 'anyio._backends._asyncio', 'sniffio',
        # Form parsing
        'multipart', 'python_multipart',
        # Tray (Linux uses appindicator or xorg backend)
        'pystray', 'pystray._xorg', 'pystray._appindicator',
        'PIL', 'PIL.Image',
        # Email
        'email_validator',
        # Google (optional)
        'google.auth', 'google.auth.transport', 'google.oauth2',
        'google_auth_oauthlib', 'googleapiclient',
        # Alembic
        'alembic', 'alembic.config', 'alembic.command',
        # App routers
        'app', 'app.main', 'app.config', 'app.database', 'app.models',
        'app.schemas', 'app.server_models', 'app.server_utils', 'app.server_identity',
        'app.auth_utils', 'app.crud_users', 'app.crud_posts',
        'app.content_filter', 'app.federation_utils', 'app.media_utils',
        'app.permissions', 'app.chat_permissions',
        'app.module_manager', 'app.module_manifest', 'app.restore_modules',
        'app.wikipedia_indexer', 'app.wikipedia_utils',
        'app.routers.auth', 'app.routers.posts', 'app.routers.media',
        'app.routers.federation', 'app.routers.community', 'app.routers.users',
        'app.routers.admin', 'app.routers.setup',
        'app.routers.calendar', 'app.routers.games', 'app.routers.wikipedia',
        'app.routers.discussions', 'app.routers.voice', 'app.routers.mail',
        'app.routers.chat', 'app.routers.chat_ex', 'app.routers.create',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', '_tkinter',
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
        'wx', 'gtk', 'webview', 'qtpy',
        'pytest', 'unittest', 'test', 'distutils',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

# onedir EXE — the COLLECT step gathers everything into a directory
# that gets wrapped into an AppImage by build_server_linux.sh
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='HOMEPAGEServer-linux',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,          # Linux: keep stdout for journald/systemd
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
    upx=True,
    upx_exclude=[],
    name='HOMEPAGEServer-linux',
)
