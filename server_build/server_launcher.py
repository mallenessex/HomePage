"""
HOMEPAGE Server Launcher — system-tray wrapper around the FastAPI/Uvicorn server.

This is the PyInstaller entry point for the frozen server binary.
It:
  1. Resolves a writable data directory (~/HOMEPAGE_Server on every OS).
  2. Copies seed assets (templates, static, manifests, alembic) from the frozen
     bundle into the data directory on first run.
  3. Starts Uvicorn in a background thread.
  4. Opens the browser to /setup (first run) or / (already configured).
  5. Shows a system-tray icon with Open / Stop / Quit controls.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

# ── Resolve directories ───────────────────────────────────────────────────

def _bundle_dir() -> Path:
    """Return the directory that holds the frozen payload (or repo root in dev)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # In dev, this file lives in server_build/ — go up one level to the project root
    return Path(__file__).resolve().parent.parent


def _data_dir() -> Path:
    """Return a writable per-user data directory."""
    explicit = os.environ.get("HOMEPAGE_DATA_DIR", "").strip()
    if explicit:
        return Path(explicit).resolve()

    home = Path.home()
    if platform.system() == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", home))
    elif platform.system() == "Darwin":
        base = home / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share"))
    return base / "HOMEPAGE_Server"


BUNDLE_DIR = _bundle_dir()
DATA_DIR = _data_dir()

HOST = os.environ.get("HOMEPAGE_HOST", "0.0.0.0")
PORT = int(os.environ.get("HOMEPAGE_PORT", "8001"))


# ── Seed assets on first run ──────────────────────────────────────────────

# Directories that should be seeded from the bundle into the data dir.
SEED_DIRS = ["templates", "static", "manifests", "alembic"]
SEED_FILES = ["alembic.ini"]

def _seed_data_dir():
    """Copy bundled templates/static/manifests into the data dir if absent."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "data").mkdir(exist_ok=True)
    (DATA_DIR / "media").mkdir(exist_ok=True)

    for dirname in SEED_DIRS:
        src = BUNDLE_DIR / dirname
        dst = DATA_DIR / dirname
        if src.is_dir() and not dst.exists():
            shutil.copytree(src, dst)

    for fname in SEED_FILES:
        src = BUNDLE_DIR / fname
        dst = DATA_DIR / fname
        if src.is_file() and not dst.exists():
            shutil.copy2(src, dst)

    # Always refresh app/ package from bundle (code updates)
    src_app = BUNDLE_DIR / "app"
    dst_app = DATA_DIR / "app"
    if src_app.is_dir():
        if dst_app.exists():
            shutil.rmtree(dst_app)
        shutil.copytree(src_app, dst_app)


# ── Server management ────────────────────────────────────────────────────

_server_thread: threading.Thread | None = None
_server_stop = threading.Event()


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _wait_for_server(timeout: float = 15.0):
    """Block until the server is accepting connections."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _port_in_use(PORT):
            return True
        time.sleep(0.3)
    return False


def _run_server():
    """Run uvicorn in the current thread (called from a background thread)."""
    import uvicorn

    # Point the app at our data directory
    os.environ["HOMEPAGE_DATA_DIR"] = str(DATA_DIR)

    config = uvicorn.Config(
        "app.main:app",
        host=HOST,
        port=PORT,
        log_level="info",
        # Disable reload in frozen builds
        reload=False,
    )
    server = uvicorn.Server(config)

    # Store reference so we can ask it to shut down later
    _run_server._uvicorn_server = server
    server.run()


def start_server():
    global _server_thread
    if _server_thread and _server_thread.is_alive():
        return  # already running

    _server_stop.clear()
    _server_thread = threading.Thread(target=_run_server, daemon=True)
    _server_thread.start()


def stop_server():
    """Gracefully stop the uvicorn server."""
    server = getattr(_run_server, "_uvicorn_server", None)
    if server:
        server.should_exit = True
    _server_stop.set()


# ── First-run detection ──────────────────────────────────────────────────

def _is_first_run() -> bool:
    """Check whether the setup wizard should run (no admin user yet).
    Falls back to checking if the DB file exists at all."""
    db_path = DATA_DIR / "local.db"
    if not db_path.exists():
        return True

    # Quick HTTP check once the server is up
    import urllib.request
    try:
        resp = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/setup/check", timeout=3)
        data = json.loads(resp.read())
        return data.get("setup_needed", True)
    except Exception:
        return True


# ── System tray ──────────────────────────────────────────────────────────

def _create_icon_image():
    """Generate a simple tray icon (blue circle with H) using Pillow."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None

    # Check for bundled icon first
    icon_path = BUNDLE_DIR / "resources" / "icon.png"
    if icon_path.exists():
        return Image.open(icon_path)

    # Fallback: generate programmatically
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([2, 2, size - 2, size - 2], fill=(79, 70, 229))  # indigo-600
    # Draw "H" in the center
    try:
        font = ImageFont.truetype("arial", 36)
    except Exception:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), "H", font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size - tw) / 2, (size - th) / 2 - 2), "H", fill="white", font=font)
    return img


def _open_in_browser(url: str | None = None):
    """Open the admin panel (or setup wizard) in the default browser."""
    target = url or f"http://127.0.0.1:{PORT}/"
    webbrowser.open(target)


def _run_tray():
    """Show a system-tray icon with server controls."""
    try:
        import pystray
        from pystray import MenuItem as item
    except ImportError:
        # pystray not available — just run headless
        print("[launcher] pystray not found — running headless (Ctrl-C to stop)")
        try:
            while not _server_stop.is_set():
                _server_stop.wait(1)
        except KeyboardInterrupt:
            pass
        return

    icon_image = _create_icon_image()
    if icon_image is None:
        print("[launcher] Pillow not found — running headless")
        try:
            while not _server_stop.is_set():
                _server_stop.wait(1)
        except KeyboardInterrupt:
            pass
        return

    def on_open(icon, item):
        _open_in_browser()

    def on_stop(icon, item):
        stop_server()
        icon.stop()

    icon = pystray.Icon(
        "HOMEPAGE Server",
        icon_image,
        "HOMEPAGE Server",
        menu=pystray.Menu(
            item("Open in Browser", on_open, default=True),
            pystray.Menu.SEPARATOR,
            item("Stop Server & Quit", on_stop),
        ),
    )

    icon.run()


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    print(f"[launcher] Bundle dir : {BUNDLE_DIR}")
    print(f"[launcher] Data dir   : {DATA_DIR}")
    print(f"[launcher] Server     : http://{HOST}:{PORT}")

    # 1. Seed data directory from bundle
    _seed_data_dir()

    # 2. Change working directory to data dir so relative paths resolved by
    #    the app (templates, static, alembic.ini, etc.) work correctly.
    os.chdir(DATA_DIR)

    # Ensure app package is importable
    if str(DATA_DIR) not in sys.path:
        sys.path.insert(0, str(DATA_DIR))

    # 3. Start the server in a background thread
    start_server()

    # 4. Wait for it to be ready
    print("[launcher] Waiting for server to start...")
    if _wait_for_server(timeout=20):
        print("[launcher] Server is ready!")
        # Open browser
        if _is_first_run():
            _open_in_browser(f"http://127.0.0.1:{PORT}/setup")
        else:
            _open_in_browser(f"http://127.0.0.1:{PORT}/")
    else:
        print("[launcher] WARNING: Server did not start within timeout")

    # 5. Run the system tray (blocks until quit)
    _run_tray()

    # 6. Cleanup
    stop_server()
    print("[launcher] Goodbye.")


if __name__ == "__main__":
    main()
