from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import desc
from sqlalchemy.orm import selectinload
from datetime import datetime
from fastapi import BackgroundTasks
from . import models, schemas, auth_utils, federation_utils, content_filter
from .config import settings
import logging
logger = logging.getLogger(__name__)

async def create_post(db: AsyncSession, post_data: schemas.PostCreate, user: models.User, background_tasks: BackgroundTasks = None):
    """
    Creates a new post and signs it with the user's private key.
    """
    # 0. Content Filter
    is_safe, word = content_filter.is_content_safe(post_data.body)
    if not is_safe:
        raise ValueError(f"Content contains forbidden word: {word}")
    
    if post_data.title:
        is_safe_title, word_title = content_filter.is_content_safe(post_data.title)
        if not is_safe_title:
            raise ValueError(f"Title contains forbidden word: {word_title}")

    # 1. Prepare data for signing
    timestamp = datetime.utcnow().isoformat()
    message = f"{timestamp}|{post_data.body}|{post_data.media_path or ''}".encode()
    
    # 2. Sign
    signature = auth_utils.sign_message(user.private_key_enc, message)
    
    # 3. Save to DB
    db_post = models.Post(
        title=post_data.title,
        body=post_data.body,
        media_path=post_data.media_path,
        media_type=post_data.media_type,
        mood=post_data.mood,
        author_id=user.id,
        signature=signature,
        visibility=post_data.visibility
    )
    
    db.add(db_post)
    await db.commit()
    await db.refresh(db_post)
    
    # Manually attach the user object so Pydantic doesn't trigger lazy load
    db_post.author = user
    
    # Trigger Federation
    if background_tasks:
        background_tasks.add_task(federate_post, db_post.id, user.id)

    return db_post

async def federate_post(post_id: int, user_id: int):
    """
    Finds all followers and pushes the post to their inboxes.
    Uses a fresh session because the request session will be closed.
    """
    from .database import get_db
    
    async for db in get_db():
        logger.info(f"Starting federation for post {post_id} by user {user_id}")
        # 1. Fetch Post and User (to be sure we have fresh data and loaded relationships)
        stmt_user = select(models.User).where(models.User.id == user_id)
        res_user = await db.execute(stmt_user)
        user = res_user.scalar_one_or_none()
        
        stmt_post = select(models.Post).where(models.Post.id == post_id)
        res_post = await db.execute(stmt_post)
        post = res_post.scalar_one_or_none()
        
        if not user or not post:
            return

        # 2. Fetch Followers
        stmt = select(models.Follower).where(
            models.Follower.local_user_id == user_id,
            models.Follower.status == "accepted"
        )
        result = await db.execute(stmt)
        followers = result.scalars().all()
        
        if not followers:
            return
            
        # 3. Build Activity
        activity = {
            "@context": "https://www.w3.org/ns/activitystreams",
            "id": f"{settings.BASE_URL}/activities/{post.id}",
            "type": "Create",
            "actor": f"{settings.BASE_URL}/users/{user.username}",
            "object": {
                "id": f"{settings.BASE_URL}/posts/{post.id}",
                "type": "Note",
                "published": post.created_at.isoformat() + "Z",
                "attributedTo": f"{settings.BASE_URL}/users/{user.username}",
                "content": post.body,
                "to": ["https://www.w3.org/ns/activitystreams#Public"]
            }
        }
        
        # 4. Deliver
        for follower in followers:
            await federation_utils.sign_and_send_activity(
                user, 
                activity, 
                follower.remote_inbox_url
            )
        break # Exit after one session loop

async def get_timeline(db: AsyncSession, limit: int = 20, offset: int = 0):
    """
    Retrieves the timeline (chronological order of all posts).
    """
    result = await db.execute(
        select(models.Post)
        .options(selectinload(models.Post.author))
        .order_by(desc(models.Post.created_at))
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()
