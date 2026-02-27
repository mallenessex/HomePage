import re
from collections import defaultdict
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from .. import database, media_utils, models, permissions, server_utils
from ..chat_permissions import (
    ALL_PERMISSIONS,
    DEFAULT_MEMBER_PERMISSIONS,
    PERM_ADMIN,
    PERM_CONNECT_VOICE,
    PERM_MANAGE_CHANNELS,
    PERM_MANAGE_EMOJIS,
    PERM_MANAGE_ROLES,
    PERM_SEND_MESSAGES,
    PERM_SPEAK_VOICE,
    PERM_VIEW_CHANNEL,
)
from ..config import settings
from . import auth

router = APIRouter(prefix="/chat-ex", tags=["chat-ex"])
templates = Jinja2Templates(directory=str(settings.BASE_DIR / "templates"))


def _slugify(value: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return base or "channel"


def _normalize_chat_view(view: Optional[str]) -> str:
    return "experimental" if (view or "").strip().lower() in {"experimental", "exp", "ex"} else "default"


def _chat_root_for_view(view: Optional[str]) -> str:
    if _normalize_chat_view(view) == "experimental":
        return "/chat-ex/experimental"
    return "/chat-ex/"


def _chat_redirect_url(
    view: Optional[str],
    *,
    server_id: Optional[int] = None,
    channel_id: Optional[int] = None,
    voice_channel_id: Optional[int] = None,
) -> str:
    base = _chat_root_for_view(view)
    params: dict[str, int] = {}
    if server_id is not None:
        params["server_id"] = int(server_id)
    if channel_id is not None:
        params["channel_id"] = int(channel_id)
    if voice_channel_id is not None:
        params["voice_channel_id"] = int(voice_channel_id)
    if not params:
        return base
    return f"{base}?{urlencode(params)}"


async def _get_member(db: AsyncSession, server_id: int, user_id: int) -> Optional[models.ChatServerMember]:
    res = await db.execute(
        select(models.ChatServerMember).where(
            and_(models.ChatServerMember.server_id == server_id, models.ChatServerMember.user_id == user_id)
        )
    )
    return res.scalar_one_or_none()


async def _can_manage_server(db: AsyncSession, server: models.ChatServer, user: models.User) -> bool:
    if user.role == permissions.UserRole.ADMIN or server.owner_user_id == user.id:
        return True
    bits = await _get_member_permissions(db, server.id, user.id)
    return bool(bits & (PERM_ADMIN | PERM_MANAGE_CHANNELS | PERM_MANAGE_ROLES | PERM_MANAGE_EMOJIS))


async def _get_member_permissions(db: AsyncSession, server_id: int, user_id: int) -> int:
    member = await _get_member(db, server_id, user_id)
    if not member:
        return 0

    bits = 0
    default_roles_res = await db.execute(
        select(models.ChatRole).where(
            and_(models.ChatRole.server_id == server_id, models.ChatRole.is_default == True)  # noqa: E712
        )
    )
    for role in default_roles_res.scalars().all():
        bits |= int(role.permissions or 0)

    links_res = await db.execute(
        select(models.ChatMemberRole).where(models.ChatMemberRole.member_id == member.id)
    )
    role_ids = [link.role_id for link in links_res.scalars().all()]
    if role_ids:
        roles_res = await db.execute(select(models.ChatRole).where(models.ChatRole.id.in_(role_ids)))
        for role in roles_res.scalars().all():
            bits |= int(role.permissions or 0)
    return bits


async def _get_member_role_context(
    db: AsyncSession, server_id: int, user_id: int
) -> tuple[Optional[models.ChatServerMember], int, list[int]]:
    member = await _get_member(db, server_id, user_id)
    if not member:
        return None, 0, []

    bits = 0
    role_ids: list[int] = []
    default_roles_res = await db.execute(
        select(models.ChatRole).where(
            and_(models.ChatRole.server_id == server_id, models.ChatRole.is_default == True)  # noqa: E712
        )
    )
    for role in default_roles_res.scalars().all():
        bits |= int(role.permissions or 0)
        role_ids.append(role.id)

    links_res = await db.execute(
        select(models.ChatMemberRole).where(models.ChatMemberRole.member_id == member.id)
    )
    linked_role_ids = [link.role_id for link in links_res.scalars().all()]
    if linked_role_ids:
        roles_res = await db.execute(select(models.ChatRole).where(models.ChatRole.id.in_(linked_role_ids)))
        for role in roles_res.scalars().all():
            bits |= int(role.permissions or 0)
            role_ids.append(role.id)

    dedup_role_ids = sorted(set(role_ids))
    return member, bits, dedup_role_ids


def _apply_overwrites(
    bits: int,
    overwrites: list,
    role_ids: list[int],
    user_id: int,
) -> int:
    role_ows = [ow for ow in overwrites if ow.role_id is not None and ow.role_id in role_ids]
    member_ows = [ow for ow in overwrites if ow.member_user_id == user_id]
    for ow in [*role_ows, *member_ows]:
        bits &= ~int(ow.deny_bits or 0)
        bits |= int(ow.allow_bits or 0)
    return bits


async def _get_effective_channel_permissions(
    db: AsyncSession, channel: models.ChatChannel, user: models.User
) -> int:
    if user.role == permissions.UserRole.ADMIN:
        return ALL_PERMISSIONS
    member, bits, role_ids = await _get_member_role_context(db, channel.server_id, user.id)
    if not member:
        return 0
    if bits & PERM_ADMIN:
        return bits

    if channel.category_id:
        category_ows_res = await db.execute(
            select(models.ChatCategoryPermission).where(
                models.ChatCategoryPermission.category_id == channel.category_id
            )
        )
        bits = _apply_overwrites(bits, category_ows_res.scalars().all(), role_ids, user.id)

    channel_ows_res = await db.execute(
        select(models.ChatChannelPermission).where(models.ChatChannelPermission.channel_id == channel.id)
    )
    bits = _apply_overwrites(bits, channel_ows_res.scalars().all(), role_ids, user.id)
    return bits


async def _ensure_voice_room_for_channel(
    db: AsyncSession, server: models.ChatServer, channel_name: str
) -> str:
    slug_base = _slugify(f"{server.name}-{channel_name}")
    slug = slug_base
    counter = 1
    while True:
        existing = await db.execute(select(models.VoiceRoom).where(models.VoiceRoom.slug == slug))
        if not existing.scalar_one_or_none():
            break
        counter += 1
        slug = f"{slug_base}-{counter}"
    room = models.VoiceRoom(
        slug=slug,
        name=f"{server.name} / {channel_name}",
        description="Auto-created from chat voice channel",
        created_by_username="chat-system",
    )
    db.add(room)
    await db.flush()
    return slug


async def _ensure_server_defaults(db: AsyncSession, server: models.ChatServer, owner: models.User) -> None:
    everyone = await db.execute(
        select(models.ChatRole).where(
            and_(models.ChatRole.server_id == server.id, models.ChatRole.is_default == True)  # noqa: E712
        )
    )
    everyone_role = everyone.scalar_one_or_none()
    if not everyone_role:
        everyone_role = models.ChatRole(
            server_id=server.id,
            name="@everyone",
            color="#64748b",
            position=0,
            permissions=DEFAULT_MEMBER_PERMISSIONS,
            is_default=True,
        )
        db.add(everyone_role)

    owner_role_res = await db.execute(
        select(models.ChatRole).where(
            and_(models.ChatRole.server_id == server.id, models.ChatRole.name == "Owner")
        )
    )
    owner_role = owner_role_res.scalar_one_or_none()
    if not owner_role:
        owner_role = models.ChatRole(
            server_id=server.id,
            name="Owner",
            color="#f59e0b",
            position=100,
            permissions=ALL_PERMISSIONS,
            is_default=False,
        )
        db.add(owner_role)
        await db.flush()

    member = await _get_member(db, server.id, owner.id)
    if not member:
        member = models.ChatServerMember(server_id=server.id, user_id=owner.id)
        db.add(member)
        await db.flush()

    owner_link_res = await db.execute(
        select(models.ChatMemberRole).where(
            and_(models.ChatMemberRole.member_id == member.id, models.ChatMemberRole.role_id == owner_role.id)
        )
    )
    if not owner_link_res.scalar_one_or_none():
        db.add(models.ChatMemberRole(member_id=member.id, role_id=owner_role.id))

    default_group_res = await db.execute(
        select(models.ChatCategory).where(
            and_(models.ChatCategory.server_id == server.id, models.ChatCategory.name == "General")
        )
    )
    default_group = default_group_res.scalar_one_or_none()
    if not default_group:
        default_group = models.ChatCategory(server_id=server.id, name="General", position=0)
        db.add(default_group)
        await db.flush()

    general_res = await db.execute(
        select(models.ChatChannel).where(
            and_(models.ChatChannel.server_id == server.id, models.ChatChannel.name == "general")
        )
    )
    if not general_res.scalar_one_or_none():
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

    lobby_res = await db.execute(
        select(models.ChatChannel).where(
            and_(models.ChatChannel.server_id == server.id, models.ChatChannel.name == "Lobby")
        )
    )
    if not lobby_res.scalar_one_or_none():
        voice_slug = await _ensure_voice_room_for_channel(db, server, "Lobby")
        db.add(
            models.ChatChannel(
                server_id=server.id,
                category_id=default_group.id,
                name="Lobby",
                slug="lobby",
                channel_type="voice",
                linked_voice_room_slug=voice_slug,
                topic="Default voice channel",
                position=1,
            )
        )


@router.get("/", response_class=HTMLResponse)
async def chat_home(
    request: Request,
    server_id: Optional[int] = None,
    channel_id: Optional[int] = None,
    voice_channel_id: Optional[int] = None,
    current_user: models.User = Depends(auth.get_current_user),
    db: AsyncSession = Depends(database.get_db),
):
    chat_view = "default"
    chat_base_path = _chat_root_for_view(chat_view)
    template_name = "chat_ex.html"

    active_modules = await server_utils.get_active_modules(db)

    servers_res = await db.execute(select(models.ChatServer).order_by(models.ChatServer.created_at.asc()))
    my_servers = servers_res.scalars().all()

    selected_server = None
    channels = []
    channel_groups = []
    selected_channel = None
    selected_voice_channel = None
    messages = []
    message_reaction_counts: dict[int, dict[str, int]] = {}
    message_user_reactions: dict[int, set[str]] = {}
    members = []
    roles = []
    emojis = []
    member_role_labels = {}
    member_unread_counts = {}
    can_manage = False
    can_send_selected_channel = False

    if my_servers:
        if server_id:
            selected_server = next((s for s in my_servers if s.id == server_id), None)
        if not selected_server:
            selected_server = my_servers[0]

    if selected_server:
        # Keep chat interactive for regular users by auto-enrolling on first visit.
        existing_member = await _get_member(db, selected_server.id, current_user.id)
        if not existing_member:
            db.add(models.ChatServerMember(server_id=selected_server.id, user_id=current_user.id))
            await db.commit()

        can_manage = await _can_manage_server(db, selected_server, current_user)
        channels_res = await db.execute(
            select(models.ChatChannel)
            .where(models.ChatChannel.server_id == selected_server.id)
            .order_by(models.ChatChannel.position.asc(), models.ChatChannel.id.asc())
        )
        all_channels = channels_res.scalars().all()

        categories_res = await db.execute(
            select(models.ChatCategory)
            .where(models.ChatCategory.server_id == selected_server.id)
            .order_by(models.ChatCategory.position.asc(), models.ChatCategory.id.asc())
        )
        categories = categories_res.scalars().all()

        effective_bits_by_channel: dict[int, int] = {}
        visible_channels: list[models.ChatChannel] = []
        for ch in all_channels:
            bits = await _get_effective_channel_permissions(db, ch, current_user)
            effective_bits_by_channel[ch.id] = bits
            if bits & (PERM_VIEW_CHANNEL | PERM_ADMIN):
                visible_channels.append(ch)
        channels = visible_channels

        grouped: dict[Optional[int], list[models.ChatChannel]] = {}
        for ch in channels:
            grouped.setdefault(ch.category_id, []).append(ch)
        for category in categories:
            grouped_channels = grouped.get(category.id, [])
            if grouped_channels:
                channel_groups.append({"category": category, "channels": grouped_channels})
        if grouped.get(None):
            channel_groups.append({"category": None, "channels": grouped[None]})

        text_channels = [c for c in channels if c.channel_type == "text"]
        voice_channels = [c for c in channels if c.channel_type == "voice"]
        if voice_channel_id:
            selected_voice_channel = next((c for c in voice_channels if c.id == voice_channel_id), None)
        if text_channels:
            if channel_id:
                selected_channel = next((c for c in text_channels if c.id == channel_id), None)
            if not selected_channel:
                selected_channel = text_channels[0]

        if selected_channel:
            selected_bits = effective_bits_by_channel.get(selected_channel.id, 0)
            if not (selected_bits & (PERM_VIEW_CHANNEL | PERM_ADMIN)):
                selected_channel = None
            else:
                can_send_selected_channel = bool(selected_bits & (PERM_SEND_MESSAGES | PERM_ADMIN))
        if selected_voice_channel:
            voice_bits = effective_bits_by_channel.get(selected_voice_channel.id, 0)
            if not (voice_bits & (PERM_CONNECT_VOICE | PERM_ADMIN)):
                selected_voice_channel = None

        if selected_channel:
            messages_res = await db.execute(
                select(models.ChatMessage)
                .where(models.ChatMessage.channel_id == selected_channel.id)
                .options(
                    selectinload(models.ChatMessage.author),
                    selectinload(models.ChatMessage.reactions),
                )
                .order_by(models.ChatMessage.created_at.asc())
            )
            messages = messages_res.scalars().all()
            reaction_counts: dict[int, dict[str, int]] = defaultdict(dict)
            user_reactions: dict[int, set[str]] = defaultdict(set)
            for msg in messages:
                emoji_counts: dict[str, int] = defaultdict(int)
                for reaction in msg.reactions or []:
                    code = (reaction.emoji_code or "").strip()
                    if not code:
                        continue
                    emoji_counts[code] += 1
                    if reaction.user_id == current_user.id:
                        user_reactions[msg.id].add(code)
                reaction_counts[msg.id] = dict(emoji_counts)
            message_reaction_counts = dict(reaction_counts)
            message_user_reactions = dict(user_reactions)

        members_res = await db.execute(
            select(models.ChatServerMember)
            .where(models.ChatServerMember.server_id == selected_server.id)
            .options(selectinload(models.ChatServerMember.user))
            .order_by(models.ChatServerMember.joined_at.asc())
        )
        members = members_res.scalars().all()
        roles_res = await db.execute(
            select(models.ChatRole)
            .where(models.ChatRole.server_id == selected_server.id)
            .order_by(models.ChatRole.position.desc(), models.ChatRole.id.asc())
        )
        roles = roles_res.scalars().all()
        role_map = {role.id: role for role in roles}
        member_role_ids: dict[int, list[int]] = {}
        if members:
            role_links_res = await db.execute(
                select(models.ChatMemberRole).where(
                    models.ChatMemberRole.member_id.in_([member.id for member in members])
                )
            )
            for link in role_links_res.scalars().all():
                member_role_ids.setdefault(link.member_id, []).append(link.role_id)
        for member in members:
            labels: list[str] = []
            for role_id in member_role_ids.get(member.id, []):
                role = role_map.get(role_id)
                if role and role.name:
                    labels.append(role.name)
            if labels:
                member_role_labels[member.id] = labels[0]
            else:
                member_role_labels[member.id] = "member"

        # Unread DM counts from each member -> current user.
        member_user_ids = [member.user_id for member in members if member.user_id != current_user.id]
        if member_user_ids:
            unread_res = await db.execute(
                select(models.MailMessage.sender_user_id, func.count(models.MailMessage.id))
                .where(
                    models.MailMessage.recipient_user_id == current_user.id,
                    models.MailMessage.is_read == False,  # noqa: E712
                    models.MailMessage.recipient_deleted == False,  # noqa: E712
                    models.MailMessage.subject.like("DM from @%"),
                    models.MailMessage.sender_user_id.in_(member_user_ids),
                )
                .group_by(models.MailMessage.sender_user_id)
            )
            for sender_user_id, unread_count in unread_res.all():
                member_unread_counts[sender_user_id] = int(unread_count or 0)

        emojis_res = await db.execute(
            select(models.ChatEmoji)
            .where(models.ChatEmoji.server_id == selected_server.id)
            .order_by(models.ChatEmoji.created_at.desc())
        )
        emojis = emojis_res.scalars().all()

    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context={
            "user": current_user,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
            "active_modules": active_modules,
            "servers": my_servers,
            "selected_server": selected_server,
            "channels": channels,
            "channel_groups": channel_groups,
            "selected_channel": selected_channel,
            "selected_voice_channel": selected_voice_channel,
            "messages": messages,
            "message_reaction_counts": message_reaction_counts,
            "message_user_reactions": message_user_reactions,
            "members": members,
            "roles": roles,
            "emojis": emojis,
            "member_role_labels": member_role_labels,
            "member_unread_counts": member_unread_counts,
            "can_manage_server": can_manage,
            "can_send_selected_channel": can_send_selected_channel,
            "chat_view": chat_view,
            "chat_base_path": chat_base_path,
        },
    )


