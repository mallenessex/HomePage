"""
Pre-build client download packages on server startup.

On every restart the server regenerates seed-infused ZIP bundles for each
platform where a client binary already exists in ``dist/``.  The admin
download routes then serve these pre-built ZIPs instantly instead of
assembling them on the fly.

Output directory: ``dist/prebuilt/``
"""
from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path
from typing import Optional

from .config import settings
from . import server_identity, server_utils
from .routers.admin import (
    _build_client_connection_info,
    _build_client_seed_config,
    _render_client_invite_text,
)

PREBUILT_DIR = settings.BASE_DIR / "dist" / "prebuilt"

# Map of (platform, audience) → output zip filename
_PACKAGE_FILES = {
    ("windows", "lan"): "homepage-client-windows-lan.zip",
    ("windows", "external"): "homepage-client-windows-external.zip",
    ("linux", "lan"): "homepage-client-linux-lan.zip",
    ("linux", "external"): "homepage-client-linux-external.zip",
    ("macos", "lan"): "homepage-client-macos-lan.zip",
    ("macos", "external"): "homepage-client-macos-external.zip",
}


def prebuilt_path(platform: str, audience: str) -> Path:
    """Return the path for a pre-built ZIP package."""
    key = (platform.lower(), audience.lower())
    fname = _PACKAGE_FILES.get(key)
    if not fname:
        raise ValueError(f"Unknown package key: {key}")
    return PREBUILT_DIR / fname


# ── Binary locations ──────────────────────────────────────────────────────

def _windows_exe() -> Optional[Path]:
    p = settings.BASE_DIR / "dist" / "HOMEPAGEClient-windows.exe"
    return p if p.exists() else None


def _linux_tarball() -> Optional[Path]:
    p = settings.BASE_DIR / "dist" / "HomepageClient-linux-x86_64.tar.gz"
    return p if p.exists() else None


def _macos_dmg() -> Optional[Path]:
    p = settings.BASE_DIR / "dist" / "HomepageClient-macos.dmg"
    return p if p.exists() else None


def _macos_app() -> Optional[Path]:
    p = settings.BASE_DIR / "dist" / "HomepageClient.app"
    return p if (p.exists() and p.is_dir()) else None


# ── ZIP builders (write to disk instead of returning a Response) ─────────

