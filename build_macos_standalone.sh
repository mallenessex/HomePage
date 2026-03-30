#!/usr/bin/env bash
# ============================================================================
# HOMEPAGE Client — Standalone macOS Build Script
#
# This script is FULLY SELF-CONTAINED.  It embeds all source files needed to
# build the macOS .app bundle + .dmg installer.  No repo checkout required.
#
# Prerequisites (the Mac needs):
#   - macOS 11+ (Big Sur or later)
#   - Python 3.10+  (check: python3 --version)
#     Install via Homebrew if missing:  brew install python@3.12
#
# Usage:
#   chmod +x build_macos_standalone.sh
#   ./build_macos_standalone.sh
#
# Output:
#   ./dist/HomepageClient-macos.dmg
#   ./dist/HomepageClient.app/
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORK_DIR="$SCRIPT_DIR/homepage-client-build"
APP_NAME="HomepageClient"
DMG_NAME="HomepageClient-macos.dmg"

echo "============================================"
echo "  HOMEPAGE Client — macOS Standalone Build"
echo "============================================"
echo ""

# ── 0. Check Python 3 ─────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found."
    echo "Install it with:  brew install python@3.12"
    exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Found Python $PY_VERSION"

# ── 1. Create clean working directory ──────────────────────────────────────
echo ""
echo "==> Setting up working directory: $WORK_DIR"
rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR/dist"
cd "$WORK_DIR"

# ── 2. Write embedded source files ────────────────────────────────────────

echo "==> Writing client_app.py ..."
cat << 'HOMEPAGE_CLIENT_APP_PY_EOF' > client_app.py
"""
HOMEPAGE Client — multi-platform contained client.

Features:
- Multi-server support: connect to multiple HOMEPAGE networks.
- Generates a persistent unique client ID.
- Performs server-ID handshake request.
- Polls join approval status.
- Opens a locked web container that only allows navigation to the approved server origin.
"""
from __future__ import annotations

import json
import os
import tkinter as tk
import ipaddress
import platform
import subprocess
import sys
from pathlib import Path
from tkinter import messagebox, ttk
from urllib.parse import urlparse

import argon2
import requests
import webview

try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

APP_NAME = "HOMEPAGE Client"
ALLOW_LOOPBACK = os.getenv("HF_ALLOW_LOOPBACK", "").strip() in {"1", "true", "TRUE", "yes", "YES"}
CONNECT_PROFILE_PATH = "/.well-known/connect-profile"
LOCAL_CA_PATH = "/.well-known/local-ca.crt"
SEED_FILE_NAMES = (
    "homepage-client-seed.json",
    "house-fantastico-client-seed.json",
    "hf-client-seed.json",
)


def _data_dir() -> Path:
    if os.name == "nt":
        base = Path(os.getenv("APPDATA", Path.home()))
        return base / "HomepageClient"
    return Path.home() / ".homepage_client"


