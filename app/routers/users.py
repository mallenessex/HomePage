from fastapi import APIRouter, Depends, HTTPException, Request, Form, File, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import and_
from typing import Annotated, Optional
from datetime import date

from .. import schemas, crud_users, database, models, media_utils, server_utils, auth_utils
from . import auth
from ..config import settings

router = APIRouter(
    prefix="/users",
    tags=["users"]
)

templates = Jinja2Templates(directory=str(settings.BASE_DIR / "templates"))

@router.get("/me")
async def view_my_profile(
    current_user: Annotated[models.User, Depends(auth.get_current_user)]
):
    """
    Redirects to the current user's public profile.
    """
    return RedirectResponse(url=f"/users/{current_user.username}", status_code=303)

@router.get("/{username}", response_class=HTMLResponse)
async def view_profile(
    username: str,
    request: Request,
    db: AsyncSession = Depends(database.get_db),
    current_user: Optional[models.User] = Depends(auth.get_current_user_optional)
):
    """
    Shows a user's public profile page.
    Supports content negotiation for ActivityPub.
    """
    user = await crud_users.get_user_by_username(db, username=username)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # 1. Content Negotiation for ActivityPub
    accept = request.headers.get("accept", "")
    if "application/activity+json" in accept or "application/ld+json" in accept:
        actor_url = f"{settings.BASE_URL}/users/{username}"
        actor = {
            "@context": [
                "https://www.w3.org/ns/activitystreams",
                "https://w3id.org/security/v1"
            ],
            "id": actor_url,
            "type": "Person",
            "preferredUsername": username,
            "name": user.display_name or username,
            "summary": user.bio or "",
            "icon": {
                "type": "Image",
                "mediaType": "image/png", 
                "url": user.avatar_url
            } if user.avatar_url else None,
            "inbox": f"{actor_url}/inbox",
            "outbox": f"{actor_url}/outbox",
            "publicKey": {
                "id": f"{actor_url}#main-key",
                "owner": actor_url,
                "publicKeyPem": user.rsa_public_key
            }
        }
        import json
        from fastapi import Response
        return Response(
            content=json.dumps(actor), 
            media_type="application/activity+json"
        )

    # 2. Render HTML
    from .. import crud_posts
    from sqlalchemy.future import select
    res = await db.execute(
        select(models.Post)
        .where(models.Post.author_id == user.id)
        .order_by(models.Post.created_at.desc())
    )
    posts = res.scalars().all()

    return templates.TemplateResponse(
        request=request,
        name="profile.html",
        context={
            "profile_user": user,
            "posts": posts,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
            "user": current_user
        }
    )

@router.get("/{username}/media", response_class=HTMLResponse)
async def view_profile_media(
    username: str,
    request: Request,
    db: AsyncSession = Depends(database.get_db),
    current_user: Optional[models.User] = Depends(auth.get_current_user_optional),
):
    """
    Shows media posts for a profile with include/exclude controls for the owner.
    """
    from sqlalchemy.future import select

    profile_user = await crud_users.get_user_by_username(db, username=username)
    if not profile_user:
        raise HTTPException(status_code=404, detail="User not found")

    media_res = await db.execute(
        select(models.Post)
        .where(
            and_(
                models.Post.author_id == profile_user.id,
                models.Post.media_path.isnot(None),
            )
        )
        .order_by(models.Post.created_at.desc())
    )
    media_posts = media_res.scalars().all()
    is_owner = bool(current_user and current_user.id == profile_user.id)
    active_modules = await server_utils.get_active_modules(db)
    visible_module_bar_modules = server_utils.filter_visible_module_bar_modules(
        active_modules,
        current_user.module_bar_config if current_user else None,
    )

    return templates.TemplateResponse(
        request=request,
        name="profile_media.html",
        context={
            "profile_user": profile_user,
            "media_posts": media_posts,
            "is_owner": is_owner,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
            "user": current_user,
            "active_modules": active_modules,
            "visible_module_bar_modules": visible_module_bar_modules,
        },
    )


