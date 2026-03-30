"""
Auto-build missing client binaries.

Detects the host OS and invokes the correct build toolchain for each
platform that *can* be built locally:

    Host OS     | Can build
    ------------|--------------------------------------
    Windows     | Windows (.exe), Linux (via WSL)
    Linux       | Linux (.tar.gz)
    macOS       | macOS (.app / .dmg)

Functions in this module are intentionally *synchronous* (subprocess-based)
so they can be run from ``asyncio.to_thread()`` in endpoint handlers or
during server startup without blocking the event loop.
"""
from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .config import settings

logger = logging.getLogger(__name__)

# ── Expected artefact paths ──────────────────────────────────────────────

DIST_DIR = settings.BASE_DIR / "dist"

WINDOWS_EXE = DIST_DIR / "HOMEPAGEClient-windows.exe"
LINUX_TARBALL = DIST_DIR / "HomepageClient-linux-x86_64.tar.gz"
MACOS_DMG = DIST_DIR / "HomepageClient-macos.dmg"
MACOS_APP = DIST_DIR / "HomepageClient.app"


def _has_windows_client() -> bool:
    return WINDOWS_EXE.exists()


def _has_linux_client() -> bool:
    return LINUX_TARBALL.exists()


def _has_macos_client() -> bool:
    return MACOS_DMG.exists() or (MACOS_APP.exists() and MACOS_APP.is_dir())


# ── Individual builders ──────────────────────────────────────────────────

_BUILD_LOCK: dict[str, bool] = {}            # simple per-platform guard


def _run(cmd: list[str], label: str, **kw) -> bool:
    """Run *cmd* and return True on success."""
    logger.info("[client_builder] Building %s: %s", label, " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            cwd=str(settings.BASE_DIR),
            capture_output=True,
            text=True,
            timeout=600,                      # 10 min ceiling
            **kw,
        )
        if result.returncode == 0:
            logger.info("[client_builder] %s build succeeded.", label)
            return True
        logger.warning(
            "[client_builder] %s build failed (rc=%s):\nstdout: %s\nstderr: %s",
            label, result.returncode, result.stdout[-2000:], result.stderr[-2000:],
        )
    except FileNotFoundError as exc:
        logger.warning("[client_builder] %s build tool not found: %s", label, exc)
    except subprocess.TimeoutExpired:
        logger.warning("[client_builder] %s build timed out.", label)
    except Exception as exc:
        logger.warning("[client_builder] %s build error: %s", label, exc)
    return False


def build_windows_client() -> bool:
    """Build the Windows .exe via PyInstaller.  Only works on Windows."""
    if _has_windows_client():
        return True
    if platform.system() != "Windows":
        logger.info("[client_builder] Skipping Windows build (not on Windows).")
        return False
    if _BUILD_LOCK.get("windows"):
        logger.info("[client_builder] Windows build already in progress.")
        return False
    _BUILD_LOCK["windows"] = True
    try:
        spec = settings.BASE_DIR / "HOMEPAGEClient-windows.spec"
        if not spec.exists():
            logger.warning("[client_builder] Windows spec not found: %s", spec)
            return False
        # Prefer the venv's pyinstaller; fall back to PATH
        pyinstaller = shutil.which("pyinstaller")
        if not pyinstaller:
            # Install it on the fly
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "pyinstaller"],
                cwd=str(settings.BASE_DIR),
                capture_output=True,
                timeout=120,
            )
            pyinstaller = shutil.which("pyinstaller") or "pyinstaller"
        return _run(
            [pyinstaller, str(spec), "--noconfirm"],
            "Windows",
        )
    finally:
        _BUILD_LOCK["windows"] = False


