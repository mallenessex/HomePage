"""
Create module — GeoCities-style website builder.

Users build personal web pages via a drag-and-drop visual editor.
Pages are published at /users/{username}/created/{slug}.
"""
from __future__ import annotations

import html as _html
import json
import re
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from .. import database, models, server_utils, auth_utils
from ..config import settings
from . import auth

router = APIRouter(prefix="/create", tags=["create"])

templates = Jinja2Templates(directory=str(settings.BASE_DIR / "templates"))

# ── Sanitisation helpers ────────────────────────────────────────────

_STRIP_TAGS_RE = re.compile(
    r"<\s*/?\s*(script|iframe|object|embed|applet|form|meta|link|base)\b[^>]*>",
    re.I,
)
_EVENT_ATTR_RE = re.compile(r"\s+on\w+\s*=\s*[\"'][^\"']*[\"']", re.I)
_JS_URI_RE = re.compile(r"javascript\s*:", re.I)
_CSS_DANGEROUS_RE = re.compile(
    r"(expression\s*\(|behavior\s*:|@import|url\s*\(\s*['\"]?javascript:)", re.I
)


def sanitise_html(raw: str) -> str:
    """Strip dangerous tags, event handlers, and JS URIs from HTML."""
    out = _STRIP_TAGS_RE.sub("", raw)
    out = _EVENT_ATTR_RE.sub("", out)
    out = _JS_URI_RE.sub("", out)
    return out


def sanitise_css(raw: str) -> str:
    """Strip dangerous CSS constructs."""
    return _CSS_DANGEROUS_RE.sub("/* removed */", raw)


def slugify(name: str) -> str:
    """Turn a page title into a URL-safe slug."""
    s = name.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:80] or "untitled"


# ── Dashboard ───────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: AsyncSession = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    res = await db.execute(
        select(models.CreatedPage)
        .where(models.CreatedPage.owner_id == current_user.id)
        .order_by(models.CreatedPage.updated_at.desc())
    )
    pages = res.scalars().all()
    return templates.TemplateResponse(
        request=request,
        name="create_dashboard.html",
        context={
            "pages": pages,
            "user": current_user,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
        },
    )


# ── Editor ──────────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
async def new_page(
    request: Request,
    current_user: models.User = Depends(auth.get_current_user),
):
    return templates.TemplateResponse(
        request=request,
        name="create_editor.html",
        context={
            "page": None,
            "user": current_user,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
        },
    )


@router.get("/edit/{page_id}", response_class=HTMLResponse)
async def edit_page(
    page_id: int,
    request: Request,
    db: AsyncSession = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    page = await _get_owned_page(db, page_id, current_user)
    return templates.TemplateResponse(
        request=request,
        name="create_editor.html",
        context={
            "page": page,
            "user": current_user,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
        },
    )


# ── Save ────────────────────────────────────────────────────────────

@router.post("/save")
async def save_page(
    request: Request,
    db: AsyncSession = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    body = await request.json()
    page_id = body.get("page_id")
    title = (body.get("title") or "Untitled Page").strip()[:200]
    slug = slugify(body.get("slug") or title)
    html_content = sanitise_html(body.get("html_content", ""))
    css_content = sanitise_css(body.get("css_content", ""))
    page_json = json.dumps(body.get("page_json", {}))
    is_published = bool(body.get("is_published", False))

    if page_id:
        page = await _get_owned_page(db, int(page_id), current_user)
        page.title = title
        page.slug = slug
        page.html_content = html_content
        page.css_content = css_content
        page.page_json = page_json
        page.is_published = is_published
    else:
        # Check slug uniqueness for this user
        existing = await db.execute(
            select(models.CreatedPage).where(
                and_(
                    models.CreatedPage.owner_id == current_user.id,
                    models.CreatedPage.slug == slug,
                )
            )
        )
        if existing.scalar_one_or_none():
            slug = slug + "-" + str(int(__import__("time").time()) % 100000)
        page = models.CreatedPage(
            owner_id=current_user.id,
            slug=slug,
            title=title,
            html_content=html_content,
            css_content=css_content,
            page_json=page_json,
            is_published=is_published,
        )
        db.add(page)

    await db.commit()
    await db.refresh(page)
    return JSONResponse({"ok": True, "page_id": page.id, "slug": page.slug})


# ── Delete ──────────────────────────────────────────────────────────

@router.post("/delete/{page_id}")
async def delete_page(
    page_id: int,
    db: AsyncSession = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    page = await _get_owned_page(db, page_id, current_user)
    await db.delete(page)
    await db.commit()
    return RedirectResponse(url="/create", status_code=303)


# ── Public viewer routes ────────────────────────────────────────────
# These are at /create/site/{username}/ and /create/site/{username}/{slug}
# so they live under the /create prefix and respect the module guard.
# The template includes a friendly canonical link to /users/{username}/created/.

@router.get("/site/{username}", response_class=HTMLResponse)
async def public_page_list(
    username: str,
    request: Request,
    db: AsyncSession = Depends(database.get_db),
    current_user: Optional[models.User] = Depends(auth.get_current_user_optional),
):
    from .. import crud_users

    owner = await crud_users.get_user_by_username(db, username)
    if not owner:
        raise HTTPException(404, "User not found")
    res = await db.execute(
        select(models.CreatedPage)
        .where(
            and_(
                models.CreatedPage.owner_id == owner.id,
                models.CreatedPage.is_published == True,
            )
        )
        .order_by(models.CreatedPage.updated_at.desc())
    )
    pages = res.scalars().all()
    return templates.TemplateResponse(
        request=request,
        name="create_pages_list.html",
        context={
            "profile_user": owner,
            "pages": pages,
            "user": current_user,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
        },
    )


@router.get("/site/{username}/{slug}", response_class=HTMLResponse)
async def public_page_view(
    username: str,
    slug: str,
    request: Request,
    db: AsyncSession = Depends(database.get_db),
    current_user: Optional[models.User] = Depends(auth.get_current_user_optional),
):
    from .. import crud_users

    owner = await crud_users.get_user_by_username(db, username)
    if not owner:
        raise HTTPException(404, "User not found")
    res = await db.execute(
        select(models.CreatedPage).where(
            and_(
                models.CreatedPage.owner_id == owner.id,
                models.CreatedPage.slug == slug,
                models.CreatedPage.is_published == True,
            )
        )
    )
    page = res.scalar_one_or_none()
    if not page:
        raise HTTPException(404, "Page not found")
    return templates.TemplateResponse(
        request=request,
        name="create_viewer.html",
        context={
            "profile_user": owner,
            "page": page,
            "user": current_user,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
        },
    )


# ── Helpers ─────────────────────────────────────────────────────────

async def _get_owned_page(
    db: AsyncSession, page_id: int, current_user: models.User
) -> models.CreatedPage:
    res = await db.execute(
        select(models.CreatedPage).where(models.CreatedPage.id == page_id)
    )
    page = res.scalar_one_or_none()
    if not page:
        raise HTTPException(404, "Page not found")
    if page.owner_id != current_user.id and not current_user.is_admin:
        raise HTTPException(403, "Not your page")
    return page
