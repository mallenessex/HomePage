from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse, PlainTextResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import Annotated, Optional
from datetime import timezone
import os
import platform
import io
import json
import zipfile
import ipaddress
from urllib.parse import urlparse

from .. import models, database, permissions, schemas, server_utils, server_models, server_identity, crud_users
from ..chat_permissions import ALL_PERMISSIONS, CHAT_PERMISSION_BITS, DEFAULT_MEMBER_PERMISSIONS
from ..module_manager import ModuleManager, ModuleManagerError
from ..client_builder import build_client, can_build_on_this_host, build_status_message
from . import auth
from ..config import settings
from sqlalchemy import func, desc, and_, or_, delete, insert
from sqlalchemy.exc import OperationalError
import re
import logging
import secrets

router = APIRouter(
    prefix="/admin",
    tags=["admin"]
)

templates = Jinja2Templates(directory=str(settings.BASE_DIR / "templates"))
logger = logging.getLogger(__name__)


def _format_datetime_utc(value) -> str:
    if not value:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%d %H:%M UTC")


def _normalize_external_username(raw: Optional[str], peer_server_id: Optional[str]) -> str:
    value = (raw or "").strip().lower()
    if not value:
        value = f"remote-{(peer_server_id or '').strip().lower()[:8]}"
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    value = re.sub(r"-+", "-", value)
    value = value.strip("._-")
    if not value:
        value = "remote-user"
    return value


async def _allocate_external_username(db: AsyncSession, requested_username: Optional[str], peer_server_id: Optional[str]) -> str:
    base = _normalize_external_username(requested_username, peer_server_id)
    candidate = base
    suffix = 2
    while True:
        existing = await db.execute(select(models.User.id).where(models.User.username == candidate))
        if existing.scalar_one_or_none() is None:
            return candidate
        candidate = f"{base}-{suffix}"
        suffix += 1


async def _is_child_manager(db: AsyncSession, manager: models.User, child: models.User) -> bool:
    """
    Admins can manage anyone. Adults can manage child accounts they have custody over.
    """
    if manager.role == permissions.UserRole.ADMIN:
        return True
    if manager.id == child.id:
        return True
    if child.role != permissions.UserRole.CHILD:
        return False
    if child.parent_id == manager.id:
        return True

    try:
        custody_stmt = select(models.child_custody.c.child_id).where(
            and_(
                models.child_custody.c.child_id == child.id,
                models.child_custody.c.parent_id == manager.id,
            )
        )
        custody_res = await db.execute(custody_stmt)
        return custody_res.first() is not None
    except OperationalError:
        # Backward compatibility for databases that don't have the child_custody table yet.
        return False


async def _get_guardian_users(db: AsyncSession):
    guardians_stmt = (
        select(models.User)
        .where(models.User.role.in_([permissions.UserRole.ADMIN, *permissions.adult_role_values()]))
        .order_by(models.User.username.asc())
    )
    guardians_res = await db.execute(guardians_stmt)
    return guardians_res.scalars().all()


def _parse_int_list(values: list[str]) -> list[int]:
    parsed: list[int] = []
    for raw in values:
        value = (raw or "").strip()
        if not value:
            continue
        try:
            parsed.append(int(value))
        except ValueError:
            continue
    return parsed


def _chat_permission_bits_from_form(form, prefix: str) -> int:
    bits = 0
    for key, value in CHAT_PERMISSION_BITS.items():
        if form.get(f"{prefix}_{key}") in {"1", "on", "true", "True"}:
            bits |= value
    return bits


def _chat_slugify(value: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return base or "channel"


def _suggest_secure_local_domain(server_name: Optional[str]) -> str:
    suggested = server_utils.normalize_secure_local_domain(server_name or "")
    return suggested or "house-fantastico.local"


def _form_bool_value(raw: object) -> bool:
    return str(raw or "").strip().lower() in {"1", "true", "on", "yes"}


def _detect_host_os_label() -> str:
    host_hint = (os.getenv("HF_HOST_OS", "") or "").strip().lower()
    if host_hint in {"windows", "win"}:
        return "Windows"
    if host_hint in {"linux"}:
        return "Linux"
    if host_hint in {"darwin", "mac", "macos", "osx"}:
        return "Darwin"

    runtime = (platform.system() or "").strip()
    if runtime:
        return runtime
    return "Unknown"


def _format_url(scheme: str, host: Optional[str], port: str) -> Optional[str]:
    clean_host = (host or "").strip()
    clean_port = (port or "").strip()
    if not clean_host:
        return None
    default_port = "443" if scheme == "https" else "80"
    if not clean_port or clean_port == default_port:
        return f"{scheme}://{clean_host}/"
    return f"{scheme}://{clean_host}:{clean_port}/"


def _build_client_connection_info(
    server_settings: server_models.ServerSettings,
    app_http_port: str,
    app_https_port: str,
    detected_request_ip: Optional[str],
) -> dict[str, Optional[str]]:
    secure_enabled = _form_bool_value(getattr(server_settings, "secure_mode_enabled", 0))
    secure_domain = server_utils.normalize_secure_local_domain(server_settings.secure_local_domain)
    lan_ip = server_utils.normalize_secure_local_ip(server_settings.secure_local_ip) or detected_request_ip

    secure_url = _format_url("https", secure_domain, app_https_port)
    first_run_http_url = _format_url("http", lan_ip, app_http_port)
    fallback_http_url = _format_url("http", lan_ip, "8001")
    recommended_server_url = secure_url if (secure_enabled and secure_url) else (first_run_http_url or fallback_http_url)

    return {
        "secure_mode_enabled": "true" if secure_enabled else "false",
        "secure_domain": secure_domain,
        "lan_ip": lan_ip,
        "recommended_server_url": recommended_server_url,
        "lan_server_url": first_run_http_url or fallback_http_url or secure_url,
        "secure_url": secure_url,
        "first_run_http_url": first_run_http_url,
        "fallback_http_url": fallback_http_url,
    }


def _is_private_or_local_host(host: Optional[str]) -> bool:
    if not host:
        return True
    value = host.strip().lower().strip("[]")
    if value in {"localhost", "127.0.0.1", "::1"}:
        return True
    if value.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(value)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return False


def _normalize_public_url(raw: str, default_scheme: str = "https", default_port: Optional[str] = None) -> Optional[str]:
    value = (raw or "").strip()
    if not value:
        return None

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        parsed = urlparse(f"{default_scheme}://{value}")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None

    host = (parsed.hostname or "").strip().lower()
    if _is_private_or_local_host(host):
        return None
    port = parsed.port
    scheme = parsed.scheme

    # Auto-correct scheme when port implies the other protocol.
    # e.g. http://example.com:8443 → https://example.com:8443
    if scheme == "http" and port in {443, 8443}:
        scheme = "https"
    elif scheme == "https" and port in {80, 8001}:
        scheme = "http"

    if port is None and default_port:
        try:
            port = int(str(default_port).strip())
        except Exception:
            port = None

    if port:
        default_for_scheme = 443 if scheme == "https" else 80
        if port == default_for_scheme:
            return f"{scheme}://{host}/"
        return f"{scheme}://{host}:{port}/"
    return f"{scheme}://{host}/"


def _infer_external_server_url(request: Request, app_https_port: str, server_settings=None) -> Optional[str]:
    # First priority: explicitly configured external URL from admin settings.
    if server_settings is not None:
        db_url = getattr(server_settings, "external_server_url", None)
        if db_url and str(db_url).strip():
            normalized = _normalize_public_url(str(db_url).strip(), default_scheme="https", default_port=app_https_port)
            if normalized:
                return normalized

    explicit_candidates = [
        (os.getenv("HF_EXTERNAL_SERVER_URL", "") or "").strip(),
        (os.getenv("EXTERNAL_SERVER_URL", "") or "").strip(),
        (getattr(settings, "EXTERNAL_SERVER_URL", None) or "").strip(),
    ]
    for candidate in explicit_candidates:
        normalized = _normalize_public_url(candidate, default_scheme="https", default_port=app_https_port)
        if normalized:
            return normalized

    candidates: list[str] = []
    app_domain = (os.getenv("APP_DOMAIN", "") or "").strip()
    if app_domain:
        candidates.append(app_domain)
    if getattr(settings, "BASE_URL", None):
        candidates.append(str(settings.BASE_URL))
    candidates.append(str(request.base_url))

    for candidate in candidates:
        normalized = _normalize_public_url(candidate, default_scheme="https", default_port=app_https_port)
        if normalized:
            return normalized
    return None


def _render_client_invite_text(local_server_id: str, connection_info: dict[str, Optional[str]]) -> str:
    recommended = connection_info.get("recommended_server_url") or ""
    secure_url = connection_info.get("secure_url") or ""
    first_run = connection_info.get("first_run_http_url") or ""
    fallback = connection_info.get("fallback_http_url") or ""
    secure_domain = connection_info.get("secure_domain") or ""
    lan_ip = connection_info.get("lan_ip") or ""

    lines = [
        "House Fantastico Client Invite",
        "",
        f"Target Server ID: {local_server_id}",
        f"Server URL: {recommended if recommended else '[not configured]'}",
    ]
    if secure_url:
        lines.append(f"Secure URL: {secure_url}")
    if first_run:
        lines.append(f"First-run HTTP URL: {first_run}")
    if fallback:
        lines.append(f"Fallback HTTP URL: {fallback}")
    if secure_domain and lan_ip:
        lines.append(f"Hosts entry: {lan_ip} {secure_domain}")

    lines += [
        "",
        "Client steps:",
        "1) Paste Server URL and Target Server ID into the client app.",
        "2) Request Join.",
        "3) Wait for admin approval, then connect.",
    ]
    return "\n".join(lines)


def _build_client_seed_config(
    local_server_id: str,
    connection_info: dict[str, Optional[str]],
    server_url_override: Optional[str] = None,
    audience: str = "lan",
) -> dict:
    recommended = connection_info.get("recommended_server_url") or ""
    secure_url = connection_info.get("secure_url") or ""
    first_run = connection_info.get("first_run_http_url") or ""
    fallback = connection_info.get("fallback_http_url") or ""
    secure_domain = connection_info.get("secure_domain") or ""
    lan_ip = connection_info.get("lan_ip") or ""
    secure_enabled = (connection_info.get("secure_mode_enabled") or "").strip().lower() in {"1", "true", "yes", "on"}
    hosts_entry = f"{lan_ip} {secure_domain}" if lan_ip and secure_domain else None

    effective_url = (server_url_override or "").strip() or recommended or secure_url or first_run or fallback

    # For external audience, strip out LAN-only data from the secure profile.
    # Remote users can't reach 192.168.x.x or .local domains.
    if audience == "external":
        return {
            "format": "house-fantastico-client-seed-v1",
            "audience": audience,
            "server_url": effective_url,
            "target_server_id": local_server_id,
            "request_status": "not_requested",
            "secure_setup_status": "not_applicable",
            "secure_profile": {
                "server_id": local_server_id,
                "secure_mode_enabled": False,
                "external_server_url": effective_url,
            },
        }

    return {
        "format": "house-fantastico-client-seed-v1",
        "audience": audience,
        "server_url": effective_url,
        "target_server_id": local_server_id,
        "request_status": "not_requested",
        "secure_setup_status": "profile_loaded" if secure_enabled else "not_configured",
        "secure_profile": {
            "server_id": local_server_id,
            "secure_mode_enabled": secure_enabled,
            "secure_local_domain": secure_domain or None,
            "secure_local_ip": lan_ip or None,
            "first_run_http_url": first_run or None,
            "secure_url": secure_url or None,
            "hosts_entry": hosts_entry,
        },
    }


def _build_windows_client_package_response(
    seed: dict,
    invite_text: str,
    audience_label: str,
) -> Response:
    exe_path = settings.BASE_DIR / "dist" / "HOMEPAGEClient-windows.exe"
    if not exe_path.exists():
        # Attempt auto-build
        build_client("windows")
        if not exe_path.exists():
            raise HTTPException(
                status_code=404,
                detail=build_status_message("windows"),
            )

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

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(str(exe_path), arcname="HOMEPAGEClient-windows.exe")
        zf.writestr("homepage-client-seed.json", seed_text + "\n")
        zf.writestr("README.txt", readme)
    payload = buffer.getvalue()

    suffix = "external" if audience_label.lower().startswith("off") else "lan"
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="homepage-client-windows-{suffix}.zip"'},
    )