@router.post("/posts/{post_id}/family-photos-toggle")
async def toggle_family_photos_inclusion(
    post_id: int,
    include_in_family_photos: Annotated[str, Form(...)],
    redirect_to: Annotated[Optional[str], Form()] = None,
    current_user: Annotated[models.User, Depends(auth.get_current_user)] = None,
    db: AsyncSession = Depends(database.get_db),
):
    """
    Owner-only toggle for whether a media post appears in Family Photos.
    """
    from sqlalchemy.future import select

    post_res = await db.execute(select(models.Post).where(models.Post.id == post_id))
    post = post_res.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.author_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not allowed")
    if not post.media_path:
        raise HTTPException(status_code=400, detail="Post has no media")

    include_normalized = str(include_in_family_photos).strip().lower()
    include_value = include_normalized in {"1", "true", "yes", "on"}
    post.include_in_family_photos = include_value
    await db.commit()

    safe_redirect = f"/users/{current_user.username}/media"
    if redirect_to and redirect_to.startswith("/") and not redirect_to.startswith("//"):
        safe_redirect = redirect_to
    return RedirectResponse(url=safe_redirect, status_code=303)

@router.get("/settings/profile", response_class=HTMLResponse)
async def edit_profile_form(
    request: Request,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db)
):
    """
    Renders the profile edit form.
    """
    active_modules = await server_utils.get_active_modules(db)
    hidden_modules = set()
    if current_user.module_bar_config:
        try:
            hidden_modules = server_utils.parse_hidden_module_names(current_user.module_bar_config)
        except Exception:
            hidden_modules = set()
    visible_module_bar_modules = server_utils.filter_visible_module_bar_modules(
        active_modules,
        current_user.module_bar_config,
    )

    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "user": current_user,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
            "active_modules": active_modules,
            "hidden_modules": hidden_modules,
            "visible_module_bar_modules": visible_module_bar_modules,
        }
    )

@router.post("/settings/profile")
async def update_profile(
    request: Request,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    display_name: Annotated[Optional[str], Form()] = None,
    bio: Annotated[Optional[str], Form()] = None,
    birth_month: Annotated[Optional[str], Form()] = None,
    birth_day: Annotated[Optional[str], Form()] = None,
    custom_css: Annotated[Optional[str], Form()] = None,
    theme_preset: Annotated[Optional[str], Form()] = None,
    avatar_file: Optional[UploadFile] = File(None),
    db: AsyncSession = Depends(database.get_db)
):
    """
    Handles profile update form submission.
    """
    avatar_url = current_user.avatar_url
    
    if avatar_file and avatar_file.filename:
        upload_data = await media_utils.save_upload_file(avatar_file)
        avatar_url = upload_data["url"]

    active_modules = await server_utils.get_active_modules(db)
    form_data = await request.form()
    visible_module_names = form_data.getlist("visible_module_names")
    enabled_module_names = {
        module.name.strip().lower()
        for module in active_modules
        if module.name and module.name.strip()
    }
    selected_visible = {
        module_name.strip().lower()
        for module_name in (visible_module_names or [])
        if module_name and module_name.strip()
    }
    normalized_visible = selected_visible.intersection(enabled_module_names)
    hidden_modules = sorted(enabled_module_names - normalized_visible)

    update_data = schemas.UserUpdate(
        display_name=display_name,
        bio=bio,
        avatar_url=avatar_url,
        custom_css=custom_css,
        theme_preset=theme_preset,
        hidden_modules=hidden_modules,
    )
    month_raw = (birth_month or "").strip()
    day_raw = (birth_day or "").strip()
    if month_raw and day_raw:
        try:
            month = int(month_raw)
            day = int(day_raw)
            # Store with fixed canonical year since we only collect month/day.
            update_data.birthdate = date(2000, month, day)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid birthday selection")
    
    await crud_users.update_user(db, current_user, update_data)
    
    return RedirectResponse(url=f"/users/{current_user.username}", status_code=303)


@router.post("/settings/change-password")
async def change_password(
    current_password: Annotated[str, Form(...)],
    new_password: Annotated[str, Form(...)],
    confirm_password: Annotated[str, Form(...)],
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    """
    Update the current user's password.
    """
    if not auth_utils.verify_password(current_password, current_user.password_hash):
        return RedirectResponse(url="/users/settings/profile?pw=bad-current", status_code=303)

    if new_password != confirm_password:
        return RedirectResponse(url="/users/settings/profile?pw=mismatch", status_code=303)

    if len(new_password or "") < 4:
        return RedirectResponse(url="/users/settings/profile?pw=weak", status_code=303)

    if auth_utils.verify_password(new_password, current_user.password_hash):
        return RedirectResponse(url="/users/settings/profile?pw=same", status_code=303)

    current_user.password_hash = auth_utils.get_password_hash(new_password)
    await db.commit()
    return RedirectResponse(url="/users/settings/profile?pw=changed", status_code=303)
