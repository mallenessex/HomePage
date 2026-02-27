"""
Utility functions for server settings management.
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import text
from typing import Optional, List, Any
import json
import ipaddress
import re
from pathlib import Path
from urllib.parse import urlparse
from . import server_models
from .config import settings


_LOCAL_DOMAIN_SANITIZE_RE = re.compile(r"[^a-z0-9\-.]+")
_LOCAL_DOMAIN_COLLAPSE_DOT_RE = re.compile(r"\.+")
_LOCAL_DOMAIN_COLLAPSE_HYPHEN_RE = re.compile(r"-+")


def normalize_secure_local_domain(raw: Optional[str]) -> Optional[str]:
    """
    Normalize an input string to a safe `.local` hostname.
    """
    value = (raw or "").strip().lower()
    if not value:
        return None
    parsed_host = None
    try:
        parsed = urlparse(value)
        if parsed.hostname:
            parsed_host = parsed.hostname
        elif "://" not in value and ("/" in value or ":" in value):
            fallback = urlparse(f"//{value}")
            if fallback.hostname:
                parsed_host = fallback.hostname
    except Exception:
        parsed_host = None
    if parsed_host:
        value = parsed_host.lower().strip()
    elif ":" in value and value.count(":") == 1:
        # host:port format without scheme
        value = value.split(":", 1)[0].strip()
    value = value.replace("_", "-").replace(" ", "-")
    value = _LOCAL_DOMAIN_SANITIZE_RE.sub("", value)
    value = _LOCAL_DOMAIN_COLLAPSE_DOT_RE.sub(".", value)
    value = _LOCAL_DOMAIN_COLLAPSE_HYPHEN_RE.sub("-", value)
    value = value.strip(".-")
    if not value:
        return None
    if value.endswith(".local"):
        value = value[:-6]
    value = value.strip(".-")
    if not value:
        return None
    return f"{value}.local"


def normalize_secure_local_ip(raw: Optional[str]) -> Optional[str]:
    value = (raw or "").strip()
    if not value:
        return None
    host = value
    try:
        parsed = urlparse(value)
        if parsed.hostname:
            host = parsed.hostname
        elif "://" not in value and ("/" in value or ":" in value):
            fallback = urlparse(f"//{value}")
            if fallback.hostname:
                host = fallback.hostname
    except Exception:
        host = value
    host = (host or "").strip().lower().strip("[]")
    if host == "localhost":
        return "127.0.0.1"
    if ":" in host and host.count(":") == 1:
        # host:port without scheme
        maybe_host, maybe_port = host.rsplit(":", 1)
        if maybe_port.isdigit():
            host = maybe_host
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return None
    if ip.version != 4:
        return None
    return str(ip)


def local_ca_cert_path() -> Path:
    return Path(settings.BASE_DIR) / "data" / "caddy-data" / "caddy" / "pki" / "authorities" / "local" / "root.crt"


def runtime_secure_mode_env_path() -> Path:
    return Path(settings.BASE_DIR) / "data" / "runtime_secure_mode.env"


def _truthy_int_flag(value: Any) -> int:
    return 1 if str(value).strip().lower() in {"1", "true", "yes", "on"} else 0


def _render_runtime_secure_mode_env(settings_row: server_models.ServerSettings) -> str:
    enabled = _truthy_int_flag(getattr(settings_row, "secure_mode_enabled", 0))
    domain = normalize_secure_local_domain(getattr(settings_row, "secure_local_domain", None)) or ""
    ip = normalize_secure_local_ip(getattr(settings_row, "secure_local_ip", None)) or ""
    return (
        f"SECURE_MODE_ENABLED={enabled}\n"
        f"SECURE_LOCAL_DOMAIN={domain}\n"
        f"SECURE_LOCAL_IP={ip}\n"
    )


def sync_runtime_secure_mode_env(settings_row: server_models.ServerSettings) -> None:
    path = runtime_secure_mode_env_path()
    content = _render_runtime_secure_mode_env(settings_row)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if existing == content:
                return
        path.write_text(content, encoding="utf-8")
    except Exception:
        # Runtime env sync should not block app startup/settings updates.
        pass

async def ensure_server_settings_schema(db: AsyncSession):
    """
    Backfill additive columns in server_settings for existing SQLite databases.
    """
    try:
        rows = await db.execute(text("PRAGMA table_info(server_settings)"))
        columns = {row[1] for row in rows.fetchall()}
        if "external_join_policy" not in columns:
            await db.execute(
                text("ALTER TABLE server_settings ADD COLUMN external_join_policy VARCHAR DEFAULT 'conditional'")
            )
            await db.commit()
        if "secure_mode_enabled" not in columns:
            await db.execute(
                text("ALTER TABLE server_settings ADD COLUMN secure_mode_enabled INTEGER DEFAULT 0")
            )
            await db.commit()
        if "secure_local_domain" not in columns:
            await db.execute(
                text("ALTER TABLE server_settings ADD COLUMN secure_local_domain VARCHAR")
            )
            await db.commit()
        if "secure_local_ip" not in columns:
            await db.execute(
                text("ALTER TABLE server_settings ADD COLUMN secure_local_ip VARCHAR")
            )
            await db.commit()
        if "external_server_url" not in columns:
            await db.execute(
                text("ALTER TABLE server_settings ADD COLUMN external_server_url VARCHAR")
            )
            await db.commit()
    except Exception:
        # Keep server operational even if schema patch cannot run.
        pass

    # Backfill join_requests table
    try:
        rows = await db.execute(text("PRAGMA table_info(join_requests)"))
        columns = {row[1] for row in rows.fetchall()}
        if "allocated_password" not in columns:
            await db.execute(
                text("ALTER TABLE join_requests ADD COLUMN allocated_password VARCHAR")
            )
            await db.commit()
    except Exception:
        pass

async def get_server_settings(db: AsyncSession) -> server_models.ServerSettings:
    """
    Get the server settings. Creates default settings if none exist.
    """
    await ensure_server_settings_schema(db)
    result = await db.execute(select(server_models.ServerSettings))
    settings = result.scalar_one_or_none()
    
    if not settings:
        # Create default settings
        settings = server_models.ServerSettings(
            server_name="Family Node",
            server_description="A family-safe social network",
            forbidden_tags="",
            warning_tags="",
            federation_enabled=1,
            allow_external_follows=0,
            external_join_policy="conditional",
            secure_mode_enabled=0,
            secure_local_domain=None,
            secure_local_ip=None,
            external_server_url=None,
        )
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    sync_runtime_secure_mode_env(settings)
    return settings

async def update_server_settings(
    db: AsyncSession,
    server_name: Optional[str] = None,
    server_description: Optional[str] = None,
    forbidden_tags: Optional[str] = None,
    warning_tags: Optional[str] = None,
    federation_enabled: Optional[bool] = None,
    allow_external_follows: Optional[bool] = None,
    external_join_policy: Optional[str] = None,
    secure_mode_enabled: Optional[bool] = None,
    secure_local_domain: Optional[str] = None,
    secure_local_ip: Optional[str] = None,
    external_server_url: Optional[str] = None,
) -> server_models.ServerSettings:
    """
    Update server settings.
    """
    await ensure_server_settings_schema(db)
    settings = await get_server_settings(db)
    
    if server_name is not None:
        settings.server_name = server_name
    if server_description is not None:
        settings.server_description = server_description
    if forbidden_tags is not None:
        settings.forbidden_tags = forbidden_tags
    if warning_tags is not None:
        settings.warning_tags = warning_tags
    if federation_enabled is not None:
        settings.federation_enabled = 1 if federation_enabled else 0
    if allow_external_follows is not None:
        settings.allow_external_follows = 1 if allow_external_follows else 0
    if external_join_policy is not None:
        policy = external_join_policy.strip().lower()
        if policy in {"conditional", "none", "all"}:
            settings.external_join_policy = policy
    if secure_mode_enabled is not None:
        settings.secure_mode_enabled = 1 if secure_mode_enabled else 0
    if secure_local_domain is not None:
        settings.secure_local_domain = normalize_secure_local_domain(secure_local_domain)
    if secure_local_ip is not None:
        settings.secure_local_ip = normalize_secure_local_ip(secure_local_ip)
    if external_server_url is not None:
        settings.external_server_url = external_server_url.strip() or None

    if _truthy_int_flag(settings.secure_mode_enabled) and not (settings.secure_local_domain or "").strip():
        settings.secure_local_domain = normalize_secure_local_domain(settings.server_name) or "house-fantastico.local"
    
    await db.commit()
    await db.refresh(settings)
    sync_runtime_secure_mode_env(settings)
    return settings

def get_forbidden_tags_list(settings: server_models.ServerSettings) -> List[str]:
    """
    Parse forbidden tags from comma-separated string.
    """
    if not settings.forbidden_tags:
        return []
    return [tag.strip().lower() for tag in settings.forbidden_tags.split(',') if tag.strip()]

def get_warning_tags_list(settings: server_models.ServerSettings) -> List[str]:
    """
    Parse warning tags from comma-separated string.
    """
    if not settings.warning_tags:
        return []
    return [tag.strip().lower() for tag in settings.warning_tags.split(',') if tag.strip()]

def is_content_forbidden(content: str, settings: server_models.ServerSettings) -> tuple[bool, Optional[str]]:
    """
    Check if content contains forbidden tags.
    Returns (is_forbidden, matched_tag)
    """
    forbidden = get_forbidden_tags_list(settings)
    content_lower = content.lower()
    
    for tag in forbidden:
        if tag in content_lower:
            return True, tag
    
    return False, None

def has_warning_tags(content: str, settings: server_models.ServerSettings) -> List[str]:
    """
    Check if content contains warning tags.
    Returns list of matched warning tags.
    """
    warnings = get_warning_tags_list(settings)
    content_lower = content.lower()
    
    matched = []
    for tag in warnings:
        if tag in content_lower:
            matched.append(tag)
    
    return matched

async def log_policy_violation(
    db: AsyncSession,
    user_id: int,
    violation_type: str,
    matched_tag: str,
    content: str,
    action_taken: str
):
    """
    Log a policy violation to the database.
    """
    violation = server_models.PolicyViolation(
        user_id=user_id,
        violation_type=violation_type,
        matched_tag=matched_tag,
        content_preview=content[:200],
        action_taken=action_taken
    )
    db.add(violation)
    await db.commit()

async def check_content_policy(
    db: AsyncSession,
    user_id: int,
    content: str,
    action_if_forbidden: str = "blocked"
):
    """
    Utility to check content against server policies and log violations.
    Raises HTTPException (400) if content is forbidden.
    """
    from fastapi import HTTPException
    
    # 1. Get settings
    settings = await get_server_settings(db)
    
    # 2. Check forbidden tags
    is_forbidden, matched_forbidden = is_content_forbidden(content, settings)
    if is_forbidden:
        # Log the violation
        await log_policy_violation(
            db=db,
            user_id=user_id,
            violation_type="forbidden",
            matched_tag=matched_forbidden,
            content=content,
            action_taken=action_if_forbidden
        )
        raise HTTPException(
            status_code=400, 
            detail=f"Post contains forbidden content (matched: {matched_forbidden})."
        )

    # 3. Check warning tags
    warning_tags = has_warning_tags(content, settings)
    if warning_tags:
        for tag in warning_tags:
            await log_policy_violation(
                db=db,
                user_id=user_id,
                violation_type="warning",
                matched_tag=tag,
                content=content,
                action_taken="flagged"
            )

async def get_all_modules(db: AsyncSession) -> List[server_models.PlatformModule]:
    """Get all platform modules."""
    result = await db.execute(select(server_models.PlatformModule))
    return result.scalars().all()

async def is_module_enabled(db: AsyncSession, module_name: str) -> bool:
    """Check if a module is enabled."""
    result = await db.execute(
        select(server_models.PlatformModule).where(server_models.PlatformModule.name == module_name)
    )
    module = result.scalar_one_or_none()
    return module and module.is_enabled == 1

async def get_active_modules(db: AsyncSession) -> List[server_models.PlatformModule]:
    """Get all enabled platform modules."""
    # Primary path for normal INTEGER 1/0 rows.
    result = await db.execute(
        select(server_models.PlatformModule).where(server_models.PlatformModule.is_enabled == 1)
    )
    modules = result.scalars().all()
    if modules:
        return modules

    # Compatibility fallback for legacy/mixed truthy values in SQLite installs.
    all_result = await db.execute(select(server_models.PlatformModule))
    all_modules = all_result.scalars().all()
    return [
        module
        for module in all_modules
        if str(getattr(module, "is_enabled", "")).strip().lower() in {"1", "true", "yes", "on"}
    ]

def parse_hidden_module_names(module_bar_config: Optional[str]) -> set[str]:
    """
    Parse hidden module names from persisted module_bar_config JSON.
    Falls back to empty set for missing/malformed values.
    """
    if not module_bar_config:
        return set()
    try:
        parsed: Any = json.loads(module_bar_config)
        raw_hidden = parsed.get("hidden_modules", []) if isinstance(parsed, dict) else []
        if not isinstance(raw_hidden, list):
            return set()
        return {
            str(module_name).strip().lower()
            for module_name in raw_hidden
            if str(module_name).strip()
        }
    except Exception:
        return set()

def filter_visible_module_bar_modules(active_modules: list, module_bar_config: Optional[str]) -> list:
    """
    Filter active modules for top module bar rendering based on user preference.
    """
    hidden_names = parse_hidden_module_names(module_bar_config)
    if not hidden_names:
        return active_modules
    return [
        module for module in active_modules
        if (getattr(module, "name", "") or "").strip().lower() not in hidden_names
    ]

async def init_default_modules(db: AsyncSession):
    """Ensure default modules exist and are enabled."""
    defaults = [
        {
            "name": "feed",
            "display_name": "Community Feed",
            "description": "See what your family and friends are sharing.",
            "icon": "💬",
            "color": "#4f46e5",
            "is_enabled": 1
        },
        {
            "name": "discovery",
            "display_name": "Discovery",
            "description": "Find and follow users across the federated network.",
            "icon": "🌍",
            "color": "#10b981",
            "is_enabled": 1
        },
        {
            "name": "calendar",
            "display_name": "Family Calendar",
            "description": "Coordinate events, birthdays, and family gatherings.",
            "icon": "📅",
            "color": "#ef4444",
            "is_enabled": 0
        },
        {
            "name": "games",
            "display_name": "Local Games",
            "description": "Play simple games with other people on your node.",
            "icon": "🎮",
            "color": "#8b5cf6",
            "is_enabled": 0
        },
        {
            "name": "wikipedia",
            "display_name": "Wikipedia",
            "description": "Browse millions of articles from the world's encyclopedia, completely offline.",
            "icon": "📚",
            "color": "#333333",
            "is_enabled": 0
        },
        {
            "name": "voice",
            "display_name": "Voice Chat",
            "description": "Encrypted voice rooms using WebRTC signaling and TURN relay support.",
            "icon": "🎙️",
            "color": "#0ea5e9",
            "is_enabled": 0
        },
        {
            "name": "mail",
            "display_name": "Mail",
            "description": "Private mailbox with local delivery and federated delivery scaffolding.",
            "icon": "✉️",
            "color": "#0f766e",
            "is_enabled": 0
        },
        {
            "name": "chat",
            "display_name": "Chat Rooms",
            "description": "Server/channel chat with roles, emoji, and voice channel integration.",
            "icon": "⌨️",
            "color": "#2563eb",
            "is_enabled": 0
        },
        {
            "name": "create",
            "display_name": "Create",
            "description": "Build your own web pages — GeoCities style! Drag-and-drop editor with custom HTML & CSS.",
            "icon": "🌐",
            "color": "#f59e0b",
            "is_enabled": 0
        }
    ]
    
    for d in defaults:
        result = await db.execute(
            select(server_models.PlatformModule).where(server_models.PlatformModule.name == d["name"])
        )
        existing = result.scalar_one_or_none()
        if not existing:
            mod = server_models.PlatformModule(**d)
            db.add(mod)
            continue
        if d["name"] == "chat" and (existing.icon or "").strip() != "⌨️":
            existing.icon = "⌨️"
    
    await db.commit()
