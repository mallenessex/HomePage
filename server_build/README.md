# HOMEPAGE Server App — Productization Plan

> **Goal**: Package the HOMEPAGE server as a standalone **PyInstaller binary** (Windows `.exe`, Linux AppImage, macOS `.app`/`.dmg`) that any non-technical user can download, double-click, and run their own HOMEPAGE node — with zero Python knowledge, zero pip installs, and zero trace of your personal instance.

---

## Build Status (Implementation Tracker)

| Component | File(s) | Status |
|---|---|---|
| Data directory support | `app/config.py` — `_resolve_base_dir()`, `HOMEPAGE_DATA_DIR` env | ✅ Done |
| Setup wizard backend | `app/routers/setup.py` — GET /setup, POST /setup/complete, GET /setup/check | ✅ Done |
| Setup wizard frontend | `templates/setup_wizard.html` — 7-step wizard UI | ✅ Done |
| Setup router registration | `app/main.py` — import + include_router, home→/setup redirect | ✅ Done |
| Server launcher | `server_build/server_launcher.py` — pystray tray + uvicorn + browser open | ✅ Done |
| Tray icon | `server_build/resources/icon.png` + `server_build/resources/generate_icon.py` | ✅ Done |
| Windows PyInstaller spec | `server_build/HOMEPAGEServer-windows.spec` — single-file .exe | ✅ Done |
| Linux PyInstaller spec | `server_build/HOMEPAGEServer-linux.spec` — onedir for AppImage | ✅ Done |
| macOS PyInstaller spec | `server_build/HOMEPAGEServer-macos.spec` — .app bundle | ✅ Done |
| Windows build script | `server_build/build_server_windows.ps1` | ✅ Done |
| Linux build script | `server_build/build_server_linux.sh` | ✅ Done |
| macOS build script | `server_build/build_server_macos.sh` | ✅ Done |
| Build requirements | `server_build/requirements-server-build.txt` | ✅ Done |

### How to Build

**Windows:**
```powershell
.\server_build\build_server_windows.ps1
# Output: dist\HOMEPAGEServer-windows.exe
```

**Linux (or WSL):**
```bash
bash server_build/build_server_linux.sh
# Output: dist/HomepageServer-linux-x86_64.AppImage
```

**macOS:**
```bash
bash server_build/build_server_macos.sh
# Output: dist/HomepageServer-macos.dmg
```

---

## 1. Why PyInstaller (Not Electron)

| Concern | Electron | PyInstaller |
|---|---|---|
| Bundle size | ~150-200 MB (ships Chromium) | ~50-80 MB (Python + server deps, no GUI framework) |
| Runtime deps on host | Still needs bundled/bootstrapped Python for the server | **None.** Everything frozen inside the binary. |
| Complexity | Two runtimes (Node + Python), IPC between them | Single runtime — it's all Python |
| Package hell risk | Still need pip for server deps at install/first-run | Zero. All packages frozen at build time. |
| GUI for wizard | Full web UI in Electron window | `/setup` route in FastAPI itself — opens in the user's browser |
| Tray icon | Electron tray API | `pystray` (pure Python, works great in PyInstaller) |
| Existing expertise | You have Electron client builds | You have working PyInstaller `.spec` files for the client |

**The server has no GUI dependencies** — no Qt, no WebEngine, no webview. It's FastAPI + SQLAlchemy + Jinja2 + uvicorn. PyInstaller handles this cleanly.

---

## 2. High-Level Architecture

```
HomepageServer.exe (single file, ~60 MB)
│
├── Frozen Python 3.12 runtime
├── All pip packages (FastAPI, SQLAlchemy, uvicorn, Jinja2, etc.)
├── app/               (server source)
├── templates/          (Jinja2 templates)
├── static/             (JS, fonts)
├── alembic/            (migrations)
└── manifests/          (module definitions)

On launch:
  1. Detect/create data directory
  2. Start uvicorn on port 8001
  3. Show system tray icon (pystray)
  4. If first run → open browser to http://localhost:8001/setup
  5. If returning → open browser to http://localhost:8001/
  6. Tray menu: Open Admin Panel | Stop Server | Quit
```

The browser IS the UI. The setup wizard is a FastAPI route. No second runtime.

---

## 3. What Exists Today (and What Must Change)