@router.get("/experimental", response_class=HTMLResponse)
async def chat_experimental_legacy_redirect(request: Request):
    """
    Legacy compatibility route.
    The isolated experimental module now lives under /chat-ex.
    """
    target = "/chat-ex/"
    if request.url.query:
        target = f"{target}?{request.url.query}"
    return RedirectResponse(url=target, status_code=307)


@router.get("/api/servers")
async def chat_server_switch_list(
    current_user: models.User = Depends(auth.get_current_user),
    db: AsyncSession = Depends(database.get_db),
):
    if current_user.role == permissions.UserRole.ADMIN:
        servers_res = await db.execute(select(models.ChatServer).order_by(models.ChatServer.created_at.asc()))
        servers = servers_res.scalars().all()
    else:
        member_res = await db.execute(
            select(models.ChatServerMember.server_id).where(models.ChatServerMember.user_id == current_user.id)
        )
        server_ids = {row[0] for row in member_res.all()}
        owner_res = await db.execute(select(models.ChatServer.id).where(models.ChatServer.owner_user_id == current_user.id))
        for row in owner_res.all():
            server_ids.add(row[0])
        if not server_ids:
            servers = []
        else:
            servers_res = await db.execute(
                select(models.ChatServer)
                .where(models.ChatServer.id.in_(server_ids))
                .order_by(models.ChatServer.created_at.asc())
            )
            servers = servers_res.scalars().all()

    return {
        "servers": [
            {"id": server.id, "name": server.name}
            for server in servers
        ]
    }


