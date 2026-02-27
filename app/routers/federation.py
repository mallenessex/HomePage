from fastapi import APIRouter, Depends, HTTPException, Query, Response, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func
from typing import Optional
import json
import uuid
import hashlib
import secrets

from .. import crud_users, database, models, auth_utils, federation_utils, content_filter
from ..config import settings

router = APIRouter()

@router.get("/.well-known/webfinger")
async def webfinger(
    resource: str = Query(..., description="The resource to query (e.g. acct:user@domain)"),
    db: AsyncSession = Depends(database.get_db)
):
    """
    WebFinger discovery endpoint.
    Resolves 'acct:username@domain' to the user's profile URL.
    """
    if not resource.startswith("acct:"):
        raise HTTPException(status_code=400, detail="Invalid resource format. Must start with 'acct:'.")
    
    # Extract username. Format: acct:username@domain or just acct:username
    handle = resource[5:] # remove 'acct:'
    if "@" in handle:
        username, domain = handle.split("@", 1)
        allowed_domains = {
            settings.DOMAIN.lower(),
            "localhost",
            "localhost:8001",
            "127.0.0.1:8001",
        }
        if domain.lower() not in allowed_domains:
            raise HTTPException(status_code=404, detail="Resource not hosted on this domain")
    else:
        username = handle

    user = await crud_users.get_user_by_username(db, username=username)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    actor_url = f"{settings.BASE_URL}/users/{username}"
    
    return {
        "subject": resource,
        "links": [
            {
                "rel": "self",
                "type": "application/activity+json",
                "href": actor_url
            },
            {
                "rel": "http://webfinger.net/rel/profile-page",
                "type": "text/html",
                "href": f"{settings.BASE_URL}/@{username}" # We can implement a friendly profile page later
            }
        ]
    }

# Actor endpoint is now handled in routers/users.py with content negotiation.


@router.post("/users/{username}/inbox")
async def inbox(
    username: str,
    request: Request,
    db: AsyncSession = Depends(database.get_db)
):
    """
    ActivityPub Inbox. Handles incoming activities (Follow, Create, etc).
    """
    # 1. Verify User exists
    user = await crud_users.get_user_by_username(db, username=username)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    # 2. Parse Activity
    try:
        activity = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
        
    activity_type = activity.get("type")
    actor_id = activity.get("actor")
    if not activity_type or not actor_id:
        raise HTTPException(status_code=400, detail="Invalid activity: missing type or actor")
    
    # Verify HTTP Signature
    # Note: For simple testing, we might want to toggle this.
    if settings.FEDERATION_ENABLED:
        # Bypass signature check for local testing
        if actor_id.startswith(str(settings.BASE_URL)):
            is_valid = True
        else:
            is_valid = await federation_utils.verify_http_signature(request)
        
        if not is_valid:
             if not actor_id.startswith(str(settings.BASE_URL)):
                raise HTTPException(status_code=401, detail="Invalid HTTP Signature")
    
    # 3. Log Activity
    object_id = None
    obj = activity.get("object")
    if isinstance(obj, dict):
        object_id = obj.get("id")
    elif isinstance(obj, str):
        object_id = obj

    log = models.ActivityLog(
        activity_type=activity_type,
        actor_id=actor_id,
        object_id=object_id,
        raw_json=json.dumps(activity)
    )
    db.add(log)
    await db.commit()
    
    # 4. Handle Activity
    if activity_type == "Follow":
        await handle_follow(db, user, activity)
    elif activity_type == "Create":
        await handle_create(db, user, activity)
    # else: Ignore or log
    
    return Response(status_code=202) # Accepted

@router.get("/users/{username}/outbox")
async def outbox(
    username: str,
    db: AsyncSession = Depends(database.get_db)
):
    """
    ActivityPub Outbox. Returns a collection of activities by the user.
    """
    user = await crud_users.get_user_by_username(db, username=username)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    # In real AP, this should return an OrderedCollection with pages.
    # For MVP, we'll return a basic list of 'Create Note' activities.
    
    from .. import crud_posts
    posts = await crud_posts.get_timeline(db) # This gets all posts, we should filter by author
    # TODO: filter by author_id == user.id
    
    outbox_url = f"{settings.BASE_URL}/users/{username}/outbox"
    
    activities = []
    for post in posts:
        if post.author_id != user.id: continue
        
        note = {
            "id": f"{settings.BASE_URL}/posts/{post.id}",
            "type": "Note",
            "published": post.created_at.isoformat() + "Z",
            "attributedTo": f"{settings.BASE_URL}/users/{username}",
            "content": post.body,
            "to": ["https://www.w3.org/ns/activitystreams#Public"]
        }
        
        activities.append({
            "id": f"{settings.BASE_URL}/activities/{post.id}",
            "type": "Create",
            "actor": f"{settings.BASE_URL}/users/{username}",
            "object": note
        })

    collection = {
        "@context": "https://www.w3.org/ns/activitystreams",
        "id": outbox_url,
        "type": "OrderedCollection",
        "totalItems": len(activities),
        "orderedItems": activities
    }
    
    return Response(content=json.dumps(collection), media_type="application/activity+json")