| Layer | Current state | Productized state |
|---|---|---|
| **Server runtime** | Python in a `.venv`, launched by `run_server.bat` / `.sh` / `.ps1` | Single frozen binary — double-click to run |
| **Config** | Hand-edited files, admin web UI | First-run `/setup` wizard in the browser + admin web UI |
| **Client generation** | Admin panel downloads pre-built clients + seed JSON | Same — no change needed. Already works. |
| **Database** | `local.db` next to repo root | Created fresh on first launch in platform data directory |
| **Customizations** | Your personal DB, keys, media, modules | None shipped. All generated at first launch. |
| **Platform support** | `.bat`, `.sh`, `.ps1`, Podman containers | Single binary per OS. Container pipeline kept as advanced option. |

---

## 4. The `/setup` Wizard (New FastAPI Route)

A new route at `/setup` activates **only when no admin user exists yet** (i.e., fresh database). Once setup is complete, it redirects to `/` and never appears again.

### Screen 1: Welcome
- Logo, product name, one-paragraph description
- "Get Started" button → next step

### Screen 2: Server Identity
- **Server name** — text input (e.g. "Smith Family Network")
- **Server description** — optional textarea
- **Platform name** — defaults to "HOMEPAGE"

### Screen 3: Module Picker
- Grid of toggle cards, one per module:
  - Icon, display name, one-line description, on/off toggle
- **Default-on**: Feed, Discovery
- **Default-off**: Calendar, Games, Wikipedia, Voice, Mail, Chat, Create
- Wikipedia shows note: "Requires ~22 GB offline data dump — can be added later via admin panel."

### Screen 4: Admin Account
- **Username** (pre-filled `admin`)
- **Display name**
- **Password** + confirm
- Account created with `is_admin = True`

### Screen 5: Network
- **LAN-only vs Internet-accessible** toggle
  - LAN-only: displays detected LAN IP
  - Internet: prompt for external URL / DynDNS, port-forwarding note
- **Secure Mode** (HTTPS) toggle
- **External Join Policy** dropdown: Conditional (default), Accept None, Accept All

### Screen 6: Generate Clients
- Two sections: **LAN Client** and **External Client**
- For each, OS selector: Windows / Linux / macOS
- "Download" button per client → uses existing `_build_*_client_package_response` admin routes
- Can skip — accessible any time from admin panel

### Screen 7: Summary & Done
- Recap choices
- "Go to Admin Panel" → redirects to `/admin/settings`