@router.post("/servers/create")
async def create_server(
    name: str = Form(...),
    description: str = Form(""),
    view: Optional[str] = None,
    current_user: models.User = Depends(auth.get_current_user),
    db: AsyncSession = Depends(database.get_db),
):
    permissions.require_admin(current_user)
    server = models.ChatServer(
        name=name.strip(),
        description=description.strip() or None,
        owner_user_id=current_user.id,
    )
    db.add(server)
    await db.flush()
    await _ensure_server_defaults(db, server, current_user)
    await db.commit()
    return RedirectResponse(
        url=_chat_redirect_url(view, server_id=server.id),
        status_code=303,
    )


@router.post("/servers/{server_id}/channels/create")
async def create_channel(
    server_id: int,
    name: str = Form(...),
    channel_type: str = Form("text"),
    category_id: Optional[int] = Form(None),
    category_name: str = Form(""),
    topic: str = Form(""),
    view: Optional[str] = None,
    current_user: models.User = Depends(auth.get_current_user),
    db: AsyncSession = Depends(database.get_db),
):
    permissions.require_admin(current_user)
    server_res = await db.execute(select(models.ChatServer).where(models.ChatServer.id == server_id))
    server = server_res.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="Chat server not found")

    normalized_type = channel_type.strip().lower()
    if normalized_type not in {"text", "voice"}:
        normalized_type = "text"

    resolved_category_id = None
    if category_id:
        cat_res = await db.execute(
            select(models.ChatCategory).where(
                and_(models.ChatCategory.server_id == server.id, models.ChatCategory.id == category_id)
            )
        )
        category = cat_res.scalar_one_or_none()
        if category:
            resolved_category_id = category.id
    if resolved_category_id is None and category_name.strip():
        category_res = await db.execute(
            select(models.ChatCategory).where(
                and_(models.ChatCategory.server_id == server.id, models.ChatCategory.name == category_name.strip())
            )
        )
        category = category_res.scalar_one_or_none()
        if not category:
            category = models.ChatCategory(server_id=server.id, name=category_name.strip(), position=0)
            db.add(category)
            await db.flush()
        resolved_category_id = category.id

    if resolved_category_id is None:
        raise HTTPException(status_code=400, detail="A channel group is required")

    channels_res = await db.execute(select(models.ChatChannel).where(models.ChatChannel.server_id == server.id))
    next_position = len(channels_res.scalars().all())
    slug_base = _slugify(name)
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
    if normalized_type == "voice":
        linked_voice_room_slug = await _ensure_voice_room_for_channel(db, server, name)

    channel = models.ChatChannel(
        server_id=server.id,
        category_id=resolved_category_id,
        name=name.strip(),
        slug=slug,
        channel_type=normalized_type,
        topic=topic.strip() or None,
        position=next_position,
        linked_voice_room_slug=linked_voice_room_slug,
    )
    db.add(channel)
    await db.commit()
    if normalized_type == "text":
        return RedirectResponse(
            url=_chat_redirect_url(view, server_id=server.id, channel_id=channel.id),
            status_code=303,
        )
    return RedirectResponse(
        url=_chat_redirect_url(view, server_id=server.id),
        status_code=303,
    )