def _build_linux_client_package_response(
    seed: dict,
    invite_text: str,
    audience_label: str,
) -> Response:
    """Build a Linux client package (zip containing tar.gz + seed config)."""
    tarball_path = settings.BASE_DIR / "dist" / "HomepageClient-linux-x86_64.tar.gz"
    if not tarball_path.exists():
        # Attempt auto-build
        build_client("linux")
        if not tarball_path.exists():
            raise HTTPException(
                status_code=404,
                detail=build_status_message("linux"),
            )

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

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(str(tarball_path), arcname="HomepageClient-linux-x86_64.tar.gz")
        zf.writestr("homepage-client-seed.json", seed_text + "\n")
        zf.writestr("README.txt", readme)
    payload = buffer.getvalue()

    suffix = "external" if audience_label.lower().startswith("off") else "lan"
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="homepage-client-linux-{suffix}.zip"'},
    )


def _build_macos_client_package_response(
    seed: dict,
    invite_text: str,
    audience_label: str,
) -> Response:
    """Build a macOS client package (zip containing .app bundle + seed config).

    If a pre-built .dmg exists, serve that instead of a zip.
    Otherwise fall back to zipping the .app directory.
    """
    dmg_path = settings.BASE_DIR / "dist" / "HomepageClient-macos.dmg"
    app_dir = settings.BASE_DIR / "dist" / "HomepageClient.app"

    seed_text = json.dumps(seed, indent=2)
    suffix = "external" if audience_label.lower().startswith("off") else "lan"

    if dmg_path.exists():
        # Serve the DMG directly (seed is baked in at build time or loaded from working dir)
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
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(str(dmg_path), arcname="HomepageClient-macos.dmg")
            zf.writestr("homepage-client-seed.json", seed_text + "\n")
            zf.writestr("README.txt", readme)
        payload = buffer.getvalue()
        return Response(
            content=payload,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="homepage-client-macos-{suffix}.zip"'},
        )

    if app_dir.exists() and app_dir.is_dir():
        # Zip up the .app bundle + seed
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
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for fpath in app_dir.rglob("*"):
                arcname = "HomepageClient.app/" + fpath.relative_to(app_dir).as_posix()
                if fpath.is_file():
                    zf.write(str(fpath), arcname=arcname)
            zf.writestr("homepage-client-seed.json", seed_text + "\n")
            zf.writestr("README.txt", readme)
        payload = buffer.getvalue()
        return Response(
            content=payload,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="homepage-client-macos-{suffix}.zip"'},
        )

    # Attempt auto-build
    build_client("macos")
    # Re-check after build
    if dmg_path.exists() or (app_dir.exists() and app_dir.is_dir()):
        return _build_macos_client_package_response(seed, invite_text, audience_label)
    raise HTTPException(
        status_code=404,
        detail=build_status_message("macos"),
    )


def _resolve_secure_domain_ip_or_400(server_settings: server_models.ServerSettings) -> tuple[str, str]:
    domain = server_utils.normalize_secure_local_domain(server_settings.secure_local_domain)
    ip = server_utils.normalize_secure_local_ip(server_settings.secure_local_ip)
    if not domain or not ip:
        raise HTTPException(
            status_code=400,
            detail="Secure mode domain and LAN IP must be configured before exporting setup scripts.",
        )
    return domain, ip


def _render_secure_mode_setup_script(
    target_os: str,
    domain: str,
    ip: str,
    app_http_port: str,
    app_https_port: str,
) -> tuple[str, str]:
    target = (target_os or "").strip().lower()
    ca_url = f"http://{ip}:{app_http_port}/.well-known/local-ca.crt"

    if target in {"windows", "win"}:
        script = f"""$ErrorActionPreference = "Stop"
$Domain = "{domain}"
$ServerIp = "{ip}"
$HttpPort = "{app_http_port}"
$CaUrl = "{ca_url}"
$CaPath = Join-Path $env:TEMP "house-fantastico-local-ca.crt"

Write-Host "Downloading local CA from $CaUrl ..."
Invoke-WebRequest -Uri $CaUrl -OutFile $CaPath -UseBasicParsing

$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)

if ($isAdmin) {{
    certutil -addstore -f Root $CaPath | Out-Null
    Write-Host "Installed CA into Local Machine Root store."
}} else {{
    Write-Warning "Not running as Administrator. Installing CA in Current User store only."
    certutil -user -addstore -f Root $CaPath | Out-Null
}}

$HostsPath = Join-Path $env:SystemRoot "System32\\drivers\\etc\\hosts"
$existing = @()
if (Test-Path $HostsPath) {{
    $existing = Get-Content -Path $HostsPath -ErrorAction SilentlyContinue
}}
$pattern = "(?i)(^|\\s)" + [regex]::Escape($Domain) + "(\\s|$)"
$filtered = @()
foreach ($line in $existing) {{
    if ($line -match $pattern) {{ continue }}
    $filtered += $line
}}
$filtered += "$ServerIp $Domain"

if ($isAdmin) {{
    Set-Content -Path $HostsPath -Value $filtered -Encoding Ascii
    ipconfig /flushdns | Out-Null
    Write-Host "Hosts updated and DNS cache flushed."
}} else {{
    Write-Warning "Hosts update requires Administrator."
    Write-Host "Add this line manually to hosts: $ServerIp $Domain"
}}

Write-Host "Secure setup complete. Use https://$Domain:{app_https_port}/"
"""
        return script, "house-fantastico-secure-setup-windows.ps1"

    if target in {"macos", "mac", "darwin", "osx"}:
        script = f"""#!/bin/bash
set -euo pipefail

DOMAIN="{domain}"
SERVER_IP="{ip}"
HTTP_PORT="{app_http_port}"
CA_URL="{ca_url}"
CA_FILE="/tmp/house-fantastico-local-ca.crt"

echo "Downloading local CA from $CA_URL ..."
curl -fsSL "$CA_URL" -o "$CA_FILE"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo to install CA + update /etc/hosts."
  echo "Manual hosts entry: $SERVER_IP $DOMAIN"
  exit 1
fi

security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain "$CA_FILE" || true

TMP_HOSTS="$(mktemp)"
grep -Ev "[[:space:]]${{DOMAIN}}([[:space:]]|$)" /etc/hosts > "$TMP_HOSTS" || true
echo "$SERVER_IP $DOMAIN" >> "$TMP_HOSTS"
cat "$TMP_HOSTS" > /etc/hosts
rm -f "$TMP_HOSTS"

dscacheutil -flushcache || true
killall -HUP mDNSResponder || true

echo "Secure setup complete. Use https://$DOMAIN:{app_https_port}/"
"""
        return script, "house-fantastico-secure-setup-macos.command"

    if target in {"linux"}:
        script = f"""#!/bin/bash
set -euo pipefail

DOMAIN="{domain}"
SERVER_IP="{ip}"
HTTP_PORT="{app_http_port}"
CA_URL="{ca_url}"
CA_TMP="/tmp/house-fantastico-local-ca.crt"
DEBIAN_CA_DST="/usr/local/share/ca-certificates/house-fantastico-local-ca.crt"
RHEL_CA_DST="/etc/pki/ca-trust/source/anchors/house-fantastico-local-ca.crt"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo/root to install CA + update /etc/hosts."
  echo "Manual hosts entry: $SERVER_IP $DOMAIN"
  exit 1
fi

if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$CA_URL" -o "$CA_TMP"
elif command -v wget >/dev/null 2>&1; then
  wget -qO "$CA_TMP" "$CA_URL"
else
  echo "Neither curl nor wget is available."
  exit 1
fi

if [ -d /usr/local/share/ca-certificates ]; then
  cp "$CA_TMP" "$DEBIAN_CA_DST"
  if command -v update-ca-certificates >/dev/null 2>&1; then
    update-ca-certificates
  fi
elif [ -d /etc/pki/ca-trust/source/anchors ]; then
  cp "$CA_TMP" "$RHEL_CA_DST"
  if command -v update-ca-trust >/dev/null 2>&1; then
    update-ca-trust extract
  fi
fi

TMP_HOSTS="$(mktemp)"
grep -Ev "[[:space:]]${{DOMAIN}}([[:space:]]|$)" /etc/hosts > "$TMP_HOSTS" || true
echo "$SERVER_IP $DOMAIN" >> "$TMP_HOSTS"
cat "$TMP_HOSTS" > /etc/hosts
rm -f "$TMP_HOSTS"

if command -v resolvectl >/dev/null 2>&1; then
  resolvectl flush-caches || true
elif command -v systemd-resolve >/dev/null 2>&1; then
  systemd-resolve --flush-caches || true
fi

echo "Secure setup complete. Use https://$DOMAIN:{app_https_port}/"
"""
        return script, "house-fantastico-secure-setup-linux.sh"

    raise HTTPException(status_code=404, detail="Unsupported setup script target OS. Use windows, macos, or linux.")


