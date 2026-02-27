from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from typing import Annotated, Optional
import datetime

from .. import models, database, auth_utils, server_utils, permissions
from . import auth
from ..config import settings

router = APIRouter(
    prefix="/discussions",
    tags=["discussions"]
)

templates = Jinja2Templates(directory=str(settings.BASE_DIR / "templates"))

@router.get("/", response_class=HTMLResponse)
async def forum_index(
    request: Request,
    db: AsyncSession = Depends(database.get_db),
    current_user: Optional[models.User] = Depends(auth.get_current_user_optional)
):
    # Fetch Categories
    result = await db.execute(select(models.ForumCategory).order_by(models.ForumCategory.order))
    categories = result.scalars().all()
    
    active_modules = await server_utils.get_active_modules(db)

    return templates.TemplateResponse(
        request=request,
        name="discussions/index.html",
        context={
            "user": current_user,
            "categories": categories,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
            "active_modules": active_modules
        }
    )

@router.get("/category/{slug}", response_class=HTMLResponse)
async def view_category(
    slug: str,
    request: Request,
    db: AsyncSession = Depends(database.get_db),
    current_user: Optional[models.User] = Depends(auth.get_current_user_optional)
):
    result = await db.execute(select(models.ForumCategory).where(models.ForumCategory.slug == slug))
    category = result.scalar_one_or_none()
    
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
        
    # Fetch Threads
    result = await db.execute(
        select(models.ForumThread)
        .where(models.ForumThread.category_id == category.id)
        .options(selectinload(models.ForumThread.author))
        .order_by(models.ForumThread.is_pinned.desc(), models.ForumThread.updated_at.desc())
    )
    threads = result.scalars().all()
    
    active_modules = await server_utils.get_active_modules(db)

    return templates.TemplateResponse(
        request=request,
        name="discussions/category.html",
        context={
            "user": current_user,
            "category": category,
            "threads": threads,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
            "active_modules": active_modules
        }
    )

@router.get("/thread/{thread_id}", response_class=HTMLResponse)
async def view_thread(
    thread_id: int,
    request: Request,
    db: AsyncSession = Depends(database.get_db),
    current_user: Optional[models.User] = Depends(auth.get_current_user_optional)
):
    result = await db.execute(
        select(models.ForumThread)
        .where(models.ForumThread.id == thread_id)
        .options(
            selectinload(models.ForumThread.author),
            selectinload(models.ForumThread.category),
            selectinload(models.ForumThread.posts).selectinload(models.ForumPost.author)
        )
    )
    thread = result.scalar_one_or_none()
    
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
        
    # Increment view count
    thread.view_count += 1
    await db.commit()
    
    active_modules = await server_utils.get_active_modules(db)

    return templates.TemplateResponse(
        request=request,
        name="discussions/thread.html",
        context={
            "user": current_user,
            "thread": thread,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
            "active_modules": active_modules
        }
    )

@router.post("/category/{slug}/create")
async def create_thread(
    slug: str,
    title: Annotated[str, Form()],
    content: Annotated[str, Form()],
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db)
):
    # Verify Category
    result = await db.execute(select(models.ForumCategory).where(models.ForumCategory.slug == slug))
    category = result.scalar_one_or_none()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
        
    # Create Thread
    thread = models.ForumThread(
        title=title,
        category_id=category.id,
        user_id=current_user.id
    )
    db.add(thread)
    await db.flush() # Get ID
    
    # Create First Post
    post = models.ForumPost(
        content=content,
        thread_id=thread.id,
        user_id=current_user.id
    )
    db.add(post)
    await db.commit()
    
    return RedirectResponse(url=f"/discussions/thread/{thread.id}", status_code=303)

@router.post("/thread/{thread_id}/reply")
async def reply_thread(
    thread_id: int,
    content: Annotated[str, Form()],
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db)
):
    result = await db.execute(select(models.ForumThread).where(models.ForumThread.id == thread_id))
    thread = result.scalar_one_or_none()
    
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
        
    if thread.is_locked:
         raise HTTPException(status_code=403, detail="Thread is locked")

    post = models.ForumPost(
        content=content,
        thread_id=thread.id,
        user_id=current_user.id
    )
    db.add(post)
    
    # Update thread updated_at
    thread.updated_at = datetime.datetime.utcnow()
    
    await db.commit()
    
    return RedirectResponse(url=f"/discussions/thread/{thread.id}", status_code=303)

# Admin Tools
@router.post("/categories/create")
async def create_category(
    name: Annotated[str, Form()],
    description: Annotated[str, Form()],
    slug: Annotated[str, Form()],
    order: Annotated[int, Form()],
    current_user: Annotated[models.User, Depends(auth.get_current_user)],
    db: AsyncSession = Depends(database.get_db)
):
    permissions.require_admin(current_user)
    
    cat = models.ForumCategory(
        name=name,
        description=description,
        slug=slug,
        order=order
    )
    db.add(cat)
    await db.commit()
    
    return RedirectResponse(url="/discussions", status_code=303)