def build_linux_client() -> bool:
    """Build the Linux client.  On Windows uses WSL; on Linux runs natively."""
    if _has_linux_client():
        return True
    if _BUILD_LOCK.get("linux"):
        logger.info("[client_builder] Linux build already in progress.")
        return False
    _BUILD_LOCK["linux"] = True
    try:
        host = platform.system()
        if host == "Windows":
            ps_script = settings.BASE_DIR / "build_client_linux_from_windows.ps1"
            if not ps_script.exists():
                logger.warning("[client_builder] WSL build script not found.")
                return False
            powershell = shutil.which("pwsh") or shutil.which("powershell")
            if not powershell:
                logger.warning("[client_builder] PowerShell not found for WSL build.")
                return False
            return _run(
                [powershell, "-ExecutionPolicy", "Bypass", "-File", str(ps_script)],
                "Linux (via WSL)",
            )
        elif host == "Linux":
            sh_script = settings.BASE_DIR / "build_client_linux.sh"
            if not sh_script.exists():
                logger.warning("[client_builder] Linux build script not found.")
                return False
            return _run(["bash", str(sh_script)], "Linux")
        else:
            logger.info("[client_builder] Skipping Linux build (unsupported host: %s).", host)
            return False
    finally:
        _BUILD_LOCK["linux"] = False


def build_macos_client() -> bool:
    """Build the macOS .app / .dmg.  Only works on macOS."""
    if _has_macos_client():
        return True
    if platform.system() != "Darwin":
        logger.info("[client_builder] Skipping macOS build (not on macOS).")
        return False
    if _BUILD_LOCK.get("macos"):
        logger.info("[client_builder] macOS build already in progress.")
        return False
    _BUILD_LOCK["macos"] = True
    try:
        sh_script = settings.BASE_DIR / "build_client_macos.sh"
        if not sh_script.exists():
            logger.warning("[client_builder] macOS build script not found.")
            return False
        return _run(["bash", str(sh_script)], "macOS")
    finally:
        _BUILD_LOCK["macos"] = False


# ── Convenience helpers ──────────────────────────────────────────────────

_BUILDERS = {
    "windows": build_windows_client,
    "linux": build_linux_client,
    "macos": build_macos_client,
}

_CHECKS = {
    "windows": _has_windows_client,
    "linux": _has_linux_client,
    "macos": _has_macos_client,
}


def build_client(platform_name: str) -> bool:
    """Try to build a single client.  Returns True if the binary now exists."""
    builder = _BUILDERS.get(platform_name.lower())
    if not builder:
        return False
    return builder()


def build_all_missing() -> dict[str, str]:
    """Build every client that is missing and *can* be built on the current host.

    Returns a dict mapping platform name → status string.
    """
    results: dict[str, str] = {}
    for plat, checker in _CHECKS.items():
        if checker():
            results[plat] = "already exists"
            continue
        builder = _BUILDERS[plat]
        ok = builder()
        results[plat] = "built" if ok else "skipped (cannot build on this host)"
    return results


def can_build_on_this_host(platform_name: str) -> bool:
    """Return True if the given platform client can be built on this host OS."""
    host = platform.system()
    plat = platform_name.lower()
    if plat == "windows":
        return host == "Windows"
    if plat == "linux":
        return host in ("Windows", "Linux")
    if plat == "macos":
        return host == "Darwin"
    return False


def build_status_message(platform_name: str) -> str:
    """Human-readable explanation of why a build isn't available."""
    plat = platform_name.lower()
    host = platform.system()

    if plat == "macos" and host != "Darwin":
        return (
            "macOS client can only be built on a Mac. "
            "Run ./build_client_macos.sh on a macOS machine, then copy "
            "dist/HomepageClient-macos.dmg (or dist/HomepageClient.app) "
            "into this server's dist/ folder."
        )
    if plat == "windows" and host != "Windows":
        return (
            "Windows client can only be built on Windows. "
            "Run: pyinstaller HOMEPAGEClient-windows.spec --noconfirm"
        )
    if plat == "linux" and host not in ("Windows", "Linux"):
        return (
            "Linux client requires a Linux host or Windows with WSL. "
            "Run ./build_client_linux.sh on Linux."
        )
    return f"{platform_name} client build failed. Check server logs for details."