def _bundle_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _ensure_client_id(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        value = path.read_text(encoding="utf-8").strip().lower()
        if value:
            return value
    import uuid

    value = uuid.uuid4().hex
    path.write_text(value, encoding="utf-8")
    return value


def _migrate_old_data_dir() -> None:
    """One-time migration: copy config from old data dir to new one."""
    new_dir = _data_dir()
    if os.name == "nt":
        base = Path(os.getenv("APPDATA", Path.home()))
        old_dir = base / "HouseFantasticoClient"  # legacy name migration
    else:
        old_dir = Path.home() / ".house_fantastico_client"
    if old_dir.exists() and not new_dir.exists():
        import shutil
        try:
            shutil.copytree(str(old_dir), str(new_dir))
        except Exception:
            pass


class ClientConfig:
    """Manages persistent configuration with multi-server support."""

    def __init__(self) -> None:
        _migrate_old_data_dir()
        self.dir = _data_dir()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.bundle_dir = _bundle_dir()
        self.client_id_file = self.dir / "client_id.txt"
        self.config_file = self.dir / "config.json"
        self.client_id = _ensure_client_id(self.client_id_file)
        self.active_index: int = 0
        self.servers: list[dict] = []
        self.load()

    @staticmethod
    def _empty_server_entry(label: str = "") -> dict:
        return {
            "label": label or "New Server",
            "server_url": "",
            "target_server_id": "",
            "requested_username": "",
            "requested_display_name": "",
            "request_note": "",
            "request_id": "",
            "request_status": "not_requested",
            "secure_setup_status": "not_configured",
            "secure_profile": {},
        }

    @property
    def active_server(self) -> dict:
        if not self.servers:
            self.servers.append(self._empty_server_entry())
        if self.active_index >= len(self.servers):
            self.active_index = 0
        return self.servers[self.active_index]

    def load(self) -> None:
        raw: dict = {}
        if self.config_file.exists():
            try:
                raw = json.loads(self.config_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        if not isinstance(raw, dict):
            raw = {}

        # Migration: old single-server format -> multi-server
        if "servers" not in raw and "server_url" in raw:
            entry = self._empty_server_entry()
            for key in list(entry.keys()):
                if key in raw:
                    entry[key] = raw[key]
            if not isinstance(entry.get("secure_profile"), dict):
                entry["secure_profile"] = {}
            sid = str(entry.get("target_server_id", "")).strip()
            entry["label"] = sid[:16] or "Server"
            self.servers = [entry]
            self.active_index = 0
        else:
            self.servers = raw.get("servers", [])
            self.active_index = raw.get("active_server_index", 0)

        if not self.servers:
            self.servers.append(self._empty_server_entry())
        if self.active_index >= len(self.servers):
            self.active_index = 0

        self._apply_seed_if_needed()

    def save(self) -> None:
        data = {
            "active_server_index": self.active_index,
            "servers": self.servers,
        }
        self.config_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def add_server(self, label: str = "", seed: dict | None = None) -> int:
        entry = self._empty_server_entry(label=label)
        if seed and isinstance(seed, dict):
            self._apply_seed_to_entry(entry, seed)
        self.servers.append(entry)
        return len(self.servers) - 1

    def remove_server(self, index: int) -> None:
        if len(self.servers) <= 1:
            return
        self.servers.pop(index)
        if self.active_index >= len(self.servers):
            self.active_index = len(self.servers) - 1

    def server_labels(self) -> list[str]:
        labels = []
        for i, srv in enumerate(self.servers):
            label = srv.get("label") or srv.get("target_server_id", "")[:12] or f"Server {i + 1}"
            labels.append(label)
        return labels

    def _find_seed_file(self) -> Path | None:
        for name in SEED_FILE_NAMES:
            candidate = self.bundle_dir / name
            if candidate.exists() and candidate.is_file():
                return candidate
        return None

    @staticmethod
    def _apply_seed_to_entry(entry: dict, raw: dict) -> None:
        for key in (
            "server_url", "target_server_id", "requested_username",
            "requested_display_name", "request_note", "request_status",
            "secure_setup_status",
        ):
            value = raw.get(key)
            if value is not None:
                entry[key] = str(value)
        profile = raw.get("secure_profile")
        if isinstance(profile, dict):
            entry["secure_profile"] = profile
        sid = entry.get("target_server_id", "")
        sname = raw.get("server_name") or ""
        entry["label"] = str(sname or sid[:16] or "Seeded Server")

    def _apply_seed_if_needed(self) -> None:
        seed_path = self._find_seed_file()
        if not seed_path:
            return
        try:
            raw = json.loads(seed_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(raw, dict):
            return

        target_id = str(raw.get("target_server_id", "")).strip().lower()
        apply_always = str(raw.get("apply_always", "")).strip().lower() in {"1", "true", "yes", "on"}

        # Check if we already have an entry for this server
        existing_idx: int | None = None
        if target_id:
            for i, srv in enumerate(self.servers):
                if str(srv.get("target_server_id", "")).strip().lower() == target_id:
                    existing_idx = i
                    break

        if existing_idx is not None and not apply_always:
            # Even when not apply_always, update server_url if the seed has a
            # different one (e.g. user re-downloaded an external package after
            # previously using a LAN package for the same server).
            seed_url = str(raw.get("server_url", "")).strip()
            if seed_url and seed_url != self.servers[existing_idx].get("server_url", ""):
                self.servers[existing_idx]["server_url"] = seed_url
                # Also refresh audience-specific secure_profile if provided
                seed_profile = raw.get("secure_profile")
                if isinstance(seed_profile, dict):
                    self.servers[existing_idx]["secure_profile"] = seed_profile
                    self.servers[existing_idx]["secure_setup_status"] = str(
                        raw.get("secure_setup_status", "not_configured")
                    )
                self.active_index = existing_idx
                try:
                    self.save()
                except Exception:
                    pass
            return

        if existing_idx is not None:
            self._apply_seed_to_entry(self.servers[existing_idx], raw)
            self.active_index = existing_idx
        else:
            idx = self.add_server(seed=raw)
            self.active_index = idx

        try:
            self.save()
        except Exception:
            pass


def _normalize_server_url(raw: str) -> str:
    value = (raw or "").strip().rstrip("/")
    if not value:
        raise ValueError("Server URL is required")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Server URL must include scheme and host, e.g. https://example.com")
    return value


def _format_url_host(host: str) -> str:
    clean = (host or "").strip().strip("[]")
    if ":" in clean and not clean.startswith("["):
        return f"[{clean}]"
    return clean


def _build_base_url(scheme: str, host: str, port: int | None = None) -> str:
    safe_host = _format_url_host(host)
    if not safe_host:
        raise ValueError("Host is required")
    default_port = 443 if scheme == "https" else 80
    if port is None or port == default_port:
        return f"{scheme}://{safe_host}"
    return f"{scheme}://{safe_host}:{int(port)}"


def _candidate_server_urls(server_url: str) -> list[str]:
    normalized = _normalize_server_url(server_url)
    parsed = urlparse(normalized)
    host = (parsed.hostname or "").strip()
    scheme = (parsed.scheme or "").strip().lower()
    port = parsed.port

    ordered: list[str] = []

    def _append(base_url: str) -> None:
        try:
            clean = _normalize_server_url(base_url)
        except Exception:
            return
        if clean not in ordered:
            ordered.append(clean)

    _append(normalized)
    if not host or not _is_private_or_local_host(host):
        return ordered

    if scheme == "https":
        if port in {None, 443}:
            _append(_build_base_url("https", host, 8443))
            _append(_build_base_url("http", host, 8001))
        elif port == 8443:
            _append(_build_base_url("http", host, 8001))
        else:
            _append(_build_base_url("https", host, 8443))
            _append(_build_base_url("http", host, 8001))
    elif scheme == "http":
        if port in {None, 80}:
            _append(_build_base_url("http", host, 8001))
        _append(_build_base_url("https", host, 8443))
    return ordered


def _request_with_server_fallback(
    method: str,
    server_url: str,
    endpoint_path: str,
    *,
    timeout: int = 15,
    params: dict | None = None,
    json_payload: dict | None = None,
) -> tuple[requests.Response, str]:
    endpoint = endpoint_path if endpoint_path.startswith("/") else f"/{endpoint_path}"
    candidates = _candidate_server_urls(server_url)
    errors: list[str] = []
    last_exc: Exception | None = None

    for base_url in candidates:
        verify = _get_tls_verify_param(base_url)
        try:
            resp = requests.request(
                method=method.upper(),
                url=f"{base_url}{endpoint}",
                params=params,
                json=json_payload,
                timeout=timeout,
                verify=verify,
            )
            return resp, base_url
        except requests.RequestException as first_exc:
            # For HTTPS, retry with verify=False — catches SSLError even when
            # wrapped inside ConnectionError / MaxRetryError by urllib3.
            if verify is not False and base_url.lower().startswith("https"):
                try:
                    resp = requests.request(
                        method=method.upper(),
                        url=f"{base_url}{endpoint}",
                        params=params,
                        json=json_payload,
                        timeout=timeout,
                        verify=False,
                    )
                    return resp, base_url
                except requests.RequestException as retry_exc:
                    last_exc = retry_exc
                    errors.append(f"{base_url} -> {retry_exc}")
            else:
                last_exc = first_exc
                errors.append(f"{base_url} -> {first_exc}")

    summary = "; ".join(errors) if errors else "no URL candidates were generated"
    raise RuntimeError(f"Unable to reach server. Tried: {summary}") from last_exc


def _is_loopback_server_url(server_url: str) -> bool:
    parsed = urlparse(server_url)
    host = (parsed.hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def _same_origin(url: str, allowed_origin: str) -> bool:
    u = urlparse(url)
    a = urlparse(allowed_origin)
    if u.scheme in {"about", "data", "blob"}:
        return True
    return u.scheme == a.scheme and u.netloc.lower() == a.netloc.lower()


def _is_private_or_local_host(host: str | None) -> bool:
    if not host:
        return False
    h = host.lower().strip("[]")
    if h in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        ip = ipaddress.ip_address(h)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return h.endswith(".local")


# Path to the locally-cached CA cert (set once ClientConfig loads).
_LOCAL_CA_CERT_PATH: Path | None = None


def _should_skip_tls_verify(server_url: str) -> bool:
    """Legacy helper kept for simple boolean checks."""
    parsed = urlparse(server_url)
    return parsed.scheme == "https" and _is_private_or_local_host(parsed.hostname)


def _get_tls_verify_param(server_url: str) -> str | bool:
    """Return the `verify` parameter for requests.

    - Private / .local HTTPS  -> False  (self-signed, skip)
    - Public HTTPS + local CA -> path to CA cert  (internal CA, verify against it)
    - Public HTTPS, no CA     -> True   (expect a real cert)
    - HTTP                    -> True   (irrelevant, no TLS)
    """
    parsed = urlparse(server_url)
    if parsed.scheme != "https":
        return True
    if _is_private_or_local_host(parsed.hostname):
        return False
    # Public host — use the local CA bundle if we have one
    if _LOCAL_CA_CERT_PATH and _LOCAL_CA_CERT_PATH.exists():
        return str(_LOCAL_CA_CERT_PATH)
    return True


def _profile_value_str(profile: dict, key: str) -> str:
    value = profile.get(key)
    return str(value).strip() if value is not None else ""


def _get_connect_profile(server_url: str) -> dict:
    resp, _resolved_url = _request_with_server_fallback(
        "GET",
        server_url,
        CONNECT_PROFILE_PATH,
        timeout=15,
    )
    data = resp.json()
    if not resp.ok:
        raise RuntimeError(data.get("detail", f"HTTP {resp.status_code}"))
    if not isinstance(data, dict):
        raise RuntimeError("Invalid connect profile response")
    return data


def _resolve_ca_url(server_url: str, profile: dict) -> str:
    candidate = _profile_value_str(profile, "ca_certificate_url")
    if candidate:
        try:
            parsed = urlparse(candidate)
            if parsed.scheme in {"http", "https"} and parsed.netloc:
                return candidate
        except Exception:
            pass
    return f"{server_url}{LOCAL_CA_PATH}"


def _hosts_file_path() -> Path:
    if os.name == "nt":
        return Path(os.getenv("SystemRoot", r"C:\Windows")) / "System32" / "drivers" / "etc" / "hosts"
    return Path("/etc/hosts")


def _upsert_hosts_mapping(domain: str, ip: str) -> None:
    hosts_path = _hosts_file_path()
    existing_lines: list[str] = []
    if hosts_path.exists():
        existing_lines = hosts_path.read_text(encoding="utf-8", errors="ignore").splitlines()

    filtered: list[str] = []
    target = domain.strip().lower()
    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            filtered.append(line)
            continue
        body = stripped.split("#", 1)[0].strip()
        tokens = body.split()
        if len(tokens) >= 2 and any(token.strip().lower() == target for token in tokens[1:]):
            continue
        filtered.append(line)

    filtered.append(f"{ip} {domain}")
    hosts_path.write_text("\n".join(filtered) + "\n", encoding="ascii", errors="ignore")


def _flush_dns_cache() -> None:
    if os.name == "nt":
        subprocess.run(["ipconfig", "/flushdns"], check=False, capture_output=True, text=True)


def _is_windows_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _install_ca_certificate(ca_path: Path) -> tuple[bool, str]:
    system = platform.system().lower()

    if system == "windows":
        # Use machine trust store when elevated; user trust store otherwise.
        cmd = ["certutil", "-addstore", "-f", "Root", str(ca_path)]
        if not _is_windows_admin():
            cmd = ["certutil", "-user", "-addstore", "-f", "Root", str(ca_path)]
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if proc.returncode == 0:
            return True, "CA certificate installed."
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
        return False, stderr or stdout or "Failed to install CA certificate."

    if system == "linux":
        if os.geteuid() != 0:
            return False, "Linux CA install needs root (run client with sudo)."
        target = Path("/usr/local/share/ca-certificates/homepage-local-ca.crt")
        target.write_bytes(ca_path.read_bytes())
        proc = subprocess.run(["update-ca-certificates"], check=False, capture_output=True, text=True)
        if proc.returncode == 0:
            return True, "CA certificate installed."
        return False, (proc.stderr or proc.stdout or "").strip() or "Failed to refresh CA trust store."

    if system == "darwin":
        cmd = [
            "security",
            "add-trusted-cert",
            "-d",
            "-r",
            "trustRoot",
            "-k",
            "/Library/Keychains/System.keychain",
            str(ca_path),
        ]
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if proc.returncode == 0:
            return True, "CA certificate installed."
        return False, (proc.stderr or proc.stdout or "").strip() or "Failed to install CA certificate."

    return False, "Unsupported OS for automatic CA install."


def _save_ca_certificate(server_url: str, profile: dict, target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = target_dir / "homepage-local-ca.crt"

    # Try the profile URL first, then same-origin fallback across candidate URLs.
    errors: list[str] = []
    urls_to_try: list[str] = []
    explicit_ca_url = _resolve_ca_url(server_url, profile)
    if explicit_ca_url:
        urls_to_try.append(explicit_ca_url)
    for candidate_base in _candidate_server_urls(server_url):
        candidate_url = f"{candidate_base}{LOCAL_CA_PATH}"
        if candidate_url not in urls_to_try:
            urls_to_try.append(candidate_url)

    for url in urls_to_try:
        verify = _get_tls_verify_param(url)
        for v in ([verify, False] if verify is not False else [False]):
            try:
                resp = requests.get(url, timeout=20, verify=v)
                if resp.ok and resp.content:
                    destination.write_bytes(resp.content)
                    return destination
                errors.append(f"{url} -> HTTP {resp.status_code}")
                break  # got a response, don't retry same URL
            except requests.RequestException:
                if v is not False:
                    continue  # retry without verification
                errors.append(f"{url} -> connection/SSL failed")
            except Exception as exc:
                errors.append(f"{url} -> {exc}")
                break

    raise RuntimeError("Unable to download local CA certificate: " + "; ".join(errors))


def _set_webview2_privacy_flags() -> None:
    """Inject Chromium command-line flags for Edge WebView2 on Windows.

    Edge WebView2 is Chromium-based and will phone home to Microsoft/Google
    for Safe Browsing, component updates, metrics, etc.  These flags disable
    all such background networking.

    Must be called BEFORE ``webview.start()`` — the env-var is read once
    when the WebView2 runtime initialises.

    On macOS (WebKit) and Linux (WebKit2/GTK) this is a no-op because those
    engines are not Chromium-based and have no Google telemetry.
    """
    if platform.system() != "Windows":
        return

    privacy_flags = [
        # -- network-level kill switches --
        "--disable-background-networking",
        "--disable-client-side-phishing-detection",
        "--disable-default-apps",
        "--disable-extensions",
        "--disable-component-update",
        "--disable-domain-reliability",
        "--disable-sync",
        "--disable-translate",
        "--disable-breakpad",
        "--no-pings",
        "--disable-features="
            "AutofillServerCommunication,"
            "SafeBrowsing,"
            "SpareRendererForSitePerProcess,"
            "OptimizationHints,"
            "MediaRouter,"
            "DialMediaRouteProvider,"
            "Translate,"
            "NetworkTimeServiceQuerying,"
            "CertificateTransparencyComponentUpdater,"
            "msSmartScreenProtection,"
            "msEdgeOnRampFRE,"
            "msEdgeOnRampImport,"
            "msImplicitSignin",
        # -- DNS / prediction --
        "--dns-prefetch-disable",
        "--disable-preconnect",
        # -- metrics & telemetry --
        "--metrics-recording-only",
        "--disable-metrics",
        "--disable-metrics-reporting",
        # -- miscellaneous hardening --
        "--disable-speech-api",
        "--disable-remote-fonts",
        "--disable-gpu-shader-disk-cache",
        "--autoplay-policy=user-gesture-required",
        # -- Edge-specific telemetry --
        "--edge-disable-push-notifications",
        "--disable-reading-from-canvas",
    ]
    existing = os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "")
    merged = " ".join(filter(None, [existing] + privacy_flags))
    os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = merged


def open_contained_browser(server_url: str) -> None:
    allowed_origin = _normalize_server_url(server_url)
    verify = _get_tls_verify_param(allowed_origin)
    webview.settings["IGNORE_SSL_ERRORS"] = (verify is False) or (isinstance(verify, str))

    # Apply Chromium privacy flags for Edge WebView2 (Windows only; no-op elsewhere)
    _set_webview2_privacy_flags()

    window = webview.create_window(APP_NAME, url=allowed_origin, width=1200, height=820)

    # JavaScript guard: origin lock + disable leak-prone browser APIs.
    guard_js = f"""
        (() => {{
          const allowedOrigin = {json.dumps(allowed_origin)};

          /* -- origin enforcement -- */
          function enforceOrigin() {{
            try {{
              if (window.location.origin !== allowedOrigin) {{
                window.location.href = allowedOrigin + '/';
              }}
            }} catch (_) {{}}
          }}

          /* -- block outbound navigation -- */
          window.open = () => null;
          document.addEventListener('click', (e) => {{
            const a = e.target.closest && e.target.closest('a[href]');
            if (!a) return;
            const href = a.getAttribute('href') || '';
            if (!href) return;
            try {{
              const u = new URL(href, window.location.origin);
              if (u.origin !== allowedOrigin) {{
                e.preventDefault();
                e.stopPropagation();
              }}
            }} catch (_) {{}}
          }}, true);
          window.addEventListener('popstate', enforceOrigin, true);
          window.addEventListener('hashchange', enforceOrigin, true);
          setInterval(enforceOrigin, 500);
          enforceOrigin();

          /* -- neuter APIs that could phone home -- */

          // 1. Beacon API (analytics / tracking pings)
          if (navigator.sendBeacon) {{
            navigator.sendBeacon = () => false;
          }}

          // 2. Geolocation — deny silently
          if (navigator.geolocation) {{
            navigator.geolocation.getCurrentPosition = (_s, err) => {{
              if (err) err({{ code: 1, message: 'Denied by privacy policy' }});
            }};
            navigator.geolocation.watchPosition = (_s, err) => {{
              if (err) err({{ code: 1, message: 'Denied by privacy policy' }});
              return 0;
            }};
          }}

          // 3. WebRTC — strip only *external* STUN/TURN servers
          //    The server provides its own TURN server (same-origin infra)
          //    which is trusted.  We only block third-party STUN servers
          //    (e.g. stun.l.google.com) that could be used for IP leak.
          if (typeof RTCPeerConnection !== 'undefined') {{
            const _RTC = RTCPeerConnection;
            window.RTCPeerConnection = function(cfg, ...rest) {{
              cfg = cfg || {{}};
              if (cfg.iceServers) {{
                const host = new URL(allowedOrigin).hostname;
                cfg.iceServers = cfg.iceServers.filter(s => {{
                  const urls = Array.isArray(s.urls) ? s.urls : [s.urls];
                  return urls.some(u => {{
                    try {{ return new URL(u.replace(/^(stun|turn|turns):/, 'https://')).hostname === host; }}
                    catch (_) {{ return u.includes(host); }}
                  }});
                }});
              }}
              return new _RTC(cfg, ...rest);
            }};
            window.RTCPeerConnection.prototype = _RTC.prototype;
          }}

          // 4. Block fetch / XHR to foreign origins
          const _fetch = window.fetch;
          window.fetch = function(resource, init) {{
            try {{
              const url = (typeof resource === 'string')
                ? new URL(resource, window.location.origin)
                : new URL(resource.url);
              if (url.origin !== allowedOrigin) {{
                return Promise.reject(new TypeError('Blocked by privacy policy'));
              }}
            }} catch (_) {{}}
            return _fetch.apply(this, arguments);
          }};

          const _xhrOpen = XMLHttpRequest.prototype.open;
          XMLHttpRequest.prototype.open = function(method, url, ...rest) {{
            try {{
              const u = new URL(url, window.location.origin);
              if (u.origin !== allowedOrigin) {{
                throw new TypeError('Blocked by privacy policy');
              }}
            }} catch (ex) {{
              if (ex.message === 'Blocked by privacy policy') throw ex;
            }}
            return _xhrOpen.apply(this, [method, url, ...rest]);
          }};

          // 5. Disable Battery API (fingerprinting)
          if (navigator.getBattery) {{
            navigator.getBattery = () => Promise.reject(
              new DOMException('Denied by privacy policy', 'NotAllowedError')
            );
          }}

          // 6. Neuter deviceMemory / hardwareConcurrency (fingerprinting)
          try {{ Object.defineProperty(navigator, 'deviceMemory',      {{ value: 4, configurable: false }}); }} catch(_) {{}}
          try {{ Object.defineProperty(navigator, 'hardwareConcurrency', {{ value: 4, configurable: false }}); }} catch(_) {{}}

          console.log('[HOMEPAGE] Privacy guard active — origin locked to', allowedOrigin);
        }})();
    """

    def on_loaded():
        try:
            window.evaluate_js(guard_js)
        except Exception:
            pass

    events = getattr(window, "events", None)
    if events is not None and hasattr(events, "loaded"):
        events.loaded += on_loaded
    start_kwargs = {
        "debug": False,
        "private_mode": False,
    }
    webview.start(**start_kwargs)


class ClientApp:
    def __init__(self) -> None:
        self.cfg = ClientConfig()
        # Publish local CA path so HTTP helpers can use it
        global _LOCAL_CA_CERT_PATH
        # Migrate old cert filename if present
        old_ca = self.cfg.dir / "house-fantastico-local-ca.crt"
        new_ca = self.cfg.dir / "homepage-local-ca.crt"
        if old_ca.exists() and not new_ca.exists():
            old_ca.rename(new_ca)
        ca_candidate = new_ca
        if ca_candidate.exists():
            _LOCAL_CA_CERT_PATH = ca_candidate
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry("900x740")
        self.root.resizable(False, False)

        # Tk vars bound to the active server entry
        srv = self.cfg.active_server
        self.server_url = tk.StringVar(value=srv.get("server_url", ""))
        self.target_server_id = tk.StringVar(value=srv.get("target_server_id", ""))
        self.requested_username = tk.StringVar(value=srv.get("requested_username", ""))
        self.requested_display_name = tk.StringVar(value=srv.get("requested_display_name", ""))
        self.request_note = tk.StringVar(value=srv.get("request_note", ""))
        self.requested_password = tk.StringVar()  # never persisted
        self.request_id = tk.StringVar(value=str(srv.get("request_id", "")))
        self.request_status = tk.StringVar(value=srv.get("request_status", "not_requested"))
        self.secure_setup_status = tk.StringVar(value=srv.get("secure_setup_status", "not_configured"))
        self.effective_url = tk.StringVar(value="")
        self.auto_opened = False
        self.connect_panel: ttk.Frame | None = None
        self.join_panel: ttk.Frame | None = None
        self.pending_label: ttk.Label | None = None
        self._poll_job: str | None = None  # Tk after() handle for auto-poll
        self.secure_profile: dict = srv.get("secure_profile", {}) if isinstance(srv.get("secure_profile"), dict) else {}
        self.secure_instructions: tk.Text | None = None
        self.server_combo: ttk.Combobox | None = None
        self._switching = False  # guard against recursive trace callbacks

        self._build()
        self._start_poll()  # auto-poll if we already have a pending request on launch

    # ---------- UI layout ----------

    def _build(self) -> None:
        frame = ttk.Frame(self.root, padding=18)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(2, weight=1)

        ttk.Label(frame, text="HOMEPAGE Client", font=("Segoe UI", 15, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w"
        )
        ttk.Label(
            frame,
            text="Connect to one or more HOMEPAGE servers. Navigation is locked to the active server origin.",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 8))

        ttk.Label(frame, text=f"Client ID: {self.cfg.client_id}", foreground="#444").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(0, 6)
        )

        # --- Server selector row ---
        ttk.Label(frame, text="Active Server", font=("Segoe UI", 10, "bold")).grid(row=3, column=0, sticky="w", pady=6)
        selector_frame = ttk.Frame(frame)
        selector_frame.grid(row=3, column=1, columnspan=2, sticky="we", pady=6)
        self.server_combo = ttk.Combobox(selector_frame, state="readonly", width=40)
        self.server_combo.pack(side="left", padx=(0, 8))
        self._refresh_server_combo()
        self.server_combo.bind("<<ComboboxSelected>>", self._on_server_selected)
        ttk.Button(selector_frame, text="Add Server", command=self._add_server).pack(side="left", padx=4)
        ttk.Button(selector_frame, text="Rename", command=self._rename_server).pack(side="left", padx=4)
        ttk.Button(selector_frame, text="Remove Server", command=self._remove_server).pack(side="left", padx=4)

        # --- Common server fields ---
        self._row_entry(frame, 4, "Server URL", self.server_url)
        self.server_url.trace_add("write", lambda *_: self._update_effective_url())
        self._update_effective_url()
        self._row_entry(frame, 5, "Target Server ID", self.target_server_id)

        ttk.Label(frame, text="Effective URL", foreground="#666").grid(row=6, column=0, sticky="w", pady=(4, 4))
        ttk.Label(frame, textvariable=self.effective_url, foreground="#111").grid(row=6, column=1, columnspan=2, sticky="w", pady=(4, 4))

        # === Swappable panel area (row 7) ===
        self._build_connect_panel(frame)
        self._build_join_panel(frame)
        self.request_status.trace_add("write", lambda *_: self._update_pending_indicator())
        self._show_connect_panel()

        # === Secure Mode section ===
        separator = ttk.Separator(frame, orient="horizontal")
        separator.grid(row=8, column=0, columnspan=3, sticky="we", pady=(10, 10))

        ttk.Label(frame, text="Secure Mode Setup", font=("Segoe UI", 11, "bold")).grid(
            row=9, column=0, columnspan=3, sticky="w", pady=(0, 4)
        )
        ttk.Label(
            frame,
            text="Fetch the server profile, install CA trust, and add hosts mapping for .local HTTPS.",
            foreground="#444",
        ).grid(row=10, column=0, columnspan=3, sticky="w", pady=(0, 8))

        secure_btns = ttk.Frame(frame)
        secure_btns.grid(row=11, column=0, columnspan=3, sticky="w", pady=(0, 8))
        ttk.Button(secure_btns, text="Fetch Secure Profile", command=self.fetch_secure_profile).pack(side="left", padx=(0, 8))
        ttk.Button(secure_btns, text="Apply Secure Setup", command=self.apply_secure_setup).pack(side="left", padx=8)
        ttk.Button(secure_btns, text="Copy Instructions", command=self.copy_secure_instructions).pack(side="left", padx=8)

        ttk.Label(frame, text="Secure Setup Status").grid(row=12, column=0, sticky="w", pady=(0, 4))
        ttk.Label(frame, textvariable=self.secure_setup_status).grid(row=12, column=1, columnspan=2, sticky="w", pady=(0, 4))

        self.secure_instructions = tk.Text(frame, height=9, width=104, wrap="word")
        self.secure_instructions.grid(row=13, column=0, columnspan=3, sticky="we", pady=(4, 0))
        self.secure_instructions.configure(state="disabled")
        self._refresh_secure_instructions()

    def _build_connect_panel(self, parent: ttk.Frame) -> None:
        """Build the Connect panel (default view)."""
        self.connect_panel = ttk.Frame(parent)
        panel = self.connect_panel
        panel.columnconfigure(0, weight=1)

        btns = ttk.Frame(panel)
        btns.grid(row=0, column=0, sticky="w", pady=(8, 6))
        ttk.Button(btns, text="Connect + Open", command=self.connect_and_open).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="Open Contained App", command=self.open_app).pack(side="left", padx=8)
        ttk.Button(btns, text="Save Config", command=self.save_config).pack(side="left", padx=8)

        # Pending-request indicator (auto-updated via trace on request_status)
        self.pending_label = ttk.Label(panel, text="", foreground="#b45309")
        self.pending_label.grid(row=1, column=0, sticky="w", pady=(4, 4))

        join_link = ttk.Label(
            panel, text="Not a member?  Request to join \u2192",
            foreground="#2563eb", cursor="hand2", font=("Segoe UI", 10, "underline"),
        )
        join_link.grid(row=2, column=0, sticky="w", pady=(8, 4))
        join_link.bind("<Button-1>", lambda _e: self._show_join_panel())

    def _build_join_panel(self, parent: ttk.Frame) -> None:
        """Build the Join Request panel."""
        self.join_panel = ttk.Frame(parent)
        panel = self.join_panel
        panel.columnconfigure(1, weight=1)
        panel.columnconfigure(2, weight=1)

        ttk.Label(panel, text="Join Request", font=("Segoe UI", 11, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(4, 6)
        )

        self._row_entry(panel, 1, "Requested Username", self.requested_username)
        self._row_entry(panel, 2, "Display Name", self.requested_display_name)

        # Password (masked, never persisted)
        ttk.Label(panel, text="Password").grid(row=3, column=0, sticky="w", pady=6)
        ttk.Entry(panel, textvariable=self.requested_password, width=68, show="\u2022").grid(
            row=3, column=1, columnspan=2, sticky="we", pady=6
        )

        self._row_entry(panel, 4, "Join Note", self.request_note)

        ttk.Label(panel, text="Request ID").grid(row=5, column=0, sticky="w", pady=(8, 4))
        ttk.Entry(panel, textvariable=self.request_id, width=22).grid(row=5, column=1, sticky="w", pady=(8, 4))

        ttk.Label(panel, text="Request Status").grid(row=6, column=0, sticky="w", pady=6)
        ttk.Label(panel, textvariable=self.request_status).grid(row=6, column=1, sticky="w", pady=6)

        btns = ttk.Frame(panel)
        btns.grid(row=7, column=0, columnspan=3, sticky="w", pady=(8, 6))
        ttk.Button(btns, text="Request Join", command=self.request_join).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="Check Status", command=self.check_status).pack(side="left", padx=8)
        ttk.Button(btns, text="Save Config", command=self.save_config).pack(side="left", padx=8)

        back_link = ttk.Label(
            panel, text="\u2190 Back to connect",
            foreground="#2563eb", cursor="hand2", font=("Segoe UI", 10, "underline"),
        )
        back_link.grid(row=8, column=0, columnspan=3, sticky="w", pady=(8, 4))
        back_link.bind("<Button-1>", lambda _e: self._show_connect_panel())

    # -- Panel visibility helpers --

    def _show_connect_panel(self) -> None:
        if self.join_panel is not None:
            self.join_panel.grid_remove()
        if self.connect_panel is not None:
            self.connect_panel.grid(row=7, column=0, columnspan=3, sticky="nswe")
        self._update_pending_indicator()

    def _show_join_panel(self) -> None:
        if self.connect_panel is not None:
            self.connect_panel.grid_remove()
        if self.join_panel is not None:
            self.join_panel.grid(row=7, column=0, columnspan=3, sticky="nswe")

    def _update_pending_indicator(self) -> None:
        """Update the pending-request badge on the connect panel."""
        if self.pending_label is None:
            return
        status = self.request_status.get()
        req_id = self.request_id.get().strip()
        if status == "pending" and req_id:
            self.pending_label.config(text=f"\u23f3 Pending join request #{req_id} \u2014 auto-checking every 20s")
        elif status == "approved":
            self.pending_label.config(text="\u2705 Join request approved")
        elif status == "rejected":
            self.pending_label.config(text="\u274c Join request was rejected")
        else:
            self.pending_label.config(text="")

    # ---------- Server selector ----------

    def _refresh_server_combo(self) -> None:
        if self.server_combo is None:
            return
        labels = self.cfg.server_labels()
        self.server_combo["values"] = labels
        if labels:
            self.server_combo.current(self.cfg.active_index)

    def _load_active_server_to_ui(self) -> None:
        self._switching = True
        srv = self.cfg.active_server
        self.server_url.set(srv.get("server_url", ""))
        self.target_server_id.set(srv.get("target_server_id", ""))
        self.requested_username.set(srv.get("requested_username", ""))
        self.requested_display_name.set(srv.get("requested_display_name", ""))
        self.request_note.set(srv.get("request_note", ""))
        self.requested_password.set("")  # never persisted; clear on server switch
        self.request_id.set(str(srv.get("request_id", "")))
        self.request_status.set(srv.get("request_status", "not_requested"))
        self.secure_setup_status.set(srv.get("secure_setup_status", "not_configured"))
        self.secure_profile = srv.get("secure_profile", {}) if isinstance(srv.get("secure_profile"), dict) else {}
        self.auto_opened = False
        self._update_effective_url()
        self._refresh_secure_instructions()
        self._switching = False
        self._show_connect_panel()  # always start on connect view for the new server
        self._start_poll()  # resume polling if this server entry is pending

    def _on_server_selected(self, _event=None) -> None:
        self._save_active_to_cfg()
        idx = self.server_combo.current() if self.server_combo else 0
        self.cfg.active_index = idx
        self._load_active_server_to_ui()

    def _add_server(self) -> None:
        self._save_active_to_cfg()
        idx = self.cfg.add_server(label="New Server")
        self.cfg.active_index = idx
        self.cfg.save()
        self._refresh_server_combo()
        self._load_active_server_to_ui()

    def _rename_server(self) -> None:
        from tkinter import simpledialog
        current = self.cfg.server_labels()[self.cfg.active_index]
        new_name = simpledialog.askstring(
            APP_NAME,
            "Enter a nickname for this server:",
            initialvalue=current,
            parent=self.root,
        )
        if not new_name or not new_name.strip():
            return
        self.cfg.servers[self.cfg.active_index]["label"] = new_name.strip()
        self.cfg.servers[self.cfg.active_index]["user_renamed"] = True
        self.cfg.save()
        self._refresh_server_combo()

    def _remove_server(self) -> None:
        if len(self.cfg.servers) <= 1:
            messagebox.showinfo(APP_NAME, "Cannot remove the last server entry.")
            return
        confirm = messagebox.askyesno(
            APP_NAME,
            f"Remove server \"{self.cfg.server_labels()[self.cfg.active_index]}\"?",
        )
        if not confirm:
            return
        self.cfg.remove_server(self.cfg.active_index)
        self.cfg.save()
        self._refresh_server_combo()
        self._load_active_server_to_ui()

    # ---------- Config persistence ----------

    @staticmethod
    def _row_entry(frame: ttk.Frame, row: int, label: str, var: tk.StringVar) -> None:
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=6)
        ttk.Entry(frame, textvariable=var, width=68).grid(row=row, column=1, columnspan=2, sticky="we", pady=6)

    def _save_active_to_cfg(self) -> None:
        """Push current UI values into the active server entry."""
        srv = self.cfg.active_server
        srv["server_url"] = self.server_url.get().strip()
        srv["target_server_id"] = self.target_server_id.get().strip().lower()
        srv["requested_username"] = self.requested_username.get().strip()
        srv["requested_display_name"] = self.requested_display_name.get().strip()
        srv["request_note"] = self.request_note.get().strip()
        srv["request_id"] = self.request_id.get().strip()
        srv["request_status"] = self.request_status.get().strip()
        srv["secure_setup_status"] = self.secure_setup_status.get().strip()
        srv["secure_profile"] = self.secure_profile if isinstance(self.secure_profile, dict) else {}
        # Auto-update label from server name or ID — but only if the user
        # hasn't explicitly renamed this server (nickname takes priority).
        if not srv.get("user_renamed"):
            sid = srv["target_server_id"]
            profile_name = ""
            if isinstance(srv.get("secure_profile"), dict):
                profile_name = str(srv["secure_profile"].get("server_name", "")).strip()
            if profile_name:
                srv["label"] = profile_name
            elif sid:
                srv["label"] = sid[:16]

    def save_config(self) -> None:
        self._save_active_to_cfg()
        self.cfg.save()
        self._refresh_server_combo()

    def _update_effective_url(self) -> None:
        if self._switching:
            return
        raw = self.server_url.get().strip()
        try:
            self.effective_url.set(_normalize_server_url(raw))
        except Exception:
            self.effective_url.set(raw or "(unset)")

    # ---------- Secure mode ----------

    def _build_secure_instruction_text(self) -> str:
        profile = self.secure_profile if isinstance(self.secure_profile, dict) else {}
        domain = _profile_value_str(profile, "secure_local_domain")
        lan_ip = _profile_value_str(profile, "secure_local_ip")
        secure_url = _profile_value_str(profile, "secure_url")
        external_url = _profile_value_str(profile, "external_server_url")
        first_run = _profile_value_str(profile, "first_run_http_url")
        server_id = _profile_value_str(profile, "server_id")
        status = self.secure_setup_status.get().strip() or "not_configured"

        lines = [
            "Secure setup guide",
            f"- Status: {status}",
            "",
            "1) First-run connect by IP/HTTP.",
            f"   Use: {first_run or '[missing from profile]'}",
            "",
            "2) Apply secure setup in this client.",
            "   Click: Fetch Secure Profile -> Apply Secure Setup",
            "",
            "3) Switch to secure URL.",
            f"   LAN: {secure_url or '[missing from profile]'}",
        ]
        if external_url:
            lines.append(f"   External: {external_url}")
        lines += [
            "",
            f"4) Target Server ID: {server_id or '[missing from profile]'}",
            "",
            f"- Domain: {domain or '[missing]'}  LAN IP: {lan_ip or '[missing]'}",
        ]

        if os.name == "nt":
            lines += [
                "",
                "Windows: run as Administrator for hosts/CA install.",
            ]
        else:
            lines += [
                "",
                "Linux/macOS: run with sudo for hosts/CA install.",
            ]
        return "\n".join(lines)

    def _refresh_secure_instructions(self) -> None:
        if self.secure_instructions is None:
            return
        self.secure_instructions.configure(state="normal")
        self.secure_instructions.delete("1.0", tk.END)
        self.secure_instructions.insert("1.0", self._build_secure_instruction_text())
        self.secure_instructions.configure(state="disabled")

    def fetch_secure_profile(self) -> None:
        try:
            server_url = self._validate_server_url()
            profile = _get_connect_profile(server_url)
            if not profile.get("secure_mode_enabled"):
                raise RuntimeError("Server secure mode is disabled. Enable it in server Admin Settings first.")

            domain = _profile_value_str(profile, "secure_local_domain")
            lan_ip = _profile_value_str(profile, "secure_local_ip")
            if not domain or not lan_ip:
                raise RuntimeError(
                    "Secure profile is missing domain/IP. Server admin must set Secure Local Domain and Server LAN IP."
                )

            self.secure_profile = profile
            # Auto-fill target server ID from profile if empty
            if not self.target_server_id.get().strip():
                sid = _profile_value_str(profile, "server_id")
                if sid:
                    self.target_server_id.set(sid)
            self.secure_setup_status.set("profile_loaded")
            self.save_config()
            self._refresh_secure_instructions()
            messagebox.showinfo(APP_NAME, "Secure profile loaded. Click Apply Secure Setup next.")
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Failed to fetch secure profile: {e}")

    def apply_secure_setup(self) -> None:
        try:
            server_url = self._validate_server_url()
            profile = self.secure_profile if isinstance(self.secure_profile, dict) else {}
            if not profile:
                profile = _get_connect_profile(server_url)
                self.secure_profile = profile

            domain = _profile_value_str(profile, "secure_local_domain")
            lan_ip = _profile_value_str(profile, "secure_local_ip")
            if not domain or not lan_ip:
                raise RuntimeError("Secure profile missing secure_local_domain or secure_local_ip.")

            ca_path = _save_ca_certificate(server_url, profile, self.cfg.dir)
            ca_ok, ca_msg = _install_ca_certificate(ca_path)

            hosts_ok = True
            hosts_msg = "Hosts mapping updated."
            try:
                _upsert_hosts_mapping(domain, lan_ip)
            except PermissionError:
                hosts_ok = False
                if os.name == "nt":
                    hosts_msg = "Hosts update needs Administrator. Re-run this app as Administrator, then retry."
                else:
                    hosts_msg = "Hosts update needs elevated permissions. Run client with sudo and retry."
            except Exception as exc:
                hosts_ok = False
                hosts_msg = str(exc)

            _flush_dns_cache()
            secure_url = _profile_value_str(profile, "secure_url")

            if ca_ok and hosts_ok:
                self.secure_setup_status.set("configured")
                if secure_url:
                    self.server_url.set(secure_url)
                result = (
                    "Secure setup completed.\n\n"
                    f"- CA: {ca_msg}\n"
                    f"- Hosts: {hosts_msg}\n\n"
                    "Server URL was switched to secure mode. You can now connect."
                )
                messagebox.showinfo(APP_NAME, result)
            else:
                self.secure_setup_status.set("partial")
                result = (
                    "Secure setup partially completed.\n\n"
                    f"- CA: {'OK' if ca_ok else 'FAILED'} ({ca_msg})\n"
                    f"- Hosts: {'OK' if hosts_ok else 'FAILED'} ({hosts_msg})\n\n"
                    "See the in-app guide for the exact next step."
                )
                messagebox.showwarning(APP_NAME, result)

            self.save_config()
            self._refresh_secure_instructions()
        except Exception as e:
            self.secure_setup_status.set("failed")
            self.save_config()
            self._refresh_secure_instructions()
            messagebox.showerror(APP_NAME, f"Secure setup failed: {e}")

    def copy_secure_instructions(self) -> None:
        text = self._build_secure_instruction_text()
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        messagebox.showinfo(APP_NAME, "Secure setup instructions copied to clipboard.")

    # ---------- Server interaction ----------

    def _validate_server_url(self) -> str:
        server_url = _normalize_server_url(self.server_url.get())
        if _is_loopback_server_url(server_url) and not ALLOW_LOOPBACK:
            raise ValueError(
                f"Loopback URL blocked in this build: {server_url}. Use your server LAN/public address."
            )
        return server_url

    # ---------- Auto-poll for pending join requests ----------

    _POLL_INTERVAL_MS = 20_000  # 20 seconds

    def _start_poll(self) -> None:
        """Start background polling if status is 'pending'."""
        self._stop_poll()
        if self.request_status.get() == "pending" and self.request_id.get().strip():
            self._poll_job = self.root.after(self._POLL_INTERVAL_MS, self._poll_tick)

    def _stop_poll(self) -> None:
        """Cancel any scheduled poll."""
        if self._poll_job is not None:
            self.root.after_cancel(self._poll_job)
            self._poll_job = None

    def _poll_tick(self) -> None:
        """Single silent poll iteration -- no messageboxes unless approved/rejected."""
        self._poll_job = None
        try:
            server_url = self._validate_server_url()
            request_id = self.request_id.get().strip()
            if not request_id:
                return
            resp, resolved_server_url = _request_with_server_fallback(
                "GET",
                server_url,
                f"/.well-known/join-requests/{request_id}",
                timeout=15,
                params={"peer_server_id": self.cfg.client_id},
            )
            data = resp.json()
            if not resp.ok:
                detail = data.get("detail", "")
                # Stale request from a previous install -- clear silently
                if resp.status_code == 403 and "mismatch" in detail.lower():
                    self.request_id.set("")
                    self.request_status.set("not_requested")
                    self.save_config()
                    return  # stop polling; user must re-request
                # Silently reschedule on transient errors
                self._start_poll()
                return
            if resolved_server_url != server_url:
                self.server_url.set(resolved_server_url)
            new_status = str(data.get("status", "unknown"))
            self.request_status.set(new_status)
            self.save_config()

            if new_status == "approved" and not self.auto_opened:
                self.auto_opened = True
                alloc_user = data.get("allocated_username") or ""
                has_password = data.get("has_password", False)
                alloc_pass = data.get("allocated_password") or ""
                if has_password:
                    cred_msg = (
                        f"Request approved!\n\n"
                        f"  Username: {alloc_user}\n\n"
                        f"You set your own password during the join request.\n\n"
                        f"Opening browser now..."
                    )
                elif alloc_user and alloc_pass:
                    cred_msg = (
                        f"Request approved!\n\n"
                        f"Your login credentials:\n"
                        f"  Username: {alloc_user}\n"
                        f"  Temporary Password: {alloc_pass}\n\n"
                        f"Change your password after first login in Profile Settings.\n\n"
                        f"Opening browser now..."
                    )
                    try:
                        self.root.clipboard_clear()
                        self.root.clipboard_append(
                            f"Username: {alloc_user}\nPassword: {alloc_pass}"
                        )
                    except Exception:
                        pass
                else:
                    cred_msg = "Request approved! Opening contained environment now."
                messagebox.showinfo(APP_NAME, cred_msg)
                self.open_app()
                return
            elif new_status == "rejected":
                messagebox.showwarning(APP_NAME, "Join request was rejected by the server admin.")
                return
            else:
                # Still pending -- reschedule
                self._start_poll()
        except Exception:
            # Silently reschedule on network errors
            self._start_poll()

    def request_join(self) -> None:
        try:
            server_url = self._validate_server_url()
            if _is_loopback_server_url(server_url):
                proceed = messagebox.askyesno(
                    APP_NAME,
                    f"Server URL is loopback ({server_url}).\nThis points to this local machine, not a remote server.\nContinue anyway?",
                )
                if not proceed:
                    return
            target_id = self.target_server_id.get().strip().lower()
            if not target_id:
                raise ValueError("Target Server ID is required")
            # Hash the password client-side so it is never sent in plaintext
            raw_password = self.requested_password.get()
            password_hash = None
            if raw_password:
                ph = argon2.PasswordHasher()
                password_hash = ph.hash(raw_password)
                self.requested_password.set("")  # clear from memory
            payload = {
                "target_server_id": target_id,
                "peer_server_id": self.cfg.client_id,
                "requested_username": self.requested_username.get().strip() or None,
                "requested_display_name": self.requested_display_name.get().strip() or None,
                "note": self.request_note.get().strip() or None,
                "password_hash": password_hash,
            }
            resp, resolved_server_url = _request_with_server_fallback(
                "POST",
                server_url,
                "/.well-known/handshake",
                timeout=15,
                json_payload=payload,
            )
            data = resp.json()
            if not resp.ok or not data.get("ok"):
                detail = data.get("detail", f"HTTP {resp.status_code}")
                raise RuntimeError(detail)
            if resolved_server_url != server_url:
                self.server_url.set(resolved_server_url)

            self.request_id.set(str(data.get("request_id", "")))
            self.request_status.set(str(data.get("status", "unknown")))
            self.save_config()
            if self.request_status.get() == "approved":
                alloc_user = data.get("allocated_username") or ""
                has_password = data.get("has_password", False)
                alloc_pass = data.get("allocated_password") or ""
                if has_password:
                    cred_msg = (
                        f"Join approved!\n\n"
                        f"  Username: {alloc_user}\n\n"
                        f"You set your own password during the join request.\n\n"
                        f"Opening browser now..."
                    )
                elif alloc_user and alloc_pass:
                    cred_msg = (
                        f"Join approved!\n\n"
                        f"Your login credentials:\n"
                        f"  Username: {alloc_user}\n"
                        f"  Temporary Password: {alloc_pass}\n\n"
                        f"Change your password after first login.\n\n"
                        f"Opening browser now..."
                    )
                    try:
                        self.root.clipboard_clear()
                        self.root.clipboard_append(
                            f"Username: {alloc_user}\nPassword: {alloc_pass}"
                        )
                    except Exception:
                        pass
                else:
                    cred_msg = "Join approved immediately. Opening contained environment now."
                messagebox.showinfo(APP_NAME, cred_msg)
                self.open_app()
                return
            messagebox.showinfo(
                APP_NAME,
                f"Join request sent.\nStatus: {self.request_status.get()}\nRequest ID: {self.request_id.get()}\n\nAuto-checking every 20 seconds...",
            )
            self._start_poll()
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Join request failed: {e}\nURL used: {self.server_url.get().strip()}")

    def check_status(self) -> None:
        try:
            server_url = self._validate_server_url()
            if _is_loopback_server_url(server_url):
                proceed = messagebox.askyesno(
                    APP_NAME,
                    f"Status check URL is loopback ({server_url}).\nThis points to this local machine, not a remote server.\nContinue anyway?",
                )
                if not proceed:
                    return
            request_id = self.request_id.get().strip()
            if not request_id:
                raise ValueError("Request ID is required")
            resp, resolved_server_url = _request_with_server_fallback(
                "GET",
                server_url,
                f"/.well-known/join-requests/{request_id}",
                timeout=15,
                params={"peer_server_id": self.cfg.client_id},
            )
            data = resp.json()
            if not resp.ok:
                detail = data.get("detail", f"HTTP {resp.status_code}")
                # Stale request from a previous client install -- clear it
                if resp.status_code == 403 and "mismatch" in detail.lower():
                    self.request_id.set("")
                    self.request_status.set("not_requested")
                    self.save_config()
                    messagebox.showwarning(
                        APP_NAME,
                        "Previous join request belongs to a different client installation.\n"
                        "It has been cleared. Click 'Request to Join' to create a new one.",
                    )
                    return
                raise RuntimeError(detail)
            if resolved_server_url != server_url:
                self.server_url.set(resolved_server_url)
            self.request_status.set(str(data.get("status", "unknown")))
            self.save_config()
            if self.request_status.get() == "approved" and not self.auto_opened:
                self.auto_opened = True
                # Show credentials if the server returned them
                alloc_user = data.get("allocated_username") or ""
                alloc_pass = data.get("allocated_password") or ""
                if alloc_user and alloc_pass:
                    cred_msg = (
                        f"Request approved!\n\n"
                        f"Your login credentials:\n"
                        f"  Username: {alloc_user}\n"
                        f"  Temporary Password: {alloc_pass}\n\n"
                        f"Change your password after first login in Profile Settings.\n\n"
                        f"Opening browser now..."
                    )
                    # Also copy to clipboard
                    try:
                        self.root.clipboard_clear()
                        self.root.clipboard_append(
                            f"Username: {alloc_user}\nPassword: {alloc_pass}"
                        )
                    except Exception:
                        pass
                else:
                    cred_msg = "Request approved. Opening contained environment now."
                messagebox.showinfo(APP_NAME, cred_msg)
                self.open_app()
                return
            messagebox.showinfo(APP_NAME, f"Request status: {self.request_status.get()}")
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Status check failed: {e}\nURL used: {self.server_url.get().strip()}")

    def connect_and_open(self) -> None:
        """
        One-click flow:
        - If no request exists, create one.
        - If approved, open immediately.
        - If pending, tell user to wait for admin approval.
        """
        try:
            request_id = self.request_id.get().strip()
            if not request_id:
                self.request_join()
                return
            self.check_status()
            if self.request_status.get() == "approved":
                self.open_app()
            elif self.request_status.get() == "pending":
                messagebox.showinfo(
                    APP_NAME,
                    "Join request is pending admin approval. Ask the server admin to approve, then click Connect + Open again.",
                )
            elif self.request_status.get() == "rejected":
                messagebox.showwarning(
                    APP_NAME,
                    "Join request was rejected by the server admin.",
                )
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Connect failed: {e}")

    def open_app(self) -> None:
        try:
            self.save_config()
            open_contained_browser(self.server_url.get())
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Failed to open contained app: {e}")

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    ClientApp().run()
HOMEPAGE_CLIENT_APP_PY_EOF

echo "==> Writing requirements-client.txt ..."
cat << 'HOMEPAGE_REQUIREMENTS_EOF' > requirements-client.txt
requests>=2.31.0
pywebview>=5.1
argon2-cffi>=23.1.0
pyobjc-framework-WebKit>=10.0
HOMEPAGE_REQUIREMENTS_EOF

echo "==> Writing HOMEPAGEClient-macos.spec ..."
cat << 'HOMEPAGE_SPEC_EOF' > HOMEPAGEClient-macos.spec
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
        'gi', 'gtk',
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
    upx=False,
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
HOMEPAGE_SPEC_EOF

# ── 3. Create venv and install dependencies ───────────────────────────────
echo ""
echo "==> Creating Python virtual environment..."
python3 -m venv .venv-client
source .venv-client/bin/activate
python -m pip install --upgrade pip

echo ""
echo "==> Installing dependencies (this may take a few minutes)..."
pip install -r requirements-client.txt
pip install pyinstaller

# ── 4. Run PyInstaller ─────────────────────────────────────────────────────
echo ""
echo "==> Running PyInstaller..."
pyinstaller HOMEPAGEClient-macos.spec --noconfirm
echo "==> PyInstaller done."

# ── 5. Create DMG ─────────────────────────────────────────────────────────
echo ""
echo "==> Creating DMG..."
DMG_DIR="$WORK_DIR/build/dmg-stage"
rm -rf "$DMG_DIR"
mkdir -p "$DMG_DIR"

# Copy the .app bundle
cp -a "dist/$APP_NAME.app" "$DMG_DIR/"

# Create a symlink to /Applications for drag-and-drop install
ln -s /Applications "$DMG_DIR/Applications"

# Build the DMG
hdiutil create -volname "$APP_NAME" \
    -srcfolder "$DMG_DIR" \
    -ov -format UDZO \
    "dist/$DMG_NAME"

# ── 6. Copy outputs to the script's directory ─────────────────────────────
echo ""
echo "==> Copying outputs..."
mkdir -p "$SCRIPT_DIR/dist"
cp "dist/$DMG_NAME" "$SCRIPT_DIR/dist/"
cp -a "dist/$APP_NAME.app" "$SCRIPT_DIR/dist/"

echo ""
echo "============================================"
echo "  BUILD COMPLETE"
echo "============================================"
echo ""
echo "Outputs:"
echo "  $SCRIPT_DIR/dist/$DMG_NAME"
echo "  $SCRIPT_DIR/dist/$APP_NAME.app/"
echo ""
ls -lh "$SCRIPT_DIR/dist/$DMG_NAME"
echo ""
echo "Send the .dmg file back to the server admin."
echo "They should place it at: dist/HomepageClient-macos.dmg"
echo ""
echo "You can delete the build directory if you like:"
echo "  rm -rf $WORK_DIR"