@router.post("/channels/{channel_id}/topic/update")
async def update_channel_topic(
    channel_id: int,
    topic: str = Form(""),
    voice_channel_id: Optional[int] = Form(None),
    view: Optional[str] = None,
    current_user: models.User = Depends(auth.get_current_user),
    db: AsyncSession = Depends(database.get_db),
):
    permissions.require_admin(current_user)

    channel_res = await db.execute(select(models.ChatChannel).where(models.ChatChannel.id == channel_id))
    channel = channel_res.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    channel.topic = topic.strip() or None
    await db.commit()

    redirect_url = _chat_redirect_url(
        view,
        server_id=channel.server_id,
        channel_id=channel.id,
        voice_channel_id=voice_channel_id,
    )
    return RedirectResponse(url=redirect_url, status_code=303)


@router.post("/channels/{channel_id}/messages/send")
async def send_message(
    channel_id: int,
    content: str = Form(...),
    image: Optional[UploadFile] = File(None),
    view: Optional[str] = None,
    current_user: models.User = Depends(auth.get_current_user),
    db: AsyncSession = Depends(database.get_db),
):
    channel_res = await db.execute(select(models.ChatChannel).where(models.ChatChannel.id == channel_id))
    channel = channel_res.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    if channel.channel_type != "text":
        raise HTTPException(status_code=400, detail="Cannot send text messages to a voice channel")
    member = await _get_member(db, channel.server_id, current_user.id)
    if not member and current_user.role != permissions.UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="You are not a member of this server")
    bits = await _get_effective_channel_permissions(db, channel, current_user)
    if current_user.role != permissions.UserRole.ADMIN and not (bits & (PERM_SEND_MESSAGES | PERM_ADMIN)):
        raise HTTPException(status_code=403, detail="Send message permission required")

    body = content.strip()
    media_path = None
    media_type = None
    if image and image.filename:
        upload_data = await media_utils.save_upload_file(image)
        media_path = upload_data["filename"]
        media_type = upload_data["type"]

    if not body and not media_path:
        return RedirectResponse(
            url=_chat_redirect_url(view, server_id=channel.server_id, channel_id=channel.id),
            status_code=303,
        )

    msg = models.ChatMessage(
        server_id=channel.server_id,
        channel_id=channel.id,
        author_user_id=current_user.id,
        content=body,
        media_path=media_path,
        media_type=media_type,
    )
    db.add(msg)
    await db.commit()
    return RedirectResponse(
        url=_chat_redirect_url(view, server_id=channel.server_id, channel_id=channel.id),
        status_code=303,
    )


