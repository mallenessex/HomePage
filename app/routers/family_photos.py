from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import database, models, server_utils
from . import auth
from ..config import settings

router = APIRouter(prefix="/family-photos", tags=["family_photos"])

templates = Jinja2Templates(directory=str(settings.BASE_DIR / "templates"))


@router.get("/", response_class=HTMLResponse)
async def family_photos_page(
    request: Request,
    db: AsyncSession = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    media_res = await db.execute(
        select(models.Post)
        .options(selectinload(models.Post.author))
        .where(
            and_(
                models.Post.media_path.isnot(None),
                models.Post.media_path != "",
                models.Post.include_in_family_photos == True,
            )
        )
        .order_by(models.Post.created_at.desc())
        .limit(500)
    )
    media_posts = media_res.scalars().all()
    active_modules = await server_utils.get_active_modules(db)
    visible_module_bar_modules = server_utils.filter_visible_module_bar_modules(
        active_modules,
        current_user.module_bar_config if current_user else None,
    )

    return templates.TemplateResponse(
        request=request,
        name="family_photos.html",
        context={
            "user": current_user,
            "media_posts": media_posts,
            "active_modules": active_modules,
            "visible_module_bar_modules": visible_module_bar_modules,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
        },
    )