**Implementation**: A single new template `templates/setup_wizard.html` with multi-step JS (similar to the Create editor's tab system). One new router file `app/routers/setup.py` with:
- `GET /setup` — render wizard (or redirect to `/` if setup already done)
- `POST /setup/complete` — process all wizard answers, create admin user, write configs, seed modules

---

## 5. System Tray Launcher (`server_launcher.py`)

New top-level entry point that PyInstaller builds from:

```python
"""
server_launcher.py — Entry point for the frozen HOMEPAGE Server binary.

1. Ensures data directory exists
2. Starts uvicorn in a background thread
3. Opens the browser (to /setup on first run, / thereafter)
4. Shows a system tray icon with Start/Stop/Open/Quit
"""

import os, sys, threading, webbrowser, time, signal
from pathlib import Path

# pystray for cross-platform system tray
import pystray
from PIL import Image

def get_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", Path.home())
        return Path(base) / "HOMEPAGE Server"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "HOMEPAGE Server"
    else:
        return Path.home() / ".config" / "homepage-server"

def start_server(data_dir: Path):
    os.environ["HOMEPAGE_DATA_DIR"] = str(data_dir)
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{data_dir / 'local.db'}"
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8001)

def main():
    data_dir = get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)

    # Start server in background thread
    server_thread = threading.Thread(target=start_server, args=(data_dir,), daemon=True)
    server_thread.start()

    # Wait for server to be ready, then open browser
    time.sleep(2)
    first_run = not (data_dir / "local.db").exists()
    url = "http://localhost:8001/setup" if first_run else "http://localhost:8001/"
    webbrowser.open(url)

    # System tray
    icon_image = Image.open(resource_path("icon.png"))
    menu = pystray.Menu(
        pystray.MenuItem("Open Admin Panel", lambda: webbrowser.open("http://localhost:8001/admin/settings")),
        pystray.MenuItem("Quit", lambda icon, item: icon.stop()),
    )
    icon = pystray.Icon("HOMEPAGE Server", icon_image, "HOMEPAGE Server", menu)
    icon.run()
```

*(Simplified sketch — real version handles graceful shutdown, health checks, port conflicts, etc.)*

---

## 6. Project Structure (New/Changed Files)

```
# NEW files
server_launcher.py              # PyInstaller entry point (tray + uvicorn)
app/routers/setup.py            # /setup wizard route
templates/setup_wizard.html     # 7-step setup wizard template
HOMEPAGEServer-windows.spec    # PyInstaller spec — Windows
HOMEPAGEServer-linux.spec      # PyInstaller spec — Linux
HOMEPAGEServer-macos.spec      # PyInstaller spec — macOS
build_server_windows.ps1        # Build script — Windows
build_server_linux.sh           # Build script — Linux
build_server_macos.sh           # Build script — macOS
resources/
  icon.ico                      # Windows tray icon
  icon.icns                     # macOS tray icon
  icon.png                      # Linux tray icon + fallback

# MODIFIED files
app/config.py                   # Support HOMEPAGE_DATA_DIR env override for BASE_DIR
app/main.py                     # Register setup router, add /setup redirect logic
```

---

## 7. What Gets Stripped (Your Personalizations → Clean Slate)

These files/artifacts are **never included** in the distributable:

| Artifact | Reason |
|---|---|
| `local.db` | Your database with your users, posts, messages |
| `data/.secret_key` | Your JWT signing key |
| `data/server_id.txt` | Your unique server identity |
| `data/enabled_modules.json` | Your module selection (wizard generates fresh) |
| `data/runtime_secure_mode.env` | Your secure mode config |
| `data/caddy-data/`, `data/caddy-config/` | Your TLS certs |
| `data/crosswords/` | Cached game data |
| `media/**` | All uploaded user media |
| `external data/wikipedia/` | 22+ GB Wikipedia dump |
| `build/`, `dist/`, `__pycache__/`, `.venv/` | Build artifacts |
| `hf_cookies.txt`, `127.0.0.1` | Dev files |
| `client_app.py`, `*Client*.spec` | Client builds (separate concern) |
| `test_*.py`, `tests/` | Test files |
| `.env` | Dev environment variables |

A `--exclude` list in each `.spec` file enforces this. The PyInstaller `datas` list explicitly names only what's needed.

---

## 8. PyInstaller Spec Strategy

### Why the server build is simpler than the client

The client needed PyQt6 + WebEngine (700+ MB of Qt/Chromium binaries to filter). The server needs:

| Package | PyInstaller support | Notes |
|---|---|---|
| `fastapi` | Excellent | Pure Python |
| `uvicorn` | Excellent | Pure Python + optional C extensions |
| `sqlalchemy` | Excellent | Well-known hooks |
| `aiosqlite` | Excellent | Pure Python |
| `jinja2` | Excellent | Pure Python |
| `pydantic` | Good | Needs `pydantic-core` binary — has wheels for all platforms |
| `cryptography` | Good | Binary wheel, PyInstaller has built-in hooks |
| `python-jose` | Excellent | Pure Python |
| `passlib[argon2]` | Good | `argon2-cffi-bindings` is a C extension — has wheels everywhere |
| `httpx` | Excellent | Pure Python |
| `pystray` | Good | Uses native OS APIs, pure Python on each platform |
| `Pillow` | Excellent | Well-supported, built-in hooks |

No Qt. No Chromium. No WebEngine. Estimated bundle: **50-80 MB**.

### Spec outline (Windows)

```python
# HOMEPAGEServer-windows.spec
a = Analysis(
    ['server_launcher.py'],
    datas=[
        ('app', 'app'),
        ('templates', 'templates'),
        ('static', 'static'),
        ('alembic', 'alembic'),
        ('manifests', 'manifests'),
        ('alembic.ini', '.'),
        ('Caddyfile', '.'),
        ('resources/icon.png', 'resources'),
        ('resources/icon.ico', 'resources'),
    ],
    hiddenimports=[
        'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto',
        'uvicorn.protocols', 'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'app.routers.auth', 'app.routers.admin', 'app.routers.users',
        'app.routers.posts', 'app.routers.media_routes',
        'app.routers.community', 'app.routers.federation',
        'app.routers.setup',
        # Optional module routers
        'app.routers.feed', 'app.routers.calendar',
        'app.routers.games', 'app.routers.wikipedia',
        'app.routers.voice', 'app.routers.mail', 'app.routers.chat',
        'app.routers.create',
        # SQLAlchemy dialects
        'sqlalchemy.dialects.sqlite',
        'aiosqlite',
    ],
    excludes=[
        'tkinter', 'PyQt6', 'PyQt5', 'PySide6', 'PySide2',
        'webview', 'qtpy', 'matplotlib', 'numpy', 'scipy',
        'IPython', 'notebook', 'pytest',
    ],
)

exe = EXE(
    PYZ(a.pure),
    a.scripts, a.binaries, a.datas, [],
    name='HOMEPAGEServer',
    console=False,       # No console window — tray only
    icon='resources/icon.ico',
)
```

Linux and macOS specs are nearly identical, with platform-appropriate `icon` and `console` settings. macOS additionally produces a `.app` bundle via PyInstaller's `BUNDLE()`.

---

## 9. Data Directory Layout (Runtime)

All runtime data lives outside the frozen binary:

| Platform | Location |
|---|---|
| Windows | `%APPDATA%\HOMEPAGE Server\` |
| Linux | `~/.config/homepage-server/` |
| macOS | `~/Library/Application Support/HOMEPAGE Server/` |

```
HOMEPAGE Server/
├── data/
│   ├── .secret_key              # Auto-generated on first run
│   ├── server_id.txt            # Auto-generated on first run
│   ├── enabled_modules.json     # Written by setup wizard
│   └── runtime_secure_mode.env  # Written by wizard if secure mode
├── local.db                     # SQLite database (created by init_db)
├── media/                       # User uploads
└── external data/               # Optional Wikipedia dump
    └── wikipedia/
```

### Config change required

`app/config.py` needs to respect a `HOMEPAGE_DATA_DIR` environment variable:

```python
# In Settings class:
BASE_DIR: Path = Path(os.environ.get("HOMEPAGE_DATA_DIR", Path(__file__).resolve().parent.parent))
```

When running from PyInstaller, `server_launcher.py` sets this env var before importing `app.main`. When running in dev mode, the env var is unset and it falls back to the repo root — no change to your workflow.

---

## 10. Build Pipeline

### Windows
```powershell
# build_server_windows.ps1
.venv\Scripts\activate
pip install pyinstaller pystray Pillow
pyinstaller HOMEPAGEServer-windows.spec --noconfirm
# Output: dist/HOMEPAGEServer.exe (~60 MB)
```

### Linux
```bash
# build_server_linux.sh
source .venv/bin/activate
pip install pyinstaller pystray Pillow
pyinstaller HOMEPAGEServer-linux.spec --noconfirm
# Then wrap in AppImage (same pattern as client build)
# Output: dist/HOMEPAGEServer-linux-x86_64.AppImage
```

### macOS
```bash
# build_server_macos.sh
source .venv/bin/activate
pip install pyinstaller pystray Pillow
pyinstaller HOMEPAGEServer-macos.spec --noconfirm
# Create DMG
hdiutil create -volname "HOMEPAGE Server" \
    -srcfolder dist/HOMEPAGEServer.app \
    -ov -format UDZO dist/HOMEPAGEServer-macos.dmg
# Output: dist/HOMEPAGEServer-macos.dmg
```

### Cross-build notes
- **Windows→Linux**: Use `build_server_linux_from_windows.ps1` (WSL, same pattern as client)
- **CI**: GitHub Actions with 3 runners (Windows, Ubuntu, macOS) each runs their build script

---

## 11. Client Generation (No Change Needed)

Client packaging already works via the admin panel:
- `_build_windows_client_package_response()` → bundles `.exe` + seed JSON
- `_build_linux_client_package_response()` → bundles `.tar.gz` + seed JSON
- `_build_macos_client_package_response()` → bundles `.app` + seed JSON

The setup wizard's Screen 6 just links to these existing admin endpoints. Pre-built client binaries need to be placed in the `dist/` or `build/` directory at deploy time (or downloaded from a release URL at first use).

---

## 12. Migration Path from Current Dev Setup

For existing instances that want to switch to the productized binary:

1. Copy `local.db`, `media/`, `data/` directory to the new data directory location
2. Download and run the HOMEPAGE Server binary
3. It detects existing `local.db` → skips setup wizard
4. Everything works as before

---

## 13. Implementation Phases

### Phase 1: `/setup` Wizard Route (estimated: 2-3 sessions)
- [ ] Create `app/routers/setup.py` with GET/POST routes
- [ ] Build `templates/setup_wizard.html` (7-step multi-screen form)
- [ ] Wire module picker to `enabled_modules.json` + `init_default_modules()`
- [ ] Wire admin account creation
- [ ] Wire server settings (name, network, join policy)
- [ ] Add setup guard: redirect to `/setup` if no admin exists, redirect away if setup done
- [ ] Register router in `app/main.py`
- [ ] Test in browser

### Phase 2: Launcher + Tray (estimated: 1-2 sessions)
- [ ] Create `server_launcher.py` with pystray integration
- [ ] Implement data directory detection/creation
- [ ] Implement uvicorn startup in background thread
- [ ] Browser auto-open (first run → `/setup`, subsequent → `/`)
- [ ] Tray menu: Open Admin Panel, Stop Server, Quit
- [ ] Add `HOMEPAGE_DATA_DIR` support to `app/config.py`
- [ ] Test on Windows

### Phase 3: PyInstaller Specs + Windows Build (estimated: 1-2 sessions)
- [ ] Create `HOMEPAGEServer-windows.spec`
- [ ] Handle `datas` list (templates, static, alembic, manifests)
- [ ] Handle `hiddenimports` (uvicorn internals, SQLAlchemy dialect, all routers)
- [ ] Build and test the `.exe` on a clean Windows machine
- [ ] Create `build_server_windows.ps1`

### Phase 4: Linux + macOS Builds (estimated: 2-3 sessions)
- [ ] Create `HOMEPAGEServer-linux.spec` + AppImage wrapper
- [ ] Create `HOMEPAGEServer-macos.spec` + DMG wrapper
- [ ] Create `build_server_linux.sh` and `build_server_macos.sh`
- [ ] Linux cross-build via WSL (`build_server_linux_from_windows.ps1`)
- [ ] Test on each platform

### Phase 5: Polish (estimated: 1-2 sessions)
- [ ] Icons (`.ico`, `.icns`, `.png`)
- [ ] Graceful shutdown handling (SIGTERM, tray Quit)
- [ ] Port conflict detection + user-friendly error
- [ ] Smoke test on clean machines (all 3 OSes)
- [ ] Write end-user README

**Total estimated: 7-12 sessions**

---

## 14. Key Technical Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **UI framework** | None — browser is the UI | Server already has a full web UI. No GUI dependency = simpler build, smaller binary. |
| **Tray icon** | `pystray` + `Pillow` | Pure Python, works on all 3 OSes, works in PyInstaller. Adds ~2 MB. |
| **Setup wizard** | FastAPI route at `/setup` | Reuses existing template engine. No second framework. |
| **Entry point** | `server_launcher.py` (new) | Thin wrapper: data dir setup → env vars → uvicorn → tray. |
| **Data dir** | OS-standard app data location | Keeps user data safe across binary updates. |
| **HTTPS** | Optional Caddy sidecar (not bundled) | HTTP default for LAN. HTTPS docs point to container pipeline or standalone Caddy install. |
| **Container support** | Kept as alternative, not required | Power users can still use `podman-compose.yml`. |
| **Client packaging** | Existing admin panel mechanism | Already works. Wizard Screen 6 links to it. |

---

## 15. New Dependencies (Server Build Only)

Added to `requirements.txt` (or a new `requirements-server-launcher.txt`):

```
pystray>=0.19.5
Pillow>=10.0.0
```

These are **only needed by the launcher**, not by the server itself. In dev mode, you continue starting the server with `uvicorn app.main:app` and don't need either package.

---

## 16. Summary

The productized HOMEPAGE Server is a **single PyInstaller binary** per platform:

| Platform | Artifact | Size (est.) |
|---|---|---|
| Windows | `HOMEPAGEServer.exe` | ~60 MB |
| Linux | `HOMEPAGEServer-linux-x86_64.AppImage` | ~55 MB |
| macOS | `HOMEPAGEServer-macos.dmg` | ~65 MB |

User experience:
1. **Download one file. Double-click.**
2. Browser opens to a **setup wizard** (server name, modules, admin account, network).
3. Server runs in the background with a **system tray icon**.
4. Admin panel and all features accessible at `http://localhost:8001/`.
5. **Generate client packages** for family/friends directly from the admin panel.
6. **No Python. No pip. No Node. No Docker. No terminal.** Just a binary and a browser.
