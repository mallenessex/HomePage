"""
First-run setup wizard.

Activates only when no admin user exists yet (fresh database).
Once an admin account is created the wizard redirects to the admin panel
and never appears again.

Routes
------
GET  /setup           → render the multi-step wizard
POST /setup/complete  → process wizard answers, create admin, seed config
"""
from __future__ import annotations

import json
import socket
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from .. import database, models, schemas, crud_users, auth_utils, server_utils, server_identity
from ..config import settings

router = APIRouter(tags=["setup"])

templates = Jinja2Templates(directory=str(settings.BASE_DIR / "templates"))

# ── Module catalogue (mirrors init_default_modules) ────────────────

AVAILABLE_MODULES = [
    {
        "name": "feed",
        "display_name": "Community Feed",
        "description": "See what your family and friends are sharing.",
        "icon": "💬",
        "always_on": True,
    },
    {
        "name": "discovery",
        "display_name": "Discovery",
        "description": "Find and follow users across the federated network.",
        "icon": "🌍",
        "always_on": True,
    },
    {
        "name": "calendar",
        "display_name": "Family Calendar",
        "description": "Coordinate events, birthdays, and family gatherings.",
        "icon": "📅",
    },
    {
        "name": "games",
        "display_name": "Local Games",
        "description": "Play simple games with other people on your node.",
        "icon": "🎮",
    },
    {
        "name": "wikipedia",
        "display_name": "Wikipedia",
        "description": "Browse millions of articles completely offline. Requires a ~22 GB data dump — can be added later.",
        "icon": "📚",
    },
    {
        "name": "voice",
        "display_name": "Voice Chat",
        "description": "Encrypted voice rooms using WebRTC.",
        "icon": "🎙️",
    },
    {
        "name": "mail",
        "display_name": "Mail",
        "description": "Private mailbox with local and federated delivery.",
        "icon": "✉️",
    },
    {
        "name": "chat",
        "display_name": "Chat Rooms",
        "description": "Server/channel chat with roles, emoji, and voice integration.",
        "icon": "⌨️",
    },
    {
        "name": "create",
        "display_name": "Create",
        "description": "Build your own web pages — GeoCities style!",
        "icon": "🌐",
    },
]


# ── Helpers ─────────────────────────────────────────────────────────

async def _has_admin(db: AsyncSession) -> bool:
    """Return True if at least one admin user exists."""
    result = await db.execute(
        select(func.count(models.User.id)).where(models.User.is_admin == True)  # noqa: E712
    )
    return (result.scalar() or 0) > 0


def _detect_lan_ip() -> str:
    """Best-effort LAN IP detection."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# ── GET /setup — render wizard ──────────────────────────────────────

@router.get("/setup", response_class=HTMLResponse)
async def setup_wizard(
    request: Request,
    db: AsyncSession = Depends(database.get_db),
):
    if await _has_admin(db):
        return RedirectResponse(url="/", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="setup_wizard.html",
        context={
            "available_modules": AVAILABLE_MODULES,
            "detected_lan_ip": _detect_lan_ip(),
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
        },
    )


# ── POST /setup/complete — process wizard answers ───────────────────

@router.post("/setup/complete")
async def setup_complete(
    request: Request,
    db: AsyncSession = Depends(database.get_db),
):
    # Guard: only works if no admin exists yet
    if await _has_admin(db):
        return RedirectResponse(url="/", status_code=303)

    body = await request.json()

    # ── Step 2: Server Identity ──
    server_name = (body.get("server_name") or "My HOMEPAGE Server").strip()[:200]
    server_description = (body.get("server_description") or "").strip()[:1000]
    platform_name = (body.get("platform_name") or "HOMEPAGE").strip()[:100]

    # ── Step 3: Modules ──
    enabled_modules = body.get("enabled_modules", [])
    if not isinstance(enabled_modules, list):
        enabled_modules = []
    # Always include feed + discovery
    base_modules = {"feed", "discovery"}
    clean_modules = sorted(
        base_modules | {str(m).strip().lower() for m in enabled_modules if str(m).strip()}
    )

    # ── Step 4: Admin Account ──
    admin_username = (body.get("admin_username") or "admin").strip()[:50]
    admin_display_name = (body.get("admin_display_name") or "Administrator").strip()[:100]
    admin_password = (body.get("admin_password") or "").strip()
    if not admin_password or len(admin_password) < 4:
        return JSONResponse(
            {"ok": False, "error": "Password must be at least 4 characters."},
            status_code=400,
        )

    # Check for duplicate username
    existing = await crud_users.get_user_by_username(db, admin_username)
    if existing:
        return JSONResponse(
            {"ok": False, "error": f'Username "{admin_username}" already exists.'},
            status_code=400,
        )

    # ── Step 5: Network ──
    network_mode = (body.get("network_mode") or "lan").strip().lower()  # "lan" or "external"
    external_url = (body.get("external_url") or "").strip()
    join_policy = (body.get("join_policy") or "conditional").strip().lower()
    if join_policy not in {"conditional", "none", "all"}:
        join_policy = "conditional"

    # ── Apply: Write enabled_modules.json ──
    modules_file = settings.BASE_DIR / "data" / "enabled_modules.json"
    modules_file.parent.mkdir(parents=True, exist_ok=True)
    modules_file.write_text(json.dumps(clean_modules, indent=2), encoding="utf-8")

    # ── Apply: Update server settings ──
    await server_utils.update_server_settings(
        db=db,
        server_name=server_name,
        server_description=server_description,
        federation_enabled=(network_mode == "external"),
        allow_external_follows=(network_mode == "external"),
        external_join_policy=join_policy,
        external_server_url=external_url if network_mode == "external" else None,
    )

    # ── Apply: Seed modules in DB ──
    await server_utils.init_default_modules(db)

    # Enable/disable modules in the DB to match the wizard selection
    from .. import server_models
    res = await db.execute(select(server_models.PlatformModule))
    all_mods = res.scalars().all()
    for mod in all_mods:
        if mod.name in {"feed", "discovery"}:
            mod.is_enabled = 1
        elif mod.name in set(clean_modules):
            mod.is_enabled = 1
        else:
            mod.is_enabled = 0
    await db.commit()

    # ── Apply: Create admin user ──
    user_in = schemas.UserCreate(
        username=admin_username,
        password=admin_password,
        display_name=admin_display_name,
    )
    new_user = await crud_users.create_user(db=db, user=user_in)
    new_user.is_admin = True
    await db.commit()

    # ── Auto-login the new admin ──
    from datetime import timedelta
    access_token = auth_utils.create_access_token(
        data={"sub": new_user.username},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )

    return JSONResponse({
        "ok": True,
        "access_token": f"Bearer {access_token}",
        "redirect": "/admin/settings",
    })


# ── GET /setup/check — lightweight probe for the launcher ────────────────

@router.get("/setup/check")
async def setup_check(db: AsyncSession = Depends(database.get_db)):
    """Return whether setup is still needed (used by server_launcher.py)."""
    return {"setup_needed": not await _has_admin(db)}