async def _ensure_chat_voice_room(db: AsyncSession, server: models.ChatServer, channel_name: str) -> str:
    slug_base = _chat_slugify(f"{server.name}-{channel_name}")
    slug = slug_base
    counter = 1
    while True:
        existing = await db.execute(select(models.VoiceRoom).where(models.VoiceRoom.slug == slug))
        if not existing.scalar_one_or_none():
            break
        counter += 1
        slug = f"{slug_base}-{counter}"
    db.add(
        models.VoiceRoom(
            slug=slug,
            name=f"{server.name} / {channel_name}",
            description="Auto-created from chat voice channel",
            created_by_username="chat-admin",
        )
    )
    await db.flush()
    return slug


async def _delete_user_with_related_data(db: AsyncSession, user: models.User) -> None:
    """
    Remove rows that reference a user before deleting the user row.
    """
    # Clear legacy parent pointers for any dependents.
    res = await db.execute(select(models.User).where(models.User.parent_id == user.id))
    dependents = res.scalars().all()
    for dependent in dependents:
        dependent.parent_id = None

    # Null out chess player references to keep game history rows.
    white_games = await db.execute(select(models.ChessGame).where(models.ChessGame.white_player_id == user.id))
    for game in white_games.scalars().all():
        game.white_player_id = None
    black_games = await db.execute(select(models.ChessGame).where(models.ChessGame.black_player_id == user.id))
    for game in black_games.scalars().all():
        game.black_player_id = None

    # Cleanup user-owned rows.
    await db.execute(delete(server_models.PolicyViolation).where(server_models.PolicyViolation.user_id == user.id))
    await db.execute(delete(models.PostReaction).where(models.PostReaction.user_id == user.id))
    await db.execute(
        delete(models.PostReaction).where(
            models.PostReaction.post_id.in_(select(models.Post.id).where(models.Post.author_id == user.id))
        )
    )
    await db.execute(delete(models.Post).where(models.Post.author_id == user.id))
    await db.execute(delete(models.CalendarEvent).where(models.CalendarEvent.user_id == user.id))
    await db.execute(delete(models.ExternalAccount).where(models.ExternalAccount.user_id == user.id))
    await db.execute(delete(models.GameLobby).where(models.GameLobby.user_id == user.id))
    await db.execute(delete(models.ForumPost).where(models.ForumPost.user_id == user.id))
    await db.execute(delete(models.ForumThread).where(models.ForumThread.user_id == user.id))
    await db.execute(delete(models.MailMessage).where(models.MailMessage.sender_user_id == user.id))
    await db.execute(delete(models.MailMessage).where(models.MailMessage.recipient_user_id == user.id))
    await db.execute(delete(models.Following).where(models.Following.local_user_id == user.id))
    await db.execute(delete(models.Follower).where(models.Follower.local_user_id == user.id))
    await db.execute(delete(models.child_custody).where(models.child_custody.c.child_id == user.id))
    await db.execute(delete(models.child_custody).where(models.child_custody.c.parent_id == user.id))
    await db.execute(delete(models.ChatReaction).where(models.ChatReaction.user_id == user.id))
    await db.execute(delete(models.ChatMessage).where(models.ChatMessage.author_user_id == user.id))
    await db.execute(delete(models.ChatEmoji).where(models.ChatEmoji.created_by_user_id == user.id))
    await db.execute(delete(models.ChatChannelPermission).where(models.ChatChannelPermission.member_user_id == user.id))
    await db.execute(delete(models.ChatCategoryPermission).where(models.ChatCategoryPermission.member_user_id == user.id))
    member_res = await db.execute(select(models.ChatServerMember).where(models.ChatServerMember.user_id == user.id))
    member_rows = member_res.scalars().all()
    for member in member_rows:
        await db.execute(delete(models.ChatMemberRole).where(models.ChatMemberRole.member_id == member.id))
    await db.execute(delete(models.ChatServerMember).where(models.ChatServerMember.user_id == user.id))
    owned_servers = await db.execute(select(models.ChatServer).where(models.ChatServer.owner_user_id == user.id))
    for owned_server in owned_servers.scalars().all():
        await db.delete(owned_server)

    await db.delete(user)

@router.get("/dashboard", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db)
):
    """
    Admin policy enforcement dashboard.
    """
    permissions.require_admin(current_user)
    
    # 1. Get Summary Stats
    # Total violations
    result = await db.execute(select(func.count(server_models.PolicyViolation.id)))
    total_violations = result.scalar()
    
    # Blocked count
    result = await db.execute(
        select(func.count(server_models.PolicyViolation.id))
        .where(server_models.PolicyViolation.action_taken == "blocked")
    )
    blocked_count = result.scalar()
    
    # Flagged count
    result = await db.execute(
        select(func.count(server_models.PolicyViolation.id))
        .where(server_models.PolicyViolation.action_taken == "flagged")
    )
    flagged_count = result.scalar()
    
    # 2. Get Top Violating Tags
    result = await db.execute(
        select(
            server_models.PolicyViolation.matched_tag,
            func.count(server_models.PolicyViolation.id).label("count")
        )
        .group_by(server_models.PolicyViolation.matched_tag)
        .order_by(desc("count"))
        .limit(5)
    )
    top_tags = result.all()
    
    # 3. Get Recent Violations
    result = await db.execute(
        select(server_models.PolicyViolation, models.User.username)
        .join(models.User, server_models.PolicyViolation.user_id == models.User.id)
        .order_by(server_models.PolicyViolation.created_at.desc())
        .limit(10)
    )
    recent_violations = result.all()
    
    active_modules = await server_utils.get_active_modules(db)

    return templates.TemplateResponse(
        request=request,
        name="admin_dashboard.html",
        context={
            "user": current_user,
            "stats": {
                "total": total_violations,
                "blocked": blocked_count,
                "flagged": flagged_count
            },
            "top_tags": top_tags,
            "recent_violations": recent_violations,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
            "active_modules": active_modules
        }
    )

@router.get("/settings", response_class=HTMLResponse)
async def server_settings_page(
    request: Request,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db)
):
    """
    Server settings management page (admin only).
    """
    permissions.require_admin(current_user)
    
    server_settings = await server_utils.get_server_settings(db)
    active_modules = await server_utils.get_active_modules(db)
    chat_module_enabled = any((m.name or "").strip().lower() == "chat" for m in active_modules)
    secure_local_domain = (server_settings.secure_local_domain or "").strip().lower()
    suggested_secure_local_domain = secure_local_domain or _suggest_secure_local_domain(server_settings.server_name)
    host_name = (request.url.hostname or "").strip().lower()
    detected_request_ip = server_utils.normalize_secure_local_ip(host_name)
    if not detected_request_ip:
        detected_request_ip = server_utils.normalize_secure_local_ip(server_settings.secure_local_ip)
    app_http_port = (os.getenv("APP_HTTP_PORT", "8001") or "8001").strip()
    app_https_port = (os.getenv("APP_HTTPS_PORT", "8443") or "8443").strip()
    secure_ca_available = server_utils.local_ca_cert_path().exists()
    settings_saved = (request.query_params.get("saved") or "").strip() == "1"
    host_os = _detect_host_os_label()
    
    # Get all users (merged from user management page)
    result = await db.execute(select(models.User))
    users = result.scalars().all()
    join_requests = []
    local_server_id = server_identity.get_or_create_server_id()
    client_connection_info = _build_client_connection_info(
        server_settings=server_settings,
        app_http_port=app_http_port,
        app_https_port=app_https_port,
        detected_request_ip=detected_request_ip,
    )
    client_lan_server_url = (client_connection_info.get("lan_server_url") or "").strip()
    client_external_server_url = (_infer_external_server_url(request, app_https_port, server_settings=server_settings) or "").strip()
    lan_invite_info = dict(client_connection_info)
    lan_invite_info["recommended_server_url"] = client_lan_server_url or (client_connection_info.get("recommended_server_url") or "")
    external_invite_info = dict(client_connection_info)
    external_invite_info["recommended_server_url"] = client_external_server_url or "[configure public domain first]"
    client_invite_text = _render_client_invite_text(local_server_id, lan_invite_info)
    client_invite_text_external = _render_client_invite_text(local_server_id, external_invite_info)
    try:
        req_res = await db.execute(
            select(server_models.JoinRequest)
            .order_by(server_models.JoinRequest.created_at.desc())
            .limit(100)
        )
        join_requests = req_res.scalars().all()
    except Exception:
        join_requests = []
    pending_join_count = sum(1 for r in join_requests if r.status == "pending")
    join_request_created_display = {
        r.id: _format_datetime_utc(r.created_at) for r in join_requests
    }

    password_reset_requests = []
    try:
        pw_res = await db.execute(
            select(server_models.PasswordResetRequest)
            .order_by(server_models.PasswordResetRequest.created_at.desc())
            .limit(100)
        )
        password_reset_requests = pw_res.scalars().all()
    except Exception:
        password_reset_requests = []
    password_reset_created_display = {
        r.id: _format_datetime_utc(r.created_at) for r in password_reset_requests
    }

    return templates.TemplateResponse(
        request=request,
        name="admin_settings.html",
        context={
            "user": current_user,
            "server_settings": server_settings,
            "users": users,
            "join_requests": join_requests,
            "pending_join_count": pending_join_count,
            "join_request_created_display": join_request_created_display,
            "password_reset_requests": password_reset_requests,
            "password_reset_created_display": password_reset_created_display,
            "local_server_id": local_server_id,
            "chat_module_enabled": chat_module_enabled,
            "suggested_secure_local_domain": suggested_secure_local_domain,
            "detected_request_ip": detected_request_ip or "",
            "app_http_port": app_http_port,
            "app_https_port": app_https_port,
            "secure_ca_available": secure_ca_available,
            "settings_saved": settings_saved,
            "host_os": host_os,
            "client_recommended_server_url": client_connection_info.get("recommended_server_url") or "",
            "client_lan_server_url": client_lan_server_url,
            "client_external_server_url": client_external_server_url,
            "client_secure_server_url": client_connection_info.get("secure_url") or "",
            "client_first_run_http_url": client_connection_info.get("first_run_http_url") or "",
            "client_fallback_http_url": client_connection_info.get("fallback_http_url") or "",
            "client_lan_ip": client_connection_info.get("lan_ip") or "",
            "client_secure_domain": client_connection_info.get("secure_domain") or "",
            "client_invite_text": client_invite_text,
            "client_invite_text_external": client_invite_text_external,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
            "active_modules": active_modules
        }
    )

