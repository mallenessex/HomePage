from fastapi import APIRouter, Depends, HTTPException, Request, Form, BackgroundTasks, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import and_, func, select
from typing import Annotated, Optional

from .. import schemas, crud_posts, auth_utils, database, models, media_utils
from . import auth
from ..config import settings

router = APIRouter(
    tags=["posts"]
)

# Setup Templates
templates = Jinja2Templates(directory=str(settings.BASE_DIR / "templates"))

LIVEJOURNAL_MOODS = [
    "accomplished",
    "aggravated",
    "amused",
    "angry",
    "annoyed",
    "anxious",
    "apathetic",
    "artistic",
    "awake",
    "bitchy",
    "blah",
    "blank",
    "bored",
    "bouncy",
    "busy",
    "calm",
    "chipper",
    "cold",
    "confused",
    "contemplative",
    "content",
    "cranky",
    "crappy",
    "crazy",
    "creative",
    "crushed",
    "curious",
    "cynical",
    "depressed",
    "determined",
    "devious",
    "disappointed",
    "discontent",
    "ditzy",
    "dorky",
    "drained",
    "drunk",
    "ecstatic",
    "embarrassed",
    "energetic",
    "enraged",
    "enthralled",
    "envious",
    "excited",
    "exhausted",
    "flirty",
    "frustrated",
    "full",
    "geeky",
    "giddy",
    "giggly",
    "gloomy",
    "good",
    "grateful",
    "groggy",
    "grumpy",
    "guilty",
    "happy",
    "high",
    "hopeful",
    "horny",
    "hot",
    "hungry",
    "hyper",
    "impressed",
    "indifferent",
    "infuriated",
    "intimidated",
    "irate",
    "irritated",
    "jealous",
    "jubilant",
    "lazy",
    "lethargic",
    "listless",
    "lonely",
    "loved",
    "melancholy",
    "mellow",
    "mischievous",
    "moody",
    "morose",
    "naughty",
    "nauseated",
    "nerdy",
    "nervous",
    "nostalgic",
    "okay",
    "optimistic",
    "peaceful",
    "pensive",
    "pessimistic",
    "predatory",
    "productive",
    "quixotic",
    "recumbent",
    "refreshed",
    "rejected",
    "relaxed",
    "relieved",
    "restless",
    "rushed",
    "sad",
    "satisfied",
    "scared",
    "sick",
    "silly",
    "sleepy",
    "sore",
    "stressed",
    "surprised",
    "sympathetic",
    "touched",
    "uncomfortable",
    "weird",
    "working",
    "worried",
]
CUSTOM_MOOD_VALUE = "__custom__"


def _normalize_submitted_mood(mood: Optional[str], custom_mood: Optional[str]) -> Optional[str]:
    selected = (mood or "").strip().lower()
    custom = (custom_mood or "").strip()
    if not selected:
        return None
    if selected == CUSTOM_MOOD_VALUE:
        if not custom:
            return None
        return custom[:64]
    if selected not in LIVEJOURNAL_MOODS:
        return None
    return selected

