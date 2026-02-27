from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, or_, select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from .. import database, models, server_utils, server_identity
from ..config import settings
from . import auth

router = APIRouter(prefix="/mail", tags=["mail"])
templates = Jinja2Templates(directory=str(settings.BASE_DIR / "templates"))


def _normalize_host_token(value: str | None) -> str:
    token = (value or "").strip().lower()
    if not token:
        return ""
    if "://" in token:
        try:
            parsed = urlparse(token)
            token = (parsed.hostname or token).strip().lower()
        except Exception:
            pass
    token = token.strip("[]").rstrip(".")
    return token


def _host_aliases(value: str | None) -> set[str]:
    token = _normalize_host_token(value)
    if not token:
        return set()
    aliases = {token}
    # Split host:port for non-IPv6 style values.
    if ":" in token and token.count(":") == 1:
        aliases.add(token.split(":", 1)[0])
    return aliases


def _parse_recipient_input(raw: str) -> tuple[str, str | None, str]:
    cleaned = (raw or "").strip()
    while cleaned.startswith("@"):
        cleaned = cleaned[1:].strip()
    if not cleaned:
        return "", None, ""

    if "@" in cleaned:
        local_part, host_hint = cleaned.split("@", 1)
        local_part = local_part.strip()
        host_hint = _normalize_host_token(host_hint)
        return local_part, (host_hint or None), cleaned

    return cleaned, None, cleaned


async def _resolve_local_recipient(
    db: AsyncSession,
    request: Request,
    raw_recipient: str,
) -> tuple[models.User | None, str]:
    local_part, host_hint, cleaned_address = _parse_recipient_input(raw_recipient)
    if not local_part:
        return None, cleaned_address

    user_res = await db.execute(
        select(models.User).where(func.lower(models.User.username) == local_part.lower())
    )
    local_user = user_res.scalars().first()
    if not local_user:
        return None, cleaned_address

    # If no host hint was provided, username-only recipient maps local directly.
    if not host_hint:
        return local_user, local_user.username

    local_tokens: set[str] = {"localhost", "127.0.0.1", "::1"}
    local_tokens.update(_host_aliases(server_identity.get_or_create_server_id()))
    local_tokens.update(_host_aliases(settings.DOMAIN))
    local_tokens.update(_host_aliases(getattr(request.url, "hostname", "")))
    local_tokens.update(_host_aliases(getattr(request.client, "host", "")))

    try:
        server_settings = await server_utils.get_server_settings(db)
    except Exception:
        server_settings = None

    if server_settings:
        local_tokens.update(_host_aliases(getattr(server_settings, "secure_local_domain", None)))
        local_tokens.update(_host_aliases(getattr(server_settings, "secure_local_ip", None)))
        local_tokens.update(_host_aliases(getattr(server_settings, "external_server_url", None)))

    if host_hint in local_tokens:
        return local_user, local_user.username

    # Host hint points elsewhere (remote target), so keep as remote address.
    return None, cleaned_address


@router.get("/", response_class=HTMLResponse)
async def mail_inbox(
    request: Request,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    inbox_res = await db.execute(
        select(models.MailMessage)
        .options(selectinload(models.MailMessage.sender))
        .where(
            and_(
                models.MailMessage.recipient_user_id == current_user.id,
                models.MailMessage.recipient_deleted == False,  # noqa: E712
            )
        )
        .order_by(models.MailMessage.created_at.desc())
    )
    inbox_messages = inbox_res.scalars().all()

    sent_res = await db.execute(
        select(models.MailMessage)
        .options(selectinload(models.MailMessage.recipient))
        .where(
            and_(
                models.MailMessage.sender_user_id == current_user.id,
                models.MailMessage.sender_deleted == False,  # noqa: E712
            )
        )
        .order_by(models.MailMessage.created_at.desc())
    )
    sent_messages = sent_res.scalars().all()

    active_modules = await server_utils.get_active_modules(db)
    return templates.TemplateResponse(
        request=request,
        name="mail_inbox.html",
        context={
            "user": current_user,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
            "active_modules": active_modules,
            "inbox_messages": inbox_messages,
            "sent_messages": sent_messages,
        },
    )


@router.get("/compose", response_class=HTMLResponse)
async def mail_compose_page(
    request: Request,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    users_res = await db.execute(select(models.User).where(models.User.id != current_user.id))
    users = users_res.scalars().all()
    active_modules = await server_utils.get_active_modules(db)
    return templates.TemplateResponse(
        request=request,
        name="mail_compose.html",
        context={
            "user": current_user,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
            "active_modules": active_modules,
            "users": users,
        },
    )


@router.post("/compose")
async def mail_compose_submit(
    request: Request,
    recipient: Annotated[str, Form()],
    subject: Annotated[str, Form()],
    body: Annotated[str, Form()],
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    recipient_value = (recipient or "").strip()
    if not recipient_value:
        raise HTTPException(status_code=400, detail="Recipient is required")
    if not subject.strip():
        raise HTTPException(status_code=400, detail="Subject is required")
    if not body.strip():
        raise HTTPException(status_code=400, detail="Body is required")

    local_user, normalized_recipient_address = await _resolve_local_recipient(db, request, recipient_value)
    if not normalized_recipient_address:
        raise HTTPException(status_code=400, detail="Recipient is invalid")

    msg = models.MailMessage(
        sender_user_id=current_user.id,
        recipient_user_id=local_user.id if local_user else None,
        recipient_address=normalized_recipient_address,
        subject=subject.strip(),
        body=body.strip(),
        delivery_status="delivered" if local_user else "queued_remote",
    )
    db.add(msg)
    await db.commit()
    return RedirectResponse(url="/mail", status_code=303)


@router.get("/unread-count")
async def mail_unread_count(
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    unread_res = await db.execute(
        select(func.count(models.MailMessage.id)).where(
            and_(
                models.MailMessage.recipient_user_id == current_user.id,
                models.MailMessage.recipient_deleted == False,  # noqa: E712
                models.MailMessage.is_read == False,  # noqa: E712
            )
        )
    )
    unread_count = int(unread_res.scalar() or 0)
    return {"unread_count": int(unread_count)}


@router.get("/message/{message_id}", response_class=HTMLResponse)
async def mail_message_view(
    message_id: int,
    request: Request,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    res = await db.execute(select(models.MailMessage).where(models.MailMessage.id == message_id))
    message = res.scalars().first()
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    is_sender = message.sender_user_id == current_user.id
    is_recipient = message.recipient_user_id == current_user.id
    if not is_sender and not is_recipient:
        raise HTTPException(status_code=403, detail="Not allowed")
    if is_recipient and not message.is_read:
        message.is_read = True
        await db.commit()

    active_modules = await server_utils.get_active_modules(db)
    return templates.TemplateResponse(
        request=request,
        name="mail_message.html",
        context={
            "user": current_user,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
            "active_modules": active_modules,
            "message": message,
            "is_sender": is_sender,
        },
    )


@router.post("/message/{message_id}/delete")
async def mail_message_delete(
    message_id: int,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    res = await db.execute(select(models.MailMessage).where(models.MailMessage.id == message_id))
    message = res.scalars().first()
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    if message.sender_user_id == current_user.id:
        message.sender_deleted = True
    elif message.recipient_user_id == current_user.id:
        message.recipient_deleted = True
    else:
        raise HTTPException(status_code=403, detail="Not allowed")

    await db.commit()
    return RedirectResponse(url="/mail", status_code=303)