@router.post("/settings/update", name="update_server_settings")
async def update_server_settings(
    request: Request,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    """
    Update server settings (admin only).
    """
    permissions.require_admin(current_user)

    form = await request.form()

    def _optional_text(key: str) -> Optional[str]:
        if key not in form:
            return None
        return str(form.get(key) or "")

    server_name = _optional_text("server_name")
    if server_name is not None:
        server_name = server_name.strip()
        if not server_name:
            server_name = None

    server_description = _optional_text("server_description")
    forbidden_tags = _optional_text("forbidden_tags")
    warning_tags = _optional_text("warning_tags")
    external_join_policy = str(form.get("external_join_policy") or "conditional").strip().lower()
    secure_mode_enabled = _form_bool_value(form.get("secure_mode_enabled")) if "secure_mode_enabled" in form else None
    secure_local_domain_raw = _optional_text("secure_local_domain_raw")
    secure_local_ip = _optional_text("secure_local_ip")
    external_server_url = _optional_text("external_server_url")
    federation_enabled = _form_bool_value(form.get("federation_enabled"))
    allow_external_follows = _form_bool_value(form.get("allow_external_follows"))

    domain_input = (secure_local_domain_raw or "").strip() if secure_local_domain_raw is not None else ""
    ip_input = (secure_local_ip or "").strip() if secure_local_ip is not None else ""
    if domain_input and server_utils.normalize_secure_local_domain(domain_input) is None:
        raise HTTPException(
            status_code=400,
            detail="Invalid Secure Local Domain. Use a hostname like house-fantastico.local",
        )
    if ip_input and server_utils.normalize_secure_local_ip(ip_input) is None:
        raise HTTPException(
            status_code=400,
            detail="Invalid Server LAN IP. Use an IPv4 address (or URL/host:port containing one).",
        )
    
    await server_utils.update_server_settings(
        db=db,
        server_name=server_name,
        server_description=server_description,
        forbidden_tags=forbidden_tags,
        warning_tags=warning_tags,
        federation_enabled=federation_enabled,
        allow_external_follows=allow_external_follows,
        external_join_policy=external_join_policy,
        secure_mode_enabled=secure_mode_enabled,
        secure_local_domain=secure_local_domain_raw,
        secure_local_ip=secure_local_ip,
        external_server_url=external_server_url,
    )

    logger.info(
        "Admin settings saved: secure_mode_enabled=%s secure_local_domain=%s secure_local_ip=%s external_join_policy=%s",
        secure_mode_enabled,
        server_utils.normalize_secure_local_domain(secure_local_domain_raw) if secure_local_domain_raw is not None else "",
        server_utils.normalize_secure_local_ip(secure_local_ip) if secure_local_ip is not None else "",
        external_join_policy,
    )
    print(
        "Admin settings saved:",
        f"secure_mode_enabled={secure_mode_enabled}",
        f"secure_local_domain={server_utils.normalize_secure_local_domain(secure_local_domain_raw) if secure_local_domain_raw is not None else ''}",
        f"secure_local_ip={server_utils.normalize_secure_local_ip(secure_local_ip) if secure_local_ip is not None else ''}",
        f"external_join_policy={external_join_policy}",
    )

    return RedirectResponse(url="/admin/settings?saved=1", status_code=303)


@router.get("/settings/secure-mode/ca", name="download_secure_mode_ca")
async def download_secure_mode_ca(
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
):
    permissions.require_admin(current_user)
    ca_path = server_utils.local_ca_cert_path()
    if not ca_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Local CA certificate not found yet. Start the Podman HTTPS stack once, then retry.",
        )
    return FileResponse(
        path=str(ca_path),
        media_type="application/x-x509-ca-cert",
        filename="house-fantastico-local-ca.crt",
    )