async def handle_follow(db: AsyncSession, user: models.User, activity: dict):
    """
    Handles a Follow request.
    Automatically accepts for now (or pending based on policy).
    Sends an Accept activity back.
    """
    actor_id = activity.get("actor")
    # For MVP, we need to fetch the actor to get their inbox!
    # But for now, let's assume we can reply if we implement delivery logic.
    
    # Store follower
    # Check if exists first
    stmt = select(models.Follower).where(
        models.Follower.local_user_id == user.id,
        models.Follower.remote_actor_id == actor_id
    )
    result = await db.execute(stmt)
    follower = result.scalars().first()
    
    # Try to get remote inbox (we might have it if we've seen them before)
    remote_inbox = actor_id + "/inbox" # Fallback
    
    if not follower:
        follower = models.Follower(
            local_user_id=user.id,
            remote_actor_id=actor_id,
            remote_inbox_url=remote_inbox,
            status="accepted"
        )
        db.add(follower)
        await db.commit()
    else:
        remote_inbox = follower.remote_inbox_url

    # Deliver 'Accept' activity
    accept_activity = {
        "@context": "https://www.w3.org/ns/activitystreams",
        "id": f"{settings.BASE_URL}/activities/{uuid.uuid4()}",
        "type": "Accept",
        "actor": f"{settings.BASE_URL}/users/{user.username}",
        "object": activity
    }
    
    # We can't use BackgroundTasks here easily because handle_follow is not a route handler
    # but we can pass it down if we want, or just await it (inbox is async anyway).
    await federation_utils.sign_and_send_activity(
        user,
        accept_activity,
        remote_inbox
    )

async def handle_create(db: AsyncSession, local_user: models.User, activity: dict):
    """
    Handles incoming posts (Create -> Note).
    Saves them to the database for display in the timeline.
    """
    actor_id = activity.get("actor")
    obj = activity.get("object")
    
    if not obj or obj.get("type") != "Note":
        return

    content = obj.get("content", "")
    
    # Check Content Filter
    is_safe, word = content_filter.is_content_safe(content)
    if not is_safe:
        print(f"DEBUG: Blocked unsafe federated content from {actor_id} (word: {word})")
        return

    remote_post_id = obj.get("id")
    if remote_post_id:
        # The request is already logged once in inbox; if this object_id appears more than once,
        # this is likely a replay/duplicate delivery.
        dup_stmt = (
            select(func.count(models.ActivityLog.id))
            .where(models.ActivityLog.activity_type == "Create")
            .where(models.ActivityLog.object_id == remote_post_id)
        )
        dup_res = await db.execute(dup_stmt)
        if (dup_res.scalar() or 0) > 1:
            return

    remote_user = await _get_or_create_remote_shadow_user(db, actor_id)
    attributed_to = (obj.get("attributedTo") or actor_id or "")[:200]
    signature = f"remote:{hashlib.sha256(attributed_to.encode()).hexdigest()[:16]}"

    db_post = models.Post(
        title=None,
        body=content,
        author_id=remote_user.id if remote_user else local_user.id,
        signature=signature,
        visibility="public",
    )
    db.add(db_post)
    await db.commit()


async def _get_or_create_remote_shadow_user(db: AsyncSession, actor_id: str) -> Optional[models.User]:
    """
    Creates a stable local shadow user for remote actors so federated notes can render in
    the same feed templates as local posts.
    """
    actor_hash = hashlib.sha1(actor_id.encode()).hexdigest()[:10]
    base_username = f"remote_{actor_hash}"

    stmt = select(models.User).where(models.User.username == base_username)
    res = await db.execute(stmt)
    existing = res.scalar_one_or_none()
    if existing:
        return existing

    actor_data = await federation_utils.discover_remote_actor(actor_id)
    display_name = None
    if actor_data:
        display_name = actor_data.get("name") or actor_data.get("preferredUsername")

    public_key_hex, private_key_hex = auth_utils.generate_keypair()
    rsa_public_pem, rsa_private_pem = auth_utils.generate_rsa_keypair()
    random_password = auth_utils.get_password_hash(secrets.token_urlsafe(24))

    remote_user = models.User(
        username=base_username,
        display_name=display_name or base_username,
        password_hash=random_password,
        public_key=public_key_hex,
        private_key_enc=private_key_hex,
        rsa_public_key=rsa_public_pem,
        rsa_private_key_enc=rsa_private_pem,
        role="child",
        can_post=False,
    )
    db.add(remote_user)
    await db.commit()
    await db.refresh(remote_user)
    return remote_user
