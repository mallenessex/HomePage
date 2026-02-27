from fastapi import APIRouter, Depends, HTTPException, Request, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import Annotated, Optional
import uuid
import json

from .. import schemas, auth_utils, database, models, federation_utils, permissions, server_utils
from . import auth
from ..config import settings

router = APIRouter(
    prefix="/community",
    tags=["community"]
)

templates = Jinja2Templates(directory=str(settings.BASE_DIR / "templates"))

@router.get("/", response_class=HTMLResponse)
async def show_community(
    request: Request,
    db: AsyncSession = Depends(database.get_db),
    current_user: Optional[models.User] = Depends(auth.get_current_user_optional)
):
    """
    Renders the community/discovery page.
    """
    # 1. Fetch current following
    # Note: We don't have user in request yet without cookie auth.
    # For now, let's just show public 'Following' log or empty.
    
    # 2. Get some recommendations or local users
    stmt = select(models.User).limit(10)
    res = await db.execute(stmt)
    local_users = res.scalars().all()
    
    from .. import server_utils
    active_modules = await server_utils.get_active_modules(db)

    return templates.TemplateResponse(
        request=request,
        name="community.html",
        context={
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
            "local_users": local_users,
            "user": current_user,
            "active_modules": active_modules
        }
    )

@router.post("/follow")
async def follow_user(
    request: Request,
    handle: Annotated[str, Form()],
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db)
):
    """
    Logic to follow a remote user.
    """
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Enforce family-safe external follow policies.
    is_external = False
    if "@" in handle:
        _, domain = handle.rsplit("@", 1)
        local_domains = {
            settings.DOMAIN.lower(),
            "localhost",
            "localhost:8001",
            "127.0.0.1:8001",
        }
        is_external = domain.lower() not in local_domains

    if is_external:
        if not permissions.can_follow_external_users(current_user):
            raise HTTPException(status_code=403, detail="External follows are disabled for this account.")
        server_settings = await server_utils.get_server_settings(db)
        if server_settings.allow_external_follows != 1:
            raise HTTPException(status_code=403, detail="External follows are disabled on this server.")

    # 1. Discover Actor URL
    actor_url = await federation_utils.perform_webfinger(handle)
    if not actor_url:
        return HTMLResponse(content=f"<div class='error'>Could not find user {handle}</div>")

    # 2. Fetch Actor Data
    actor_data = await federation_utils.discover_remote_actor(actor_url)
    if not actor_data:
        return HTMLResponse(content=f"<div class='error'>Could not fetch actor data for {actor_url}</div>")

    # 3. Save to Following table
    remote_inbox = actor_data.get("inbox")
    remote_username = actor_data.get("preferredUsername") or actor_data.get("name")
    
    following = models.Following(
        local_user_id=current_user.id,
        remote_actor_id=actor_url,
        remote_inbox_url=remote_inbox,
        remote_username=remote_username,
        status="pending"
    )
    db.add(following)
    await db.commit()

    # 4. Construct and Send Follow Activity
    activity = {
        "@context": "https://www.w3.org/ns/activitystreams",
        "id": f"{settings.BASE_URL}/activities/{uuid.uuid4()}",
        "type": "Follow",
        "actor": f"{settings.BASE_URL}/users/{current_user.username}",
        "object": actor_url
    }
    
    # Send it (awaiting for feedback in UI)
    status = await federation_utils.sign_and_send_activity(
        current_user,
        activity,
        remote_inbox
    )
    
    return HTMLResponse(content=f"<div class='success'>Successfully sent follow request to {handle}! (Status: {status})</div>")