@router.get("/settings/secure-mode/hosts", name="download_secure_mode_hosts")
async def download_secure_mode_hosts(
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    permissions.require_admin(current_user)
    server_settings = await server_utils.get_server_settings(db)
    domain, ip = _resolve_secure_domain_ip_or_400(server_settings)
    hosts_entry = f"{ip} {domain}"
    content = (
        "# House Fantastico secure-mode hosts profile\n"
        "# Add this line to your hosts file on client devices:\n"
        f"{hosts_entry}\n"
    )
    return PlainTextResponse(
        content=content,
        headers={"Content-Disposition": 'attachment; filename="house-fantastico-hosts.txt"'},
    )


@router.get("/settings/secure-mode/setup-script/{target_os}", name="download_secure_mode_setup_script")
async def download_secure_mode_setup_script(
    target_os: str,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    permissions.require_admin(current_user)
    server_settings = await server_utils.get_server_settings(db)
    domain, ip = _resolve_secure_domain_ip_or_400(server_settings)
    app_http_port = (os.getenv("APP_HTTP_PORT", "8001") or "8001").strip()
    app_https_port = (os.getenv("APP_HTTPS_PORT", "8443") or "8443").strip()
    script_body, filename = _render_secure_mode_setup_script(
        target_os,
        domain,
        ip,
        app_http_port,
        app_https_port,
    )
    return PlainTextResponse(
        content=script_body,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/settings/secure-mode/profile", name="download_secure_mode_profile")
async def download_secure_mode_profile(
    request: Request,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    permissions.require_admin(current_user)
    server_settings = await server_utils.get_server_settings(db)
    domain = server_utils.normalize_secure_local_domain(server_settings.secure_local_domain)
    ip = server_utils.normalize_secure_local_ip(server_settings.secure_local_ip)
    http_port = (os.getenv("APP_HTTP_PORT", "8001") or "8001").strip()
    https_port = (os.getenv("APP_HTTPS_PORT", "8443") or "8443").strip()
    local_server_id = server_identity.get_or_create_server_id()

    profile = {
        "server_id": local_server_id,
        "server_name": server_settings.server_name,
        "secure_mode_enabled": bool(server_settings.secure_mode_enabled),
        "secure_local_domain": domain,
        "secure_local_ip": ip,
        "first_run_http_url": f"http://{ip}:{http_port}" if ip else None,
        "secure_url": f"https://{domain}:{https_port}" if domain else None,
        "ca_certificate_url": str(request.url_for("download_secure_mode_ca")),
        "hosts_entry": f"{ip} {domain}" if ip and domain else None,
    }
    return JSONResponse(
        content=profile,
        headers={"Content-Disposition": 'attachment; filename="house-fantastico-connect-profile.json"'},
    )


@router.get("/settings/client-invite", name="download_client_invite")
async def download_client_invite(
    request: Request,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    permissions.require_admin(current_user)
    server_settings = await server_utils.get_server_settings(db)
    app_http_port = (os.getenv("APP_HTTP_PORT", "8001") or "8001").strip()
    app_https_port = (os.getenv("APP_HTTPS_PORT", "8443") or "8443").strip()
    host_name = (request.url.hostname or "").strip().lower()
    detected_request_ip = server_utils.normalize_secure_local_ip(host_name)
    if not detected_request_ip:
        detected_request_ip = server_utils.normalize_secure_local_ip(server_settings.secure_local_ip)
    connection_info = _build_client_connection_info(
        server_settings=server_settings,
        app_http_port=app_http_port,
        app_https_port=app_https_port,
        detected_request_ip=detected_request_ip,
    )
    local_server_id = server_identity.get_or_create_server_id()
    content = _render_client_invite_text(local_server_id, connection_info)
    return PlainTextResponse(
        content=content + "\n",
        headers={"Content-Disposition": 'attachment; filename="house-fantastico-client-invite.txt"'},
    )


@router.get("/settings/client-seed", name="download_client_seed")
async def download_client_seed(
    request: Request,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    permissions.require_admin(current_user)
    server_settings = await server_utils.get_server_settings(db)
    app_http_port = (os.getenv("APP_HTTP_PORT", "8001") or "8001").strip()
    app_https_port = (os.getenv("APP_HTTPS_PORT", "8443") or "8443").strip()
    host_name = (request.url.hostname or "").strip().lower()
    detected_request_ip = server_utils.normalize_secure_local_ip(host_name)
    if not detected_request_ip:
        detected_request_ip = server_utils.normalize_secure_local_ip(server_settings.secure_local_ip)

    connection_info = _build_client_connection_info(
        server_settings=server_settings,
        app_http_port=app_http_port,
        app_https_port=app_https_port,
        detected_request_ip=detected_request_ip,
    )
    local_server_id = server_identity.get_or_create_server_id()
    lan_server_url = (connection_info.get("lan_server_url") or "").strip()
    seed = _build_client_seed_config(
        local_server_id,
        connection_info,
        server_url_override=lan_server_url,
        audience="lan",
    )
    return JSONResponse(
        content=seed,
        headers={"Content-Disposition": 'attachment; filename="house-fantastico-client-seed.json"'},
    )


@router.get("/settings/client-seed/external", name="download_client_seed_external")
async def download_client_seed_external(
    request: Request,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    permissions.require_admin(current_user)
    server_settings = await server_utils.get_server_settings(db)
    app_http_port = (os.getenv("APP_HTTP_PORT", "8001") or "8001").strip()
    app_https_port = (os.getenv("APP_HTTPS_PORT", "8443") or "8443").strip()
    host_name = (request.url.hostname or "").strip().lower()
    detected_request_ip = server_utils.normalize_secure_local_ip(host_name)
    if not detected_request_ip:
        detected_request_ip = server_utils.normalize_secure_local_ip(server_settings.secure_local_ip)

    connection_info = _build_client_connection_info(
        server_settings=server_settings,
        app_http_port=app_http_port,
        app_https_port=app_https_port,
        detected_request_ip=detected_request_ip,
    )
    external_server_url = (_infer_external_server_url(request, app_https_port, server_settings=server_settings) or "").strip()
    if not external_server_url:
        raise HTTPException(
            status_code=400,
            detail="Off-network URL is not configured. Set 'External / Public Server URL' in Admin Settings > Federation Settings.",
        )
    local_server_id = server_identity.get_or_create_server_id()
    seed = _build_client_seed_config(
        local_server_id,
        connection_info,
        server_url_override=external_server_url,
        audience="external",
    )
    return JSONResponse(
        content=seed,
        headers={"Content-Disposition": 'attachment; filename="homepage-client-seed-external.json"'},
    )


@router.get("/settings/client-package/windows", name="download_windows_client_package")
@router.get("/settings/client-package/windows/lan", name="download_windows_client_package_lan")
async def download_windows_client_package(
    request: Request,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    permissions.require_admin(current_user)
    from ..client_packager import prebuilt_path
    _pb = prebuilt_path("windows", "lan")
    if _pb.exists():
        from fastapi.responses import FileResponse
        return FileResponse(_pb, filename=_pb.name, media_type="application/zip")
    server_settings = await server_utils.get_server_settings(db)
    app_http_port = (os.getenv("APP_HTTP_PORT", "8001") or "8001").strip()
    app_https_port = (os.getenv("APP_HTTPS_PORT", "8443") or "8443").strip()
    host_name = (request.url.hostname or "").strip().lower()
    detected_request_ip = server_utils.normalize_secure_local_ip(host_name)
    if not detected_request_ip:
        detected_request_ip = server_utils.normalize_secure_local_ip(server_settings.secure_local_ip)

    connection_info = _build_client_connection_info(
        server_settings=server_settings,
        app_http_port=app_http_port,
        app_https_port=app_https_port,
        detected_request_ip=detected_request_ip,
    )
    local_server_id = server_identity.get_or_create_server_id()
    lan_server_url = (connection_info.get("lan_server_url") or "").strip()
    lan_info = dict(connection_info)
    lan_info["recommended_server_url"] = lan_server_url or (connection_info.get("recommended_server_url") or "")
    seed = _build_client_seed_config(
        local_server_id,
        connection_info,
        server_url_override=lan_server_url,
        audience="lan",
    )
    invite_text = _render_client_invite_text(local_server_id, lan_info)
    return _build_windows_client_package_response(seed, invite_text, "LAN")


@router.get("/settings/client-package/windows/external", name="download_windows_client_package_external")
async def download_windows_client_package_external(
    request: Request,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    permissions.require_admin(current_user)
    from ..client_packager import prebuilt_path
    _pb = prebuilt_path("windows", "external")
    if _pb.exists():
        from fastapi.responses import FileResponse
        return FileResponse(_pb, filename=_pb.name, media_type="application/zip")
    server_settings = await server_utils.get_server_settings(db)
    app_http_port = (os.getenv("APP_HTTP_PORT", "8001") or "8001").strip()
    app_https_port = (os.getenv("APP_HTTPS_PORT", "8443") or "8443").strip()
    host_name = (request.url.hostname or "").strip().lower()
    detected_request_ip = server_utils.normalize_secure_local_ip(host_name)
    if not detected_request_ip:
        detected_request_ip = server_utils.normalize_secure_local_ip(server_settings.secure_local_ip)

    connection_info = _build_client_connection_info(
        server_settings=server_settings,
        app_http_port=app_http_port,
        app_https_port=app_https_port,
        detected_request_ip=detected_request_ip,
    )
    external_server_url = (_infer_external_server_url(request, app_https_port, server_settings=server_settings) or "").strip()
    if not external_server_url:
        raise HTTPException(
            status_code=400,
            detail="Off-network URL is not configured. Set 'External / Public Server URL' in Admin Settings > Federation Settings.",
        )
    local_server_id = server_identity.get_or_create_server_id()
    external_info = dict(connection_info)
    external_info["recommended_server_url"] = external_server_url
    seed = _build_client_seed_config(
        local_server_id,
        connection_info,
        server_url_override=external_server_url,
        audience="external",
    )
    invite_text = _render_client_invite_text(local_server_id, external_info)
    return _build_windows_client_package_response(seed, invite_text, "Off-network")


async def _build_portable_package_args(
    request: Request,
    current_user: models.User,
    db: AsyncSession,
    audience: str,
) -> tuple[dict, str]:
    """Shared logic for Linux/Mac package endpoints. Returns (seed, invite_text)."""
    permissions.require_admin(current_user)
    server_settings = await server_utils.get_server_settings(db)
    app_http_port = (os.getenv("APP_HTTP_PORT", "8001") or "8001").strip()
    app_https_port = (os.getenv("APP_HTTPS_PORT", "8443") or "8443").strip()
    host_name = (request.url.hostname or "").strip().lower()
    detected_request_ip = server_utils.normalize_secure_local_ip(host_name)
    if not detected_request_ip:
        detected_request_ip = server_utils.normalize_secure_local_ip(server_settings.secure_local_ip)
    connection_info = _build_client_connection_info(
        server_settings=server_settings,
        app_http_port=app_http_port,
        app_https_port=app_https_port,
        detected_request_ip=detected_request_ip,
    )
    local_server_id = server_identity.get_or_create_server_id()
    if audience == "external":
        server_url = (_infer_external_server_url(request, app_https_port, server_settings=server_settings) or "").strip()
        if not server_url:
            raise HTTPException(
                status_code=400,
                detail="Off-network URL is not configured. Set 'External / Public Server URL' in Admin Settings > Federation Settings.",
            )
        info = dict(connection_info)
        info["recommended_server_url"] = server_url
    else:
        server_url = (connection_info.get("lan_server_url") or "").strip()
        info = dict(connection_info)
        info["recommended_server_url"] = server_url or (connection_info.get("recommended_server_url") or "")
    seed = _build_client_seed_config(local_server_id, connection_info, server_url_override=server_url, audience=audience)
    invite_text = _render_client_invite_text(local_server_id, info)
    return seed, invite_text


@router.get("/settings/client-package/linux/lan", name="download_linux_client_package_lan")
async def download_linux_client_package_lan(
    request: Request,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    from ..client_packager import prebuilt_path
    _pb = prebuilt_path("linux", "lan")
    if _pb.exists():
        permissions.require_admin(current_user)
        from fastapi.responses import FileResponse
        return FileResponse(_pb, filename=_pb.name, media_type="application/zip")
    seed, invite_text = await _build_portable_package_args(request, current_user, db, "lan")
    return _build_linux_client_package_response(seed, invite_text, "LAN")


@router.get("/settings/client-package/linux/external", name="download_linux_client_package_external")
async def download_linux_client_package_external(
    request: Request,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    from ..client_packager import prebuilt_path
    _pb = prebuilt_path("linux", "external")
    if _pb.exists():
        permissions.require_admin(current_user)
        from fastapi.responses import FileResponse
        return FileResponse(_pb, filename=_pb.name, media_type="application/zip")
    seed, invite_text = await _build_portable_package_args(request, current_user, db, "external")
    return _build_linux_client_package_response(seed, invite_text, "Off-network")


@router.get("/settings/client-package/macos/lan", name="download_macos_client_package_lan")
async def download_macos_client_package_lan(
    request: Request,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    from ..client_packager import prebuilt_path
    _pb = prebuilt_path("macos", "lan")
    if _pb.exists():
        permissions.require_admin(current_user)
        from fastapi.responses import FileResponse
        return FileResponse(_pb, filename=_pb.name, media_type="application/zip")
    seed, invite_text = await _build_portable_package_args(request, current_user, db, "lan")
    return _build_macos_client_package_response(seed, invite_text, "LAN")


@router.get("/settings/client-package/macos/external", name="download_macos_client_package_external")
async def download_macos_client_package_external(
    request: Request,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    from ..client_packager import prebuilt_path
    _pb = prebuilt_path("macos", "external")
    if _pb.exists():
        permissions.require_admin(current_user)
        from fastapi.responses import FileResponse
        return FileResponse(_pb, filename=_pb.name, media_type="application/zip")
    seed, invite_text = await _build_portable_package_args(request, current_user, db, "external")
    return _build_macos_client_package_response(seed, invite_text, "Off-network")


@router.get("/modules", response_class=HTMLResponse)
async def manage_modules(
    request: Request,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db)
):
    """
    Admin page to enable/disable platform modules.
    """
    permissions.require_admin(current_user)
    
    modules = await server_utils.get_all_modules(db)
    active_modules = await server_utils.get_active_modules(db)

    # Read which modules have their routers actually loaded
    loaded_modules = settings.loaded_modules

    return templates.TemplateResponse(
        request=request,
        name="admin_modules.html",
        context={
            "user": current_user,
            "modules": modules,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
            "active_modules": active_modules,
            "loaded_modules": loaded_modules,
        }
    )

@router.post("/modules/{module_id}/toggle")
async def toggle_module(
    module_id: int,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db)
):
    """
    Toggle a module's enabled status.
    """
    permissions.require_admin(current_user)
    
    result = await db.execute(select(server_models.PlatformModule).where(server_models.PlatformModule.id == module_id))
    module = result.scalar_one_or_none()
    
    if module:
        manager = ModuleManager(db)
        try:
            if module.is_enabled == 1:
                await manager.disable(module.name)
            else:
                await manager.enable(module.name)
        except ModuleManagerError as e:
            raise HTTPException(status_code=500, detail=f"Module toggle failed: {e}")
    
    return RedirectResponse(url="/admin/modules", status_code=303)


@router.post("/modules/save-loaded")
async def save_loaded_modules(
    request: Request,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
):
    """
    Save which optional modules should have their routers loaded on next restart.
    Writes data/enabled_modules.json.
    """
    import json as _json
    permissions.require_admin(current_user)
    form = await request.form()
    # Checkbox values: each checked module sends its name
    all_optional = {"calendar", "games", "wikipedia", "discussions", "voice", "mail", "chat"}
    enabled = [m for m in all_optional if form.get(f"load_{m}")]
    modules_file = settings.BASE_DIR / "data" / "enabled_modules.json"
    modules_file.parent.mkdir(parents=True, exist_ok=True)
    modules_file.write_text(_json.dumps(sorted(enabled), indent=2) + "\n", encoding="utf-8")
    return RedirectResponse(url="/admin/modules", status_code=303)


@router.post("/chat/servers/create")
async def admin_create_chat_server(
    request: Request,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    permissions.require_admin(current_user)
    active_modules = await server_utils.get_active_modules(db)
    if not any((m.name or "").strip().lower() == "chat" for m in active_modules):
        raise HTTPException(status_code=400, detail="Chat module is not enabled")

    form = await request.form()
    name = (form.get("chat_server_name") or "").strip()
    description = (form.get("chat_server_description") or "").strip()
    if not name:
        return RedirectResponse(url="/admin/settings", status_code=303)

    server = models.ChatServer(
        name=name,
        description=description or None,
        owner_user_id=current_user.id,
    )
    db.add(server)
    await db.flush()

    everyone = models.ChatRole(
        server_id=server.id,
        name="@everyone",
        color="#64748b",
        position=0,
        permissions=DEFAULT_MEMBER_PERMISSIONS,
        is_default=True,
    )
    owner_role = models.ChatRole(
        server_id=server.id,
        name="Owner",
        color="#f59e0b",
        position=100,
        permissions=ALL_PERMISSIONS,
        is_default=False,
    )
    db.add(everyone)
    db.add(owner_role)
    await db.flush()

    member = models.ChatServerMember(server_id=server.id, user_id=current_user.id)
    db.add(member)
    await db.flush()
    db.add(models.ChatMemberRole(member_id=member.id, role_id=owner_role.id))

    default_group = models.ChatCategory(server_id=server.id, name="General", position=0)
    db.add(default_group)
    await db.flush()

    db.add(
        models.ChatChannel(
            server_id=server.id,
            category_id=default_group.id,
            name="general",
            slug="general",
            channel_type="text",
            topic="Default text channel",
            position=0,
        )
    )
    lobby_voice_slug = await _ensure_chat_voice_room(db, server, "Lobby")
    db.add(
        models.ChatChannel(
            server_id=server.id,
            category_id=default_group.id,
            name="Lobby",
            slug="lobby",
            channel_type="voice",
            topic="Default voice channel",
            linked_voice_room_slug=lobby_voice_slug,
            position=1,
        )
    )

    await db.commit()
    return RedirectResponse(url="/admin/chat-settings", status_code=303)


@router.post("/chat/servers/{server_id}/groups/create")
async def admin_create_chat_group(
    server_id: int,
    request: Request,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    permissions.require_admin(current_user)
    server_res = await db.execute(select(models.ChatServer).where(models.ChatServer.id == server_id))
    server = server_res.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="Chat server not found")

    form = await request.form()
    group_name = (form.get(f"chat_group_name_{server_id}") or "").strip()
    if not group_name:
        return RedirectResponse(url="/admin/chat-settings", status_code=303)

    existing_res = await db.execute(
        select(models.ChatCategory).where(
            and_(models.ChatCategory.server_id == server.id, models.ChatCategory.name == group_name)
        )
    )
    if existing_res.scalar_one_or_none():
        return RedirectResponse(url="/admin/chat-settings", status_code=303)

    count_res = await db.execute(select(func.count(models.ChatCategory.id)).where(models.ChatCategory.server_id == server.id))
    next_position = int(count_res.scalar() or 0)
    db.add(models.ChatCategory(server_id=server.id, name=group_name, position=next_position))
    await db.commit()
    return RedirectResponse(url="/admin/chat-settings", status_code=303)


@router.post("/chat/servers/{server_id}/roles/create")
async def admin_create_chat_role(
    server_id: int,
    request: Request,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    permissions.require_admin(current_user)
    server_res = await db.execute(select(models.ChatServer).where(models.ChatServer.id == server_id))
    server = server_res.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="Chat server not found")

    form = await request.form()
    role_name = (form.get(f"chat_role_name_{server_id}") or "").strip()
    role_color = (form.get(f"chat_role_color_{server_id}") or "#64748b").strip() or "#64748b"
    if not role_name:
        return RedirectResponse(url="/admin/chat-settings", status_code=303)

    existing_res = await db.execute(
        select(models.ChatRole).where(
            and_(models.ChatRole.server_id == server.id, models.ChatRole.name == role_name)
        )
    )
    if existing_res.scalar_one_or_none():
        return RedirectResponse(url="/admin/chat-settings", status_code=303)

    role_count_res = await db.execute(select(func.count(models.ChatRole.id)).where(models.ChatRole.server_id == server.id))
    position = int(role_count_res.scalar() or 0) + 1
    db.add(
        models.ChatRole(
            server_id=server.id,
            name=role_name,
            color=role_color,
            position=position,
            permissions=DEFAULT_MEMBER_PERMISSIONS,
            is_default=False,
        )
    )
    await db.commit()
    return RedirectResponse(url="/admin/chat-settings", status_code=303)


@router.get("/chat-settings", response_class=HTMLResponse)
async def chat_settings_page(
    request: Request,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    permissions.require_admin(current_user)
    active_modules = await server_utils.get_active_modules(db)
    chat_module_enabled = any((m.name or "").strip().lower() == "chat" for m in active_modules)
    if not chat_module_enabled:
        return RedirectResponse(url="/admin/settings", status_code=303)
    chat_error = (request.query_params.get("chat_error") or "").strip().lower()

    servers_res = await db.execute(select(models.ChatServer).order_by(models.ChatServer.created_at.asc()))
    chat_servers = servers_res.scalars().all()

    chat_groups_by_server: dict[int, list[models.ChatCategory]] = {}
    chat_channels_by_server: dict[int, list[models.ChatChannel]] = {}
    chat_roles_by_server: dict[int, list[models.ChatRole]] = {}
    group_overwrites_by_group: dict[int, list[models.ChatCategoryPermission]] = {}
    overwrite_labels: dict[int, dict[str, list[str]]] = {}
    for server in chat_servers:
        groups_res = await db.execute(
            select(models.ChatCategory)
            .where(models.ChatCategory.server_id == server.id)
            .order_by(models.ChatCategory.position.asc(), models.ChatCategory.id.asc())
        )
        groups = groups_res.scalars().all()
        chat_groups_by_server[server.id] = groups

        channels_res = await db.execute(
            select(models.ChatChannel)
            .where(models.ChatChannel.server_id == server.id)
            .order_by(models.ChatChannel.position.asc(), models.ChatChannel.id.asc())
        )
        chat_channels_by_server[server.id] = channels_res.scalars().all()

        roles_res = await db.execute(
            select(models.ChatRole)
            .where(models.ChatRole.server_id == server.id)
            .order_by(models.ChatRole.position.desc(), models.ChatRole.id.asc())
        )
        chat_roles_by_server[server.id] = roles_res.scalars().all()

        for group in groups:
            ow_res = await db.execute(
                select(models.ChatCategoryPermission)
                .where(models.ChatCategoryPermission.category_id == group.id)
                .order_by(models.ChatCategoryPermission.id.asc())
            )
            group_overwrites_by_group[group.id] = ow_res.scalars().all()
            for ow in group_overwrites_by_group[group.id]:
                allow_names = [k for k, v in CHAT_PERMISSION_BITS.items() if int(ow.allow_bits or 0) & v]
                deny_names = [k for k, v in CHAT_PERMISSION_BITS.items() if int(ow.deny_bits or 0) & v]
                overwrite_labels[ow.id] = {"allow": allow_names, "deny": deny_names}

    return templates.TemplateResponse(
        request=request,
        name="admin_chat_settings.html",
        context={
            "user": current_user,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
            "active_modules": active_modules,
            "chat_servers": chat_servers,
            "chat_groups_by_server": chat_groups_by_server,
            "chat_channels_by_server": chat_channels_by_server,
            "chat_roles_by_server": chat_roles_by_server,
            "group_overwrites_by_group": group_overwrites_by_group,
            "overwrite_labels": overwrite_labels,
            "chat_permission_bits": CHAT_PERMISSION_BITS,
            "chat_error": chat_error,
        },
    )


@router.post("/chat/servers/{server_id}/channels/create")
async def admin_create_chat_channel(
    server_id: int,
    request: Request,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    permissions.require_admin(current_user)
    server_res = await db.execute(select(models.ChatServer).where(models.ChatServer.id == server_id))
    server = server_res.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="Chat server not found")

    form = await request.form()
    channel_name = (form.get(f"chat_channel_name_{server_id}") or "").strip()
    if not channel_name:
        return RedirectResponse(url="/admin/chat-settings", status_code=303)

    channel_type = (form.get(f"chat_channel_type_{server_id}") or "text").strip().lower()
    if channel_type not in {"text", "voice"}:
        channel_type = "text"
    category_name = (form.get(f"chat_category_name_{server_id}") or "").strip()
    group_id_raw = (form.get(f"chat_group_id_{server_id}") or "").strip()
    topic = (form.get(f"chat_topic_{server_id}") or "").strip()

    category_id = None
    if group_id_raw:
        try:
            group_id = int(group_id_raw)
            cat_by_id = await db.execute(
                select(models.ChatCategory).where(
                    and_(models.ChatCategory.server_id == server.id, models.ChatCategory.id == group_id)
                )
            )
            by_id = cat_by_id.scalar_one_or_none()
            if by_id:
                category_id = by_id.id
        except ValueError:
            category_id = None
    if category_name:
        cat_res = await db.execute(
            select(models.ChatCategory).where(
                and_(models.ChatCategory.server_id == server.id, models.ChatCategory.name == category_name)
            )
        )
        category = cat_res.scalar_one_or_none()
        if not category:
            category = models.ChatCategory(server_id=server.id, name=category_name, position=0)
            db.add(category)
            await db.flush()
        category_id = category.id

    if category_id is None:
        return RedirectResponse(url="/admin/chat-settings?chat_error=group_required", status_code=303)

    channels_res = await db.execute(select(models.ChatChannel).where(models.ChatChannel.server_id == server.id))
    next_position = len(channels_res.scalars().all())
    slug_base = _chat_slugify(channel_name)
    slug = slug_base
    i = 1
    while True:
        exists_res = await db.execute(
            select(models.ChatChannel).where(
                and_(models.ChatChannel.server_id == server.id, models.ChatChannel.slug == slug)
            )
        )
        if not exists_res.scalar_one_or_none():
            break
        i += 1
        slug = f"{slug_base}-{i}"

    linked_voice_room_slug = None
    if channel_type == "voice":
        linked_voice_room_slug = await _ensure_chat_voice_room(db, server, channel_name)

    db.add(
        models.ChatChannel(
            server_id=server.id,
            category_id=category_id,
            name=channel_name,
            slug=slug,
            channel_type=channel_type,
            topic=topic or None,
            linked_voice_room_slug=linked_voice_room_slug,
            position=next_position,
        )
    )
    await db.commit()
    return RedirectResponse(url="/admin/chat-settings", status_code=303)


@router.post("/chat/channels/{channel_id}/assign-group")
async def admin_assign_channel_group(
    channel_id: int,
    group_id: Annotated[int, Form(...)],
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    permissions.require_admin(current_user)
    channel_res = await db.execute(select(models.ChatChannel).where(models.ChatChannel.id == channel_id))
    channel = channel_res.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    group_res = await db.execute(
        select(models.ChatCategory).where(
            and_(models.ChatCategory.id == group_id, models.ChatCategory.server_id == channel.server_id)
        )
    )
    group = group_res.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Channel group not found")

    channel.category_id = group.id
    await db.commit()
    return RedirectResponse(url="/admin/chat-settings", status_code=303)


@router.post("/chat/groups/{group_id}/permissions/upsert")
async def admin_upsert_group_permissions(
    group_id: int,
    request: Request,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    permissions.require_admin(current_user)
    group_res = await db.execute(select(models.ChatCategory).where(models.ChatCategory.id == group_id))
    group = group_res.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Channel group not found")

    form = await request.form()
    role_id_raw = (form.get("role_id") or "").strip()
    if not role_id_raw:
        return RedirectResponse(url="/admin/chat-settings", status_code=303)

    try:
        role_id = int(role_id_raw)
    except ValueError:
        return RedirectResponse(url="/admin/chat-settings", status_code=303)

    role_res = await db.execute(
        select(models.ChatRole).where(and_(models.ChatRole.id == role_id, models.ChatRole.server_id == group.server_id))
    )
    role = role_res.scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    allow_bits = _chat_permission_bits_from_form(form, "allow")
    deny_bits = _chat_permission_bits_from_form(form, "deny")
    deny_bits &= ~allow_bits

    existing_res = await db.execute(
        select(models.ChatCategoryPermission).where(
            and_(
                models.ChatCategoryPermission.category_id == group.id,
                models.ChatCategoryPermission.role_id == role.id,
                models.ChatCategoryPermission.member_user_id.is_(None),
            )
        )
    )
    row = existing_res.scalar_one_or_none()
    if row:
        row.allow_bits = allow_bits
        row.deny_bits = deny_bits
    else:
        db.add(
            models.ChatCategoryPermission(
                category_id=group.id,
                role_id=role.id,
                allow_bits=allow_bits,
                deny_bits=deny_bits,
            )
        )
    await db.commit()
    return RedirectResponse(url="/admin/chat-settings", status_code=303)


@router.post("/join-requests/{request_id}/approve")
async def approve_join_request(
    request: Request,
    request_id: int,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    permissions.require_admin(current_user)
    result = await db.execute(
        select(server_models.JoinRequest).where(server_models.JoinRequest.id == request_id)
    )
    req = result.scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail="Join request not found")

    try:
        # Provision a local user account exactly once when the join request is approved.
        if req.consumed_at is None:
            username = await _allocate_external_username(db, req.requested_username, req.peer_server_id)
            display_name = (req.requested_display_name or req.requested_username or username or "").strip() or username

            # If the client supplied a pre-hashed password, use it directly.
            # Otherwise fall back to a random temporary password.
            client_hash = (req.allocated_password or "").strip()
            has_client_password = client_hash.startswith("$argon2")

            if has_client_password:
                # Dummy password for schema; the pre-hashed value is used instead.
                user_in = schemas.UserCreate(
                    username=username,
                    password="__placeholder__",
                    display_name=display_name,
                )
                created_user = await crud_users.create_user_no_commit(
                    db=db, user=user_in, pre_hashed_password=client_hash,
                )
            else:
                temp_password = secrets.token_urlsafe(16)
                user_in = schemas.UserCreate(
                    username=username,
                    password=temp_password,
                    display_name=display_name,
                )
                created_user = await crud_users.create_user_no_commit(db=db, user=user_in)
                req.allocated_password = temp_password  # store for admin display

            created_user.role = permissions.UserRole.ADULT
            created_user.can_post = True
            created_user.can_follow_external = True
            req.requested_username = username
            req.consumed_at = func.now()

        req.status = "approved"
        req.approved_by_user_id = current_user.id
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    # Determine what to show the admin.
    allocated_username = req.requested_username or "(unknown)"
    client_supplied_password = (req.allocated_password or "").startswith("$argon2")
    allocated_password = None if client_supplied_password else (req.allocated_password or "(unknown)")
    return templates.TemplateResponse(
        request=request,
        name="admin_join_approved.html",
        context={
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
            "user": current_user,
            "allocated_username": allocated_username,
            "allocated_password": allocated_password,
            "client_supplied_password": client_supplied_password,
            "request_id": req.id,
        },
    )


@router.post("/join-requests/{request_id}/reject")
async def reject_join_request(
    request_id: int,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    permissions.require_admin(current_user)
    result = await db.execute(
        select(server_models.JoinRequest).where(server_models.JoinRequest.id == request_id)
    )
    req = result.scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail="Join request not found")
    req.status = "rejected"
    req.approved_by_user_id = current_user.id
    await db.commit()
    return RedirectResponse(url="/admin/settings", status_code=303)


@router.post("/join-requests/{request_id}/delete", name="delete_join_request")
async def delete_join_request(
    request_id: int,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    permissions.require_admin(current_user)
    result = await db.execute(
        select(server_models.JoinRequest).where(server_models.JoinRequest.id == request_id)
    )
    req = result.scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail="Join request not found")
    await db.delete(req)
    await db.commit()
    return JSONResponse({"ok": True, "request_id": request_id})


@router.post("/password-reset-requests/{request_id}/approve")
async def approve_password_reset_request(
    request_id: int,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    """Approve a password reset request — updates the user's password_hash."""
    permissions.require_admin(current_user)
    result = await db.execute(
        select(server_models.PasswordResetRequest).where(
            server_models.PasswordResetRequest.id == request_id
        )
    )
    req = result.scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail="Password reset request not found")

    # Find the user and update their password hash
    user_result = await db.execute(
        select(models.User).where(models.User.username == req.username)
    )
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail=f"User '{req.username}' not found")

    user.password_hash = req.new_password_hash
    req.status = "approved"
    req.approved_by_user_id = current_user.id
    await db.commit()
    return RedirectResponse(url="/admin/settings", status_code=303)


@router.post("/password-reset-requests/{request_id}/reject")
async def reject_password_reset_request(
    request_id: int,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    permissions.require_admin(current_user)
    result = await db.execute(
        select(server_models.PasswordResetRequest).where(
            server_models.PasswordResetRequest.id == request_id
        )
    )
    req = result.scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail="Password reset request not found")
    req.status = "rejected"
    req.approved_by_user_id = current_user.id
    await db.commit()
    return RedirectResponse(url="/admin/settings", status_code=303)


@router.post("/password-reset-requests/{request_id}/delete", name="delete_password_reset_request")
async def delete_password_reset_request(
    request_id: int,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    permissions.require_admin(current_user)
    result = await db.execute(
        select(server_models.PasswordResetRequest).where(
            server_models.PasswordResetRequest.id == request_id
        )
    )
    req = result.scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail="Password reset request not found")
    await db.delete(req)
    await db.commit()
    return JSONResponse({"ok": True, "request_id": request_id})



@router.get("/family", response_class=HTMLResponse)
async def manage_family(
    request: Request,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db)
):
    """
    Adult/admin page to manage child accounts.
    """
    permissions.require_parent_or_admin(current_user)

    children: list[models.User] = []
    guardians: list[models.User] = []
    child_custodians: dict[int, list[models.User]] = {}

    try:
        if current_user.role == permissions.UserRole.ADMIN:
            children_stmt = (
                select(models.User)
                .where(models.User.role == permissions.UserRole.CHILD)
                .order_by(models.User.username.asc())
            )
            result = await db.execute(children_stmt)
            children = result.scalars().all()
        else:
            try:
                children_stmt = (
                    select(models.User)
                    .where(models.User.role == permissions.UserRole.CHILD)
                    .outerjoin(models.child_custody, models.child_custody.c.child_id == models.User.id)
                    .where(
                        or_(
                            models.User.parent_id == current_user.id,
                            models.child_custody.c.parent_id == current_user.id,
                        )
                    )
                    .order_by(models.User.username.asc())
                )
                result = await db.execute(children_stmt)
                children = result.scalars().unique().all()
            except OperationalError:
                # Backward compatibility for databases that don't have child_custody yet.
                legacy_stmt = (
                    select(models.User)
                    .where(
                        models.User.role == permissions.UserRole.CHILD,
                        models.User.parent_id == current_user.id,
                    )
                    .order_by(models.User.username.asc())
                )
                result = await db.execute(legacy_stmt)
                children = result.scalars().all()

        guardians = await _get_guardian_users(db)
        child_custodians = {child.id: [] for child in children}
        child_ids = [child.id for child in children]

        if child_ids:
            try:
                custody_stmt = (
                    select(models.child_custody.c.child_id, models.User)
                    .join(models.User, models.User.id == models.child_custody.c.parent_id)
                    .where(models.child_custody.c.child_id.in_(child_ids))
                    .order_by(models.User.username.asc())
                )
                custody_res = await db.execute(custody_stmt)
                for child_id, guardian in custody_res.all():
                    child_custodians[child_id].append(guardian)
            except Exception:
                pass

        legacy_parent_ids = {child.parent_id for child in children if child.parent_id}
        legacy_parent_map: dict[int, models.User] = {}
        if legacy_parent_ids:
            legacy_stmt = select(models.User).where(models.User.id.in_(legacy_parent_ids))
            legacy_res = await db.execute(legacy_stmt)
            for guardian in legacy_res.scalars().all():
                legacy_parent_map[guardian.id] = guardian

        for child in children:
            if child.parent_id and child.parent_id in legacy_parent_map:
                existing_ids = {guardian.id for guardian in child_custodians[child.id]}
                if child.parent_id not in existing_ids:
                    child_custodians[child.id].append(legacy_parent_map[child.parent_id])
    except Exception:
        # Last-resort fallback: keep page operational even if advanced family schema is out of sync.
        legacy_stmt = (
            select(models.User)
            .where(models.User.role == permissions.UserRole.CHILD)
            .order_by(models.User.username.asc())
        )
        result = await db.execute(legacy_stmt)
        children = result.scalars().all()
        guardians = [current_user]
        child_custodians = {child.id: [] for child in children}

    return templates.TemplateResponse(
        request=request,
        name="family_management.html",
        context={
            "user": current_user,
            "children": children,
            "guardians": guardians,
            "child_custodians": child_custodians,
            "is_admin_view": current_user.role == permissions.UserRole.ADMIN,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME
        }
    )

@router.post("/users/{user_id}/update-role")
async def update_user_role(
    user_id: int,
    role: Annotated[str, Form()],
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db)
):
    """
    Update a user's role (admin only).
    """
    permissions.require_admin(current_user)
    
    result = await db.execute(select(models.User).where(models.User.id == user_id))
    target_user = result.scalar_one_or_none()
    
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    normalized_role = permissions.normalize_role_value(role)
    if normalized_role not in [permissions.UserRole.ADMIN, permissions.UserRole.ADULT, permissions.UserRole.CHILD]:
        raise HTTPException(status_code=400, detail="Invalid role")
    
    target_user.role = normalized_role
    await db.commit()
    
    return RedirectResponse(url="/admin/settings", status_code=303)

@router.post("/users/{user_id}/update-permissions")
async def update_child_permissions(
    user_id: int,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    content_filter_level: Annotated[str, Form()],
    db: AsyncSession = Depends(database.get_db),
    can_post: Annotated[bool, Form()] = False,
    can_follow_external: Annotated[bool, Form()] = False,
    max_daily_screen_time: Annotated[Optional[int], Form()] = None
):
    """
    Update a child's permissions (adult or admin).
    """
    result = await db.execute(select(models.User).where(models.User.id == user_id))
    target_user = result.scalar_one_or_none()
    
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    can_manage = await _is_child_manager(db, current_user, target_user)
    if not can_manage:
        raise HTTPException(status_code=403, detail="You do not have permission to manage this user")
    
    # Update permissions
    target_user.content_filter_level = content_filter_level
    target_user.can_post = can_post
    target_user.can_follow_external = can_follow_external
    target_user.max_daily_screen_time = max_daily_screen_time
    
    await db.commit()
    
    return RedirectResponse(url="/admin/family", status_code=303)


@router.post("/users/{user_id}/update-custody")
async def update_child_custody(
    user_id: int,
    request: Request,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    """
    Update custodians (adults/admins) for a child account.
    """
    permissions.require_parent_or_admin(current_user)

    result = await db.execute(select(models.User).where(models.User.id == user_id))
    child = result.scalar_one_or_none()
    if not child:
        raise HTTPException(status_code=404, detail="User not found")
    if child.role != permissions.UserRole.CHILD:
        raise HTTPException(status_code=400, detail="Custody can only be set on child accounts")

    can_manage = await _is_child_manager(db, current_user, child)
    if not can_manage:
        raise HTTPException(status_code=403, detail="You do not have permission to manage this child")

    form = await request.form()
    selected_ids = set(_parse_int_list(form.getlist("parent_ids")))
    if permissions.is_adult(current_user):
        selected_ids.add(current_user.id)

    if not selected_ids:
        raise HTTPException(status_code=400, detail="At least one custodian must be selected")

    guardians_stmt = select(models.User).where(
        models.User.id.in_(selected_ids),
        models.User.role.in_([permissions.UserRole.ADMIN, *permissions.adult_role_values()]),
    )
    guardians_res = await db.execute(guardians_stmt)
    guardians = guardians_res.scalars().all()

    if len(guardians) != len(selected_ids):
        raise HTTPException(status_code=400, detail="One or more selected custodians are invalid")

    # Avoid async lazy-load pitfalls by updating custody via association table directly.
    await db.execute(delete(models.child_custody).where(models.child_custody.c.child_id == child.id))
    for guardian in guardians:
        await db.execute(
            insert(models.child_custody).values(child_id=child.id, parent_id=guardian.id)
        )
    child.parent_id = guardians[0].id if guardians else None
    await db.commit()

    return RedirectResponse(url="/admin/family", status_code=303)


@router.post("/users/{user_id}/delete", name="delete_user_account")
@router.post("/users/{user_id}/remove")
async def delete_user_account(
    user_id: int,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    """
    Admin-only account deletion from the admin settings user table.
    """
    permissions.require_admin(current_user)
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own admin account.")

    result = await db.execute(select(models.User).where(models.User.id == user_id))
    target_user = result.scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    await _delete_user_with_related_data(db, target_user)
    await db.commit()
    return RedirectResponse(url="/admin/settings", status_code=303)


@router.post("/users/{user_id}/delete-child")
async def delete_child_account(
    user_id: int,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    """
    Delete a child account (admin or assigned adult custodian).
    """
    permissions.require_parent_or_admin(current_user)

    result = await db.execute(select(models.User).where(models.User.id == user_id))
    child = result.scalar_one_or_none()
    if not child:
        raise HTTPException(status_code=404, detail="User not found")
    if child.role != permissions.UserRole.CHILD:
        raise HTTPException(status_code=400, detail="Only child accounts can be deleted here")

    can_manage = await _is_child_manager(db, current_user, child)
    if not can_manage:
        raise HTTPException(status_code=403, detail="You do not have permission to delete this child account")

    await _delete_user_with_related_data(db, child)
    await db.commit()

    return RedirectResponse(url="/admin/family", status_code=303)

@router.post("/create-child")
async def create_child_account(
    request: Request,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    """
    Create a child account linked to the current adult/admin custodian.
    """
    from .. import crud_users
    
    permissions.require_parent_or_admin(current_user)

    form = await request.form()
    username = (form.get("username") or "").strip()
    password = (form.get("password") or "").strip()
    display_name = (form.get("display_name") or "").strip() or None

    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required")
    
    # Check if username exists
    existing = await crud_users.get_user_by_username(db, username)
    if existing:
        # TODO: Better error display (flash message)
        raise HTTPException(status_code=400, detail="That child already exists")
    
    # Create the child user
    user_in = schemas.UserCreate(
        username=username,
        password=password,
        display_name=display_name
    )
    
    try:
        child = await crud_users.create_user_no_commit(db=db, user=user_in)
        
        selected_ids = set(_parse_int_list(form.getlist("parent_ids")))
        if permissions.is_adult(current_user) or not selected_ids:
            selected_ids.add(current_user.id)

        guardians_stmt = select(models.User).where(
            models.User.id.in_(selected_ids),
            models.User.role.in_([permissions.UserRole.ADMIN, *permissions.adult_role_values()]),
        )
        guardians_res = await db.execute(guardians_stmt)
        guardians = guardians_res.scalars().all()
        if len(guardians) != len(selected_ids):
            raise HTTPException(status_code=400, detail="Invalid custodian selection")

        # Set as child and link to custodians
        child.role = permissions.UserRole.CHILD
        child.parent_id = guardians[0].id if guardians else current_user.id
        child.content_filter_level = permissions.ContentFilterLevel.MODERATE
        child.can_post = True
        child.can_follow_external = False
        for guardian in guardians:
            await db.execute(
                insert(models.child_custody).values(child_id=child.id, parent_id=guardian.id)
            )
        
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        print(f"Error creating child: {e}")
        await db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create account")
    
    return RedirectResponse(url="/admin/family", status_code=303)
