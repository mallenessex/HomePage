from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from typing import Optional
from pathlib import Path

from .. import models, database, server_utils
from . import auth
from ..config import settings
from ..wikipedia_utils import WikipediaReader
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(
    prefix="/wikipedia",
    tags=["wikipedia"]
)

templates = Jinja2Templates(directory=str(settings.BASE_DIR / "templates"))

def _resolve_wikipedia_paths() -> tuple[str, str]:
    configured_dir = (settings.WIKI_DATA_DIR or "").strip()
    configured_bz2 = (settings.WIKI_BZ2_PATH or "").strip()
    configured_db = (settings.WIKI_DB_PATH or "").strip()

    candidate_dirs: list[Path] = []
    if configured_dir:
        candidate_dirs.append(Path(configured_dir))
    candidate_dirs.append(settings.BASE_DIR / "external data" / "wikipedia")
    candidate_dirs.append(Path("/app/external_data/wikipedia"))
    candidate_dirs.append(Path("/app/external data/wikipedia"))

    resolved_dir: Optional[Path] = None
    seen: set[str] = set()
    for candidate in candidate_dirs:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists():
            resolved_dir = candidate
            break

    if not resolved_dir:
        resolved_dir = Path(configured_dir) if configured_dir else (settings.BASE_DIR / "external data" / "wikipedia")

    db_path = Path(configured_db) if configured_db else (resolved_dir / "wikipedia.db")
    if configured_bz2:
        bz2_path = Path(configured_bz2)
    else:
        dumps = sorted(resolved_dir.glob("enwiki-*-pages-articles-multistream.xml.bz2"))
        bz2_path = dumps[-1] if dumps else (resolved_dir / "enwiki-20260101-pages-articles-multistream.xml.bz2")

    return str(bz2_path), str(db_path)


WIKI_FILE, WIKI_DB = _resolve_wikipedia_paths()

reader = WikipediaReader(WIKI_FILE, WIKI_DB)

@router.get("/", response_class=HTMLResponse)
async def wiki_index(
    request: Request,
    q: Optional[str] = None,
    current_user: Optional[models.User] = Depends(auth.get_current_user_optional),
    db: AsyncSession = Depends(database.get_db)
):
    active_modules = await server_utils.get_active_modules(db)
    
    article = None
    article_html = None
    article_is_truncated = False
    featured = []
    results = []
    error = None

    try:
        is_indexed = reader.has_index()

        if q:
            if is_indexed:
                # Fast search using SQLite FTS
                results = reader.search_articles(q)
                if not results:
                    # Try exact title match
                    article = reader.get_article(q)
                    if not article:
                        error = f"No results found for '{q}'."
            else:
                # Slow scan for exact title match
                article = reader.get_article(q)
                if not article:
                    error = f"Article '{q}' not found. Note: Indexing would make this 1000x faster."
            if article:
                article_text = article.get("text") or ""
                article_is_truncated = len(article_text) > 10000
                article_html = reader.render_article_html(article_text, limit=10000)
        elif is_indexed:
            # Load random articles only if indexed (Instant)
            featured = reader.get_random_articles(6)
        else:
            # Fallback for unindexed state (Instant)
            featured = [
                {"title": "Offline Internet", "summary": "This Wikipedia module is currently running in offline mode. Indexing is recommended for full features."},
                {"title": "Search", "summary": "You can still search for specific articles by exact title above."},
                {"title": "Indexing", "summary": "To enable fast search and random articles, please run the indexing script."}
            ]

        return templates.TemplateResponse(
            request=request,
            name="wikipedia.html",
            context={
                "user": current_user,
                "node_name": settings.NODE_NAME,
                "platform_name": settings.PLATFORM_NAME,
                "active_modules": active_modules,
                "article": article,
                "article_html": article_html,
                "article_is_truncated": article_is_truncated,
                "featured": featured,
                "search_results": results,
                "query": q,
                "error": error,
                "is_indexed": is_indexed,
                "wiki_path": WIKI_FILE
            }
        )
    except Exception as e:
        return templates.TemplateResponse(
            request=request,
            name="wikipedia.html",
            context={
                "user": current_user,
                "node_name": settings.NODE_NAME,
                "platform_name": settings.PLATFORM_NAME,
                "error": f"Internal Error: {str(e)}",
                "is_indexed": reader.has_index(),
                "featured": [],
                "search_results": []
            }
        )

@router.get("/random", response_class=HTMLResponse)
async def wiki_random(
    request: Request,
    current_user: Optional[models.User] = Depends(auth.get_current_user_optional)
):
    try:
        if not reader.has_index():
             return RedirectResponse(url="/wikipedia?error=Indexing+required+for+random+articles", status_code=303)
        
        article = reader.get_random_article_from_index()
        if not article:
            raise HTTPException(status_code=404, detail="Could not find a random article")
        article_text = article.get("text") or ""
        article_is_truncated = len(article_text) > 10000
        article_html = reader.render_article_html(article_text, limit=10000)

        return templates.TemplateResponse(
            request=request,
            name="wikipedia.html",
            context={
                "user": current_user,
                "node_name": settings.NODE_NAME,
                "platform_name": settings.PLATFORM_NAME,
                "article": article,
                "article_html": article_html,
                "article_is_truncated": article_is_truncated,
                "featured": [],
                "search_results": [],
                "query": None,
                "error": None,
                "is_indexed": True,
                "wiki_path": WIKI_FILE
            }
        )
    except Exception as e:
        return RedirectResponse(url=f"/wikipedia?error=Random+Error:+{str(e)}", status_code=303)

@router.get("/article/{title}", response_class=HTMLResponse)
async def view_article(
    title: str,
    request: Request,
    current_user: Optional[models.User] = Depends(auth.get_current_user_optional)
):
    article = reader.get_article(title)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    article_text = article.get("text") or ""
    article_is_truncated = len(article_text) > 10000
    article_html = reader.render_article_html(article_text, limit=10000)

    return templates.TemplateResponse(
        request=request,
        name="wikipedia.html",
        context={
            "user": current_user,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
            "article": article,
            "article_html": article_html,
            "article_is_truncated": article_is_truncated,
            "query": title,
            "is_indexed": reader.has_index()
        }
    )