@router.post("/messages/{message_id}/reactions/toggle")
async def toggle_message_reaction(
    message_id: int,
    emoji_code: str = Form(...),
    view: Optional[str] = None,
    current_user: models.User = Depends(auth.get_current_user),
    db: AsyncSession = Depends(database.get_db),
):
    msg_res = await db.execute(select(models.ChatMessage).where(models.ChatMessage.id == message_id))
    msg = msg_res.scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    channel_res = await db.execute(select(models.ChatChannel).where(models.ChatChannel.id == msg.channel_id))
    channel = channel_res.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    member = await _get_member(db, channel.server_id, current_user.id)
    if not member and current_user.role != permissions.UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="You are not a member of this server")
    bits = await _get_effective_channel_permissions(db, channel, current_user)
    if current_user.role != permissions.UserRole.ADMIN and not (bits & (PERM_VIEW_CHANNEL | PERM_ADMIN)):
        raise HTTPException(status_code=403, detail="View permission required")

    code = (emoji_code or "").strip()
    if not code:
        return RedirectResponse(
            url=_chat_redirect_url(view, server_id=msg.server_id, channel_id=msg.channel_id),
            status_code=303,
        )

    existing_res = await db.execute(
        select(models.ChatReaction).where(
            and_(
                models.ChatReaction.message_id == msg.id,
                models.ChatReaction.user_id == current_user.id,
                models.ChatReaction.emoji_code == code,
            )
        )
    )
    existing = existing_res.scalar_one_or_none()
    if existing:
        await db.delete(existing)
    else:
        db.add(models.ChatReaction(message_id=msg.id, user_id=current_user.id, emoji_code=code))
    await db.commit()
    return RedirectResponse(
        url=_chat_redirect_url(view, server_id=msg.server_id, channel_id=msg.channel_id),
        status_code=303,
    )


