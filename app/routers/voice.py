import asyncio
import json
import re
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import auth_utils, crud_users, database, models, server_models, server_utils
from ..config import settings
from . import auth

router = APIRouter(prefix="/voice", tags=["voice"])
templates = Jinja2Templates(directory=str(settings.BASE_DIR / "templates"))

ROOM_CONNECTIONS: dict[str, set[WebSocket]] = {}
ROOM_LOCK = asyncio.Lock()


def _slugify(value: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return base or "voice-room"


def _parse_config_json(raw: Optional[str]) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


async def _get_voice_module(db: AsyncSession):
    result = await db.execute(
        select(server_models.PlatformModule).where(server_models.PlatformModule.name == "voice")
    )
    return result.scalar_one_or_none()


async def _get_voice_config(db: AsyncSession) -> dict:
    module = await _get_voice_module(db)
    if not module:
        return {}
    return _parse_config_json(module.config_json)


async def _ensure_default_room(db: AsyncSession, cfg: dict, created_by: str) -> None:
    default_room = cfg.get("default_room")
    if not isinstance(default_room, dict):
        return
    slug = default_room.get("slug")
    name = default_room.get("name")
    if not slug or not name:
        return

    result = await db.execute(select(models.VoiceRoom).where(models.VoiceRoom.slug == slug))
    if result.scalar_one_or_none():
        return

    room = models.VoiceRoom(
        slug=slug,
        name=name,
        description=default_room.get("description"),
        created_by_username=created_by,
    )
    db.add(room)
    await db.commit()


def _token_from_websocket(websocket: WebSocket) -> Optional[str]:
    token = websocket.query_params.get("token")
    if token:
        return token
    cookie_token = websocket.cookies.get("access_token")
    if cookie_token and cookie_token.startswith("Bearer "):
        return cookie_token[7:]
    return None


async def _authenticate_websocket(websocket: WebSocket, db: AsyncSession) -> Optional[models.User]:
    token = _token_from_websocket(websocket)
    if not token:
        return None
    try:
        payload = auth_utils.jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        username = payload.get("sub")
        if not username:
            return None
    except auth_utils.JWTError:
        return None
    return await crud_users.get_user_by_username(db, username=username)


@router.get("/", response_class=HTMLResponse)
async def voice_home(
    request: Request,
    current_user: Optional[models.User] = Depends(auth.get_current_user_optional),
    db: AsyncSession = Depends(database.get_db),
):
    active_modules = await server_utils.get_active_modules(db)
    voice_cfg = await _get_voice_config(db)
    await _ensure_default_room(db, voice_cfg, current_user.username if current_user else "system")
    result = await db.execute(select(models.VoiceRoom).order_by(models.VoiceRoom.created_at.desc()))
    rooms = result.scalars().all()
    return templates.TemplateResponse(
        request=request,
        name="voice.html",
        context={
            "user": current_user,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
            "active_modules": active_modules,
            "rooms": rooms,
            "voice_config": voice_cfg,
        },
    )


@router.post("/rooms/create")
async def create_room(
    name: str = Form(...),
    description: str = Form(""),
    current_user: models.User = Depends(auth.get_current_user),
    db: AsyncSession = Depends(database.get_db),
):
    slug_base = _slugify(name)
    slug = slug_base
    counter = 1
    while True:
        result = await db.execute(select(models.VoiceRoom).where(models.VoiceRoom.slug == slug))
        if not result.scalar_one_or_none():
            break
        counter += 1
        slug = f"{slug_base}-{counter}"

    room = models.VoiceRoom(
        slug=slug,
        name=name.strip(),
        description=description.strip() or None,
        created_by_username=current_user.username,
    )
    db.add(room)
    await db.commit()
    return RedirectResponse(url=f"/voice/rooms/{room.slug}", status_code=303)


@router.get("/rooms/{room_slug}", response_class=HTMLResponse)
async def room_page(
    room_slug: str,
    request: Request,
    embed: bool = False,
    current_user: models.User = Depends(auth.get_current_user),
    db: AsyncSession = Depends(database.get_db),
):
    result = await db.execute(select(models.VoiceRoom).where(models.VoiceRoom.slug == room_slug))
    room = result.scalar_one_or_none()
    if not room:
        raise HTTPException(status_code=404, detail="Voice room not found")

    voice_cfg = await _get_voice_config(db)
    active_modules = await server_utils.get_active_modules(db)
    ws_scheme = "wss" if request.url.scheme == "https" else "ws"
    ws_url = f"{ws_scheme}://{request.headers.get('host')}/voice/ws/{room.slug}"
    ice_servers = voice_cfg.get(
        "ice_servers",
        [],  # No external STUN/TURN by default — use only the local coturn server
    )
    return templates.TemplateResponse(
        request=request,
        name="voice_room.html",
        context={
            "user": current_user,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
            "active_modules": active_modules,
            "room": room,
            "ws_url": ws_url,
            "ice_servers_json": json.dumps(ice_servers),
            "embed_minimal": embed,
        },
    )


@router.websocket("/ws/{room_slug}")
async def room_ws(
    room_slug: str,
    websocket: WebSocket,
):
    await websocket.accept()
    async with database.AsyncSessionLocal() as db:
        user = await _authenticate_websocket(websocket, db)
    if not user:
        await websocket.send_text(json.dumps({"type": "error", "message": "Unauthorized"}))
        await websocket.close(code=4401)
        return

    async with ROOM_LOCK:
        ROOM_CONNECTIONS.setdefault(room_slug, set()).add(websocket)
    try:
        await websocket.send_text(
            json.dumps(
                {
                    "type": "system",
                    "message": "connected",
                    "you": user.username,
                    "you_avatar_url": user.avatar_url,
                }
            )
        )
        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            payload["sender"] = user.username
            payload["sender_avatar_url"] = user.avatar_url
            out = json.dumps(payload)
            async with ROOM_LOCK:
                peers = list(ROOM_CONNECTIONS.get(room_slug, set()))
            for peer in peers:
                if peer is websocket:
                    continue
                try:
                    await peer.send_text(out)
                except Exception:
                    pass
    except WebSocketDisconnect:
        pass
    finally:
        async with ROOM_LOCK:
            peers = ROOM_CONNECTIONS.get(room_slug)
            if peers and websocket in peers:
                peers.remove(websocket)
            if peers is not None and len(peers) == 0:
                ROOM_CONNECTIONS.pop(room_slug, None)