@router.post("/api/posts", response_model=schemas.PostResponse)
async def create_post_api(
    post: schemas.PostCreate,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(database.get_db)
):
    """
    Create a new post via API (JSON).
    """
    from .. import server_utils, permissions
    
    # 1. Check Permissions
    if not permissions.can_post(current_user):
        raise HTTPException(status_code=403, detail="Your account does not have permission to post.")
        
    # 2. Check Policies
    full_content = f"{post.title or ''} {post.body}"
    await server_utils.check_content_policy(db, current_user.id, full_content)
    
    try:
        return await crud_posts.create_post(
            db=db, 
            post_data=post, 
            user=current_user,
            background_tasks=background_tasks
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/api/posts/feed", response_model=list[schemas.PostResponse])
async def read_feed_api(
    db: AsyncSession = Depends(database.get_db),
    current_user: Annotated[models.User, Depends(auth.get_current_user)] = None
):
    """
    Get the social timeline as JSON (for dedicated clients).
    """
    return await crud_posts.get_timeline(db)

@router.get("/feed", response_class=HTMLResponse)
async def read_feed(
    request: Request,
    db: AsyncSession = Depends(database.get_db),
    current_user: Optional[models.User] = Depends(auth.get_current_user_optional)
):
    """
    Render the timeline using Jinja2.
    """
    from .. import server_utils
    active_modules = await server_utils.get_active_modules(db)
    posts = await crud_posts.get_timeline(db)
    post_ids = [post.id for post in posts]
    reaction_counts: dict[int, dict[str, int]] = {}
    my_reactions: dict[int, set[str]] = {}

    if post_ids:
        count_res = await db.execute(
            select(models.PostReaction.post_id, models.PostReaction.emoji, func.count(models.PostReaction.id))
            .where(models.PostReaction.post_id.in_(post_ids))
            .group_by(models.PostReaction.post_id, models.PostReaction.emoji)
        )
        for post_id, emoji, count in count_res.all():
            reaction_counts.setdefault(post_id, {})[emoji] = int(count or 0)

        if current_user:
            mine_res = await db.execute(
                select(models.PostReaction.post_id, models.PostReaction.emoji)
                .where(
                    and_(
                        models.PostReaction.post_id.in_(post_ids),
                        models.PostReaction.user_id == current_user.id,
                    )
                )
            )
            for post_id, emoji in mine_res.all():
                my_reactions.setdefault(post_id, set()).add(emoji)

    reaction_options = ["👍", "❤️", "😂", "😮", "😢", "🔥"]
    return templates.TemplateResponse(
        request=request, 
        name="index.html", 
        context={
            "posts": posts, 
            "node_name": settings.NODE_NAME, 
            "platform_name": settings.PLATFORM_NAME,
            "user": current_user,
            "active_modules": active_modules,
            "reaction_counts": reaction_counts,
            "my_reactions": my_reactions,
            "reaction_options": reaction_options,
            "mood_options": LIVEJOURNAL_MOODS,
            "custom_mood_value": CUSTOM_MOOD_VALUE,
        }
    )


@router.post("/posts/{post_id}/react")
async def toggle_reaction(
    request: Request,
    post_id: int,
    emoji: Annotated[str, Form()],
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    allowed = {"👍", "❤️", "😂", "😮", "😢", "🔥"}
    if emoji not in allowed:
        raise HTTPException(status_code=400, detail="Unsupported reaction")

    post_res = await db.execute(select(models.Post).where(models.Post.id == post_id))
    post = post_res.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    existing_res = await db.execute(
        select(models.PostReaction).where(
            and_(
                models.PostReaction.post_id == post_id,
                models.PostReaction.user_id == current_user.id,
                models.PostReaction.emoji == emoji,
            )
        )
    )
    existing = existing_res.scalar_one_or_none()
    if existing:
        await db.delete(existing)
        reacted = False
    else:
        db.add(models.PostReaction(post_id=post_id, user_id=current_user.id, emoji=emoji))
        reacted = True

    await db.commit()
    count_res = await db.execute(
        select(func.count(models.PostReaction.id)).where(
            and_(models.PostReaction.post_id == post_id, models.PostReaction.emoji == emoji)
        )
    )
    count = int(count_res.scalar() or 0)

    if request.headers.get("x-requested-with", "").lower() == "xmlhttprequest":
        return JSONResponse(
            {"ok": True, "post_id": post_id, "emoji": emoji, "count": count, "reacted": reacted}
        )
    return RedirectResponse(url="/feed", status_code=303)


@router.post("/posts/{post_id}/delete")
async def delete_post(
    request: Request,
    post_id: int,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db),
):
    post_res = await db.execute(select(models.Post).where(models.Post.id == post_id))
    post = post_res.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.author_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the post author can delete this post")

    await db.delete(post)
    await db.commit()

    referer = (request.headers.get("referer") or "").strip()
    if referer:
        return RedirectResponse(url=referer, status_code=303)
    return RedirectResponse(url="/feed", status_code=303)

@router.post("/posts/submit")
async def submit_post_form(
    request: Request,
    body: Annotated[str, Form()],
    background_tasks: BackgroundTasks,
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    title: Annotated[Optional[str], Form()] = None,
    mood: Annotated[Optional[str], Form()] = None,
    custom_mood: Annotated[Optional[str], Form()] = None,
    image: Optional[UploadFile] = File(None),
    db: AsyncSession = Depends(database.get_db)
):
    """
    Handles form-based post submission with optional image.
    """
    from .. import server_utils, permissions
    
    # 1. Check Permissions
    if not permissions.can_post(current_user):
        raise HTTPException(status_code=403, detail="Your account does not have permission to post.")

    # 2. Check Policies
    full_content = f"{title or ''} {body}"
    await server_utils.check_content_policy(db, current_user.id, full_content)
    
    media_path = None
    media_type = None
    
    if image and image.filename:
        media_data = await media_utils.save_upload_file(image)
        media_path = media_data["filename"]
        media_type = media_data["type"]
    normalized_mood = _normalize_submitted_mood(mood, custom_mood)

    post_in = schemas.PostCreate(
        body=body, 
        title=title, 
        mood=normalized_mood,
        media_path=media_path,
        media_type=media_type
    )
    
    try:
        await crud_posts.create_post(
            db=db, 
            post_data=post_in, 
            user=current_user,
            background_tasks=background_tasks
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    return RedirectResponse(url="/feed", status_code=303)