@router.post("/messages/{message_id}/delete")
async def delete_message(
    message_id: int,
    view: Optional[str] = None,
    current_user: models.User = Depends(auth.get_current_user),
    db: AsyncSession = Depends(database.get_db),
):
    msg_res = await db.execute(select(models.ChatMessage).where(models.ChatMessage.id == message_id))
    msg = msg_res.scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    if msg.author_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the message author can delete this message")

    server_id = msg.server_id
    channel_id = msg.channel_id
    await db.delete(msg)
    await db.commit()
    return RedirectResponse(
        url=_chat_redirect_url(view, server_id=server_id, channel_id=channel_id),
        status_code=303,
    )


@router.post("/servers/{server_id}/roles/create")
async def create_role(
    server_id: int,
    name: str = Form(...),
    color: str = Form("#64748b"),
    view: Optional[str] = None,
    current_user: models.User = Depends(auth.get_current_user),
    db: AsyncSession = Depends(database.get_db),
):
    server_res = await db.execute(select(models.ChatServer).where(models.ChatServer.id == server_id))
    server = server_res.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="Chat server not found")
    if not await _can_manage_server(db, server, current_user):
        raise HTTPException(status_code=403, detail="Manage role permission required")

    role_count_res = await db.execute(select(models.ChatRole).where(models.ChatRole.server_id == server.id))
    position = len(role_count_res.scalars().all()) + 1
    role = models.ChatRole(
        server_id=server.id,
        name=name.strip(),
        color=color.strip() or "#64748b",
        position=position,
        permissions=DEFAULT_MEMBER_PERMISSIONS,
        is_default=False,
    )
    db.add(role)
    await db.commit()
    return RedirectResponse(
        url=_chat_redirect_url(view, server_id=server.id),
        status_code=303,
    )