def _write_windows_zip(dest: Path, seed: dict, invite_text: str, audience_label: str) -> bool:
    exe = _windows_exe()
    if not exe:
        return False
    seed_text = json.dumps(seed, indent=2)
    readme = (
        f"HOMEPAGE Client Package (Windows - {audience_label})\n\n"
        "Files:\n"
        "- HOMEPAGEClient-windows.exe\n"
        "- homepage-client-seed.json\n\n"
        "Usage:\n"
        "1) Keep both files in the same folder.\n"
        "2) Run HOMEPAGEClient-windows.exe.\n"
        "3) The client auto-loads the seed values on first run.\n\n"
        "Shared values:\n"
        f"{invite_text}\n"
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(str(exe), arcname="HOMEPAGEClient-windows.exe")
        zf.writestr("homepage-client-seed.json", seed_text + "\n")
        zf.writestr("README.txt", readme)
    return True


def _write_linux_zip(dest: Path, seed: dict, invite_text: str, audience_label: str) -> bool:
    tarball = _linux_tarball()
    if not tarball:
        return False
    seed_text = json.dumps(seed, indent=2)
    readme = (
        f"HOMEPAGE Client Package (Linux - {audience_label})\n\n"
        "Files:\n"
        "- HomepageClient-linux-x86_64.tar.gz\n"
        "- homepage-client-seed.json\n\n"
        "Usage:\n"
        "1) Extract: tar xzf HomepageClient-linux-x86_64.tar.gz\n"
        "2) Copy homepage-client-seed.json into the extracted folder.\n"
        "3) Run: ./homepage-client/homepage-client\n"
        "4) The client auto-loads the seed values on first run.\n\n"
        "Shared values:\n"
        f"{invite_text}\n"
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(str(tarball), arcname="HomepageClient-linux-x86_64.tar.gz")
        zf.writestr("homepage-client-seed.json", seed_text + "\n")
        zf.writestr("README.txt", readme)
    return True


def _write_macos_zip(dest: Path, seed: dict, invite_text: str, audience_label: str) -> bool:
    dmg = _macos_dmg()
    app_dir = _macos_app()
    seed_text = json.dumps(seed, indent=2)
    suffix = "external" if audience_label.lower().startswith("off") else "lan"

    dest.parent.mkdir(parents=True, exist_ok=True)

    if dmg:
        readme = (
            f"HOMEPAGE Client Package (macOS - {audience_label})\n\n"
            "Files:\n"
            "- HomepageClient-macos.dmg\n"
            "- homepage-client-seed.json\n\n"
            "Usage:\n"
            "1) Open the DMG and drag HomepageClient to Applications.\n"
            "2) Copy homepage-client-seed.json next to the app or into ~/.\n"
            "3) Launch HomepageClient from Applications.\n"
            "4) The client auto-loads the seed values on first run.\n\n"
            "Shared values:\n"
            f"{invite_text}\n"
        )
        with zipfile.ZipFile(dest, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(str(dmg), arcname="HomepageClient-macos.dmg")
            zf.writestr("homepage-client-seed.json", seed_text + "\n")
            zf.writestr("README.txt", readme)
        return True

    if app_dir:
        readme = (
            f"HOMEPAGE Client Package (macOS - {audience_label})\n\n"
            "Files:\n"
            "- HomepageClient.app/\n"
            "- homepage-client-seed.json\n\n"
            "Usage:\n"
            "1) Move HomepageClient.app to /Applications.\n"
            "2) Keep homepage-client-seed.json next to the app or in ~/.\n"
            "3) Double-click HomepageClient to launch.\n\n"
            "Shared values:\n"
            f"{invite_text}\n"
        )
        with zipfile.ZipFile(dest, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for fpath in app_dir.rglob("*"):
                if fpath.is_file():
                    arcname = "HomepageClient.app/" + fpath.relative_to(app_dir).as_posix()
                    zf.write(str(fpath), arcname=arcname)
            zf.writestr("homepage-client-seed.json", seed_text + "\n")
            zf.writestr("README.txt", readme)
        return True

    return False


# ── Main entry point ─────────────────────────────────────────────────────

_PLATFORM_WRITERS = {
    "windows": _write_windows_zip,
    "linux": _write_linux_zip,
    "macos": _write_macos_zip,
}

_AUDIENCE_LABELS = {
    "lan": "LAN",
    "external": "Off-network",
}


async def prebuild_client_packages(db, server_settings, local_server_id: str) -> dict:
    """
    Pre-generate client ZIP packages for every available platform.

    Called during server startup from the lifespan hook.
    Returns a summary dict of what was built.
    """
    app_http_port = (os.getenv("APP_HTTP_PORT", "8001") or "8001").strip()
    app_https_port = (os.getenv("APP_HTTPS_PORT", "8443") or "8443").strip()

    # Use the configured LAN IP (no request object at startup)
    detected_ip = server_utils.normalize_secure_local_ip(
        getattr(server_settings, "secure_local_ip", None)
    )

    connection_info = _build_client_connection_info(
        server_settings=server_settings,
        app_http_port=app_http_port,
        app_https_port=app_https_port,
        detected_request_ip=detected_ip,
    )

    # Determine external URL (if configured)
    external_url: Optional[str] = None
    db_ext_url = getattr(server_settings, "external_server_url", None)
    if db_ext_url and str(db_ext_url).strip():
        external_url = str(db_ext_url).strip()
    if not external_url:
        for env_key in ("HF_EXTERNAL_SERVER_URL", "EXTERNAL_SERVER_URL"):
            val = (os.getenv(env_key, "") or "").strip()
            if val:
                external_url = val
                break

    # Build seeds for each audience
    lan_server_url = (connection_info.get("lan_server_url") or "").strip()
    lan_info = dict(connection_info)
    lan_info["recommended_server_url"] = lan_server_url or connection_info.get("recommended_server_url", "")
    lan_seed = _build_client_seed_config(
        local_server_id, connection_info,
        server_url_override=lan_server_url, audience="lan",
    )
    lan_invite = _render_client_invite_text(local_server_id, lan_info)

    ext_seed = None
    ext_invite = None
    if external_url:
        ext_info = dict(connection_info)
        ext_info["recommended_server_url"] = external_url
        ext_seed = _build_client_seed_config(
            local_server_id, connection_info,
            server_url_override=external_url, audience="external",
        )
        ext_invite = _render_client_invite_text(local_server_id, ext_info)

    summary: dict[str, str] = {}

    for platform, writer in _PLATFORM_WRITERS.items():
        # LAN package
        dest_lan = prebuilt_path(platform, "lan")
        try:
            if writer(dest_lan, lan_seed, lan_invite, "LAN"):
                size_mb = dest_lan.stat().st_size / (1024 * 1024)
                summary[f"{platform}/lan"] = f"{size_mb:.1f} MB"
        except Exception as exc:
            print(f"[client_packager] WARNING: Failed to prebuild {platform}/lan: {exc}")

        # External package (only if external URL is configured)
        if ext_seed and ext_invite:
            dest_ext = prebuilt_path(platform, "external")
            try:
                if writer(dest_ext, ext_seed, ext_invite, "Off-network"):
                    size_mb = dest_ext.stat().st_size / (1024 * 1024)
                    summary[f"{platform}/external"] = f"{size_mb:.1f} MB"
            except Exception as exc:
                print(f"[client_packager] WARNING: Failed to prebuild {platform}/external: {exc}")

    return summary