@router.post("/servers/{server_id}/emojis/create")
async def create_emoji(
    server_id: int,
    short_code: str = Form(...),
    unicode_char: str = Form(""),
    view: Optional[str] = None,
    current_user: models.User = Depends(auth.get_current_user),
    db: AsyncSession = Depends(database.get_db),
):
    server_res = await db.execute(select(models.ChatServer).where(models.ChatServer.id == server_id))
    server = server_res.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="Chat server not found")
    if not await _can_manage_server(db, server, current_user):
        raise HTTPException(status_code=403, detail="Manage emoji permission required")

    emoji = models.ChatEmoji(
        server_id=server.id,
        short_code=short_code.strip().lower(),
        unicode_char=unicode_char.strip() or None,
        created_by_user_id=current_user.id,
    )
    db.add(emoji)
    await db.commit()
    return RedirectResponse(
        url=_chat_redirect_url(view, server_id=server.id),
        status_code=303,
    )


@router.get("/dm/{username}", response_class=HTMLResponse)
async def direct_message_thread(
    username: str,
    request: Request,
    current_user: models.User = Depends(auth.get_current_user),
    db: AsyncSession = Depends(database.get_db),
):
    target_res = await db.execute(select(models.User).where(models.User.username == username))
    target_user = target_res.scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    if target_user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot DM yourself")

    active_modules = await server_utils.get_active_modules(db)
    convo_res = await db.execute(
        select(models.MailMessage)
        .where(
            or_(
                and_(
                    models.MailMessage.sender_user_id == current_user.id,
                    models.MailMessage.recipient_user_id == target_user.id,
                    models.MailMessage.sender_deleted == False,  # noqa: E712
                    models.MailMessage.subject.like("DM from @%"),
                ),
                and_(
                    models.MailMessage.sender_user_id == target_user.id,
                    models.MailMessage.recipient_user_id == current_user.id,
                    models.MailMessage.recipient_deleted == False,  # noqa: E712
                    models.MailMessage.subject.like("DM from @%"),
                ),
            )
        )
        .order_by(models.MailMessage.created_at.asc())
    )
    messages = convo_res.scalars().all()

    # Mark incoming messages as read when viewing thread.
    changed = False
    for msg in messages:
        if msg.recipient_user_id == current_user.id and not msg.is_read:
            msg.is_read = True
            changed = True
    if changed:
        await db.commit()

    return templates.TemplateResponse(
        request=request,
        name="chat_ex_dm.html",
        context={
            "user": current_user,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
            "active_modules": active_modules,
            "target_user": target_user,
            "messages": messages,
        },
    )


@router.post("/dm/{username}/send")
async def send_direct_message(
    username: str,
    content: str = Form(...),
    current_user: models.User = Depends(auth.get_current_user),
    db: AsyncSession = Depends(database.get_db),
):
    target_res = await db.execute(select(models.User).where(models.User.username == username))
    target_user = target_res.scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    if target_user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot DM yourself")

    body = content.strip()
    if body:
        dm = models.MailMessage(
            sender_user_id=current_user.id,
            recipient_user_id=target_user.id,
            recipient_address=target_user.username,
            subject=f"DM from @{current_user.username}",
            body=body,
            delivery_status="delivered",
        )
        db.add(dm)
        await db.commit()
    return RedirectResponse(url=f"/chat-ex/dm/{target_user.username}", status_code=303)


