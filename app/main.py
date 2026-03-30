from fastapi import FastAPI, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from typing import Optional
from pydantic import BaseModel
from sqlalchemy.exc import OperationalError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import asyncio
import os
import subprocess
import shutil
import time
import ipaddress
import socket

from .config import settings
from . import models, database, server_models, server_utils
from .database import init_db
from . import server_identity

# Core routers — always loaded
from .routers import auth, posts, media, federation, community, users, admin, setup

# Optional routers — loaded only if enabled in data/enabled_modules.json
_loaded_modules = settings.loaded_modules
print(f"Enabled optional modules: {sorted(_loaded_modules) if _loaded_modules else '(all)'}")

calendar = None
games = None
wikipedia = None
maps = None
family_photos = None
discussions = None
voice = None
mail = None
chat = None
chat_ex = None
create = None

if "calendar" in _loaded_modules:
    from .routers import calendar
if "games" in _loaded_modules:
    from .routers import games
if "wikipedia" in _loaded_modules:
    from .routers import wikipedia
if "maps" in _loaded_modules:
    from .routers import maps
if "family_photos" in _loaded_modules:
    from .routers import family_photos
if "discussions" in _loaded_modules:
    from .routers import discussions
if "voice" in _loaded_modules:
    from .routers import voice
if "mail" in _loaded_modules:
    from .routers import mail
if "chat" in _loaded_modules:
    from .routers import chat
    from .routers import chat_ex
if "create" in _loaded_modules:
    from .routers import create


class HandshakeRequest(BaseModel):
    target_server_id: str
    peer_server_id: str
    requested_username: Optional[str] = None
    requested_display_name: Optional[str] = None
    note: Optional[str] = None
    nonce: Optional[str] = None
    password_hash: Optional[str] = None  # argon2 hash of the user's chosen password


class WebJoinRequest(BaseModel):
    username: str
    display_name: Optional[str] = None
    password: str


class PasswordResetPayload(BaseModel):
    username: str
    peer_server_id: str
    new_password_hash: str

# Auth-guarded path prefixes — built dynamically from loaded modules
_ALWAYS_AUTH_PREFIXES = [
    "/feed", "/api/posts", "/posts/submit", "/community", "/channels",
]
_OPTIONAL_PREFIX_MAP = {
    "calendar":    ["/calendar"],
    "games":       ["/games"],
    "wikipedia":   ["/wikipedia"],
    "maps":        ["/maps"],
    "family_photos": ["/family-photos"],
    "discussions": ["/discussions"],
    "voice":       ["/voice"],
    "mail":        ["/mail"],
    "chat":        ["/chat", "/chat-ex"],
    "create":      ["/create"],
}
MODULE_PATH_PREFIXES = tuple(
    _ALWAYS_AUTH_PREFIXES
    + [p for mod, prefixes in _OPTIONAL_PREFIX_MAP.items() if mod in _loaded_modules for p in prefixes]
)


async def _get_external_join_policy() -> str:
    try:
        async with database.AsyncSessionLocal() as db:
            settings_row = await server_utils.get_server_settings(db)
            policy = (getattr(settings_row, "external_join_policy", None) or "conditional").strip().lower()
            if policy in {"conditional", "none", "all"}:
                return policy
    except Exception:
        pass
    return "conditional"


_SECURE_MODE_CACHE: dict[str, object] = {
    "expires_at": 0.0,
    "enabled": False,
    "domain": None,
}

_SECURE_DOMAIN_RESOLVE_CACHE: dict[str, object] = {
    "domain": None,
    "checked_at": 0.0,
    "resolvable": False,
}


def _truthy(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _is_private_or_local_host(host: Optional[str]) -> bool:
    if not host:
        return False
    value = host.strip().lower().strip("[]")
    if value in {"localhost", "127.0.0.1", "::1"} or value.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(value)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return False


def _is_resolvable_hostname(host: Optional[str]) -> bool:
    if not host:
        return False
    value = host.strip().lower().strip("[]")
    if not value:
        return False
    try:
        socket.getaddrinfo(value, None)
        return True
    except OSError:
        return False


def _is_resolvable_hostname_cached(host: Optional[str], ttl_seconds: float = 60.0) -> bool:
    value = (host or "").strip().lower()
    if not value:
        return False
    now = time.monotonic()
    cached_domain = str(_SECURE_DOMAIN_RESOLVE_CACHE.get("domain") or "").strip().lower()
    cached_checked_at = float(_SECURE_DOMAIN_RESOLVE_CACHE.get("checked_at", 0.0) or 0.0)
    if cached_domain == value and (now - cached_checked_at) < ttl_seconds:
        return bool(_SECURE_DOMAIN_RESOLVE_CACHE.get("resolvable", False))

    resolvable = _is_resolvable_hostname(value)
    _SECURE_DOMAIN_RESOLVE_CACHE["domain"] = value
    _SECURE_DOMAIN_RESOLVE_CACHE["checked_at"] = now
    _SECURE_DOMAIN_RESOLVE_CACHE["resolvable"] = bool(resolvable)
    return bool(resolvable)


async def _get_secure_mode_redirect_config() -> tuple[bool, Optional[str]]:
    now = time.monotonic()
    expires_at = float(_SECURE_MODE_CACHE.get("expires_at", 0.0) or 0.0)
    if now < expires_at:
        cached_enabled = bool(_SECURE_MODE_CACHE.get("enabled", False))
        cached_domain = _SECURE_MODE_CACHE.get("domain")
        return cached_enabled, str(cached_domain) if cached_domain else None

    enabled = False
    domain: Optional[str] = None
    try:
        async with database.AsyncSessionLocal() as db:
            settings_row = await server_utils.get_server_settings(db)
            enabled = _truthy(getattr(settings_row, "secure_mode_enabled", 0))
            domain = server_utils.normalize_secure_local_domain(
                getattr(settings_row, "secure_local_domain", None)
            )
    except Exception:
        enabled = False
        domain = None

    _SECURE_MODE_CACHE["expires_at"] = now + 15.0
    _SECURE_MODE_CACHE["enabled"] = enabled
    _SECURE_MODE_CACHE["domain"] = domain
    return enabled, domain

@asynccontextmanager
async def lifespan(app: FastAPI):
    crossword_scheduler_task: Optional[asyncio.Task] = None
    # Startup: Database init
    print("Initialize DB...")
    print(f"DATABASE_URL: {settings.DATABASE_URL}")
    try:
        await init_db()
        # Initialize modules
        from . import server_utils
        async with database.AsyncSessionLocal() as db:
            await server_utils.init_default_modules(db)
            await server_utils.get_server_settings(db)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"DB Init Failed: {e}")
        
    print("Starting up...")
    local_server_id = server_identity.get_or_create_server_id()
    print(f"Server ID: {local_server_id}")

    # Auto-build any missing client binaries that can be built on this host
    try:
        import asyncio
        from .client_builder import build_all_missing
        build_results = await asyncio.to_thread(build_all_missing)
        for plat, status in build_results.items():
            print(f"  Client build [{plat}]: {status}")
    except Exception as e:
        print(f"WARNING: Client auto-build check failed: {e}")

    # Pre-build client download packages so admin downloads are instant
    try:
        from .client_packager import prebuild_client_packages
        async with database.AsyncSessionLocal() as _db:
            _ss = await server_utils.get_server_settings(_db)
            built = await prebuild_client_packages(_db, _ss, local_server_id)
            if built:
                print(f"Pre-built client packages: {built}")
            else:
                print("No client binaries found in dist/ — skipping package pre-build.")
    except Exception as e:
        print(f"WARNING: Client package pre-build failed: {e}")

    delete_routes = [
        route.path
        for route in app.routes
        if getattr(route, "path", "").startswith("/admin/users/") and "delete" in getattr(route, "path", "")
    ]
    if delete_routes:
        print(f"Loaded admin delete routes: {', '.join(delete_routes)}")
    topic_routes = [
        route.path
        for route in app.routes
        if "topic/update" in getattr(route, "path", "")
    ]
    if topic_routes:
        print(f"Loaded chat topic routes: {', '.join(topic_routes)}")
    if games and hasattr(games, "run_crossword_scheduler"):
        crossword_scheduler_task = asyncio.create_task(
            games.run_crossword_scheduler(),
            name="crossword-scheduler",
        )
        print("Crossword scheduler started.")

    # Start Java Scorch 2000 game server (port 4242) if java is available
    scorch_proc = None
    scorch_jar = settings.BASE_DIR / "static" / "scorch" / "ScorchServer.jar"
    if scorch_jar.exists() and shutil.which("java"):
        try:
            scorch_proc = subprocess.Popen(
                ["java", "-cp", str(scorch_jar), "scorchserver.ScorchServer"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print(f"Scorch 2000 game server started (PID {scorch_proc.pid}, port 4242)")
        except Exception as e:
            print(f"WARNING: Could not start Scorch server: {e}")
    elif scorch_jar.exists():
        print("Scorch server JAR found but java not on PATH — skipping.")

    yield
    # Shutdown
    if scorch_proc is not None:
        scorch_proc.terminate()
        try:
            scorch_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            scorch_proc.kill()
        print("Scorch 2000 game server stopped.")
    if crossword_scheduler_task is not None:
        crossword_scheduler_task.cancel()
        try:
            await crossword_scheduler_task
        except asyncio.CancelledError:
            pass
    print("Shutting down...")

app = FastAPI(
    title="Federated Local Internet",
    description="Family-safe, algorithm-free local internet node.",
    version="0.1.0",
    lifespan=lifespan
)


@app.middleware("http")
async def module_auth_guard(request: Request, call_next):
    path = request.url.path
    if path == "/wikipedia" or path.startswith("/wikipedia/"):
        detail = "Wikipedia is temporarily disabled while we investigate a stability issue."
        accepts_html = "text/html" in (request.headers.get("accept", "") or "").lower()
        if request.method.upper() == "GET" and accepts_html:
            html = (
                "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"UTF-8\">"
                "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">"
                f"<title>Wikipedia Disabled | {settings.NODE_NAME}</title>"
                "<style>"
                "body{font-family:'Inter',system-ui,-apple-system,sans-serif;background:#f8fafc;color:#1e293b;"
                "display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0;padding:24px;}"
                ".card{background:#fff;border:1px solid #e2e8f0;border-radius:16px;padding:32px;max-width:520px;"
                "box-shadow:0 10px 15px -3px rgb(0 0 0/.1);text-align:center;}"
                "h1{margin:0 0 10px;font-size:1.4rem;}p{margin:0;color:#64748b;line-height:1.6;}"
                "</style></head><body><div class=\"card\">"
                "<h1>Wikipedia Is Temporarily Disabled</h1>"
                f"<p>{detail}</p>"
                "</div></body></html>"
            )
            return HTMLResponse(content=html, status_code=503)
        return JSONResponse(status_code=503, content={"detail": detail})

    secure_mode_enabled, secure_domain = await _get_secure_mode_redirect_config()
    if secure_mode_enabled and secure_domain:
        accepts = (request.headers.get("accept", "") or "").lower()
        host = (request.url.hostname or "").strip().lower()
        scheme = (request.url.scheme or "").strip().lower()
        if (
            request.method.upper() in {"GET", "HEAD"}
            and "text/html" in accepts
            and host
            and _is_private_or_local_host(host)
        ):
            if scheme != "https":
                https_port = (os.getenv("APP_HTTPS_PORT", "8443") or "8443").strip()
                domain_is_resolvable = _is_resolvable_hostname_cached(secure_domain)
                target_base = secure_domain if domain_is_resolvable else host
                target_host = target_base if https_port in {"", "443"} else f"{target_base}:{https_port}"
                redirect_target = f"https://{target_host}{path}"
                if request.url.query:
                    redirect_target = f"{redirect_target}?{request.url.query}"
                return RedirectResponse(url=redirect_target, status_code=307)

    needs_auth = any(path == p or path.startswith(f"{p}/") for p in MODULE_PATH_PREFIXES)
    if not needs_auth:
        return await call_next(request)

    try:
        async with database.AsyncSessionLocal() as db:
            await auth.get_current_user(request, db)
    except HTTPException:
        accepts_html = "text/html" in (request.headers.get("accept", "").lower())
        if request.method.upper() == "GET" and accepts_html:
            return RedirectResponse(url="/auth/login", status_code=303)
        return JSONResponse(status_code=401, content={"detail": "Authentication required"})

    return await call_next(request)


# ── Friendly 401 page for browser requests ───────────────────────────────
from starlette.exceptions import HTTPException as StarletteHTTPException

@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 401:
        accepts_html = "text/html" in (request.headers.get("accept", "") or "").lower()
        if accepts_html:
            html = (
                '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
                f'<title>Not Signed In | {settings.NODE_NAME}</title>'
                '<style>'
                'body{font-family:"Inter",system-ui,-apple-system,sans-serif;background:#f8fafc;color:#1e293b;'
                'display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0;}'
                '.card{background:#fff;border:1px solid #e2e8f0;border-radius:16px;padding:40px 48px;'
                'max-width:440px;text-align:center;box-shadow:0 10px 15px -3px rgb(0 0 0/.1);}'
                'h1{font-size:1.5rem;margin-bottom:8px;}'
                'p{color:#64748b;font-size:.95rem;margin-bottom:24px;line-height:1.6;}'
                '.actions{display:flex;gap:12px;justify-content:center;flex-wrap:wrap;}'
                'a{display:inline-block;padding:10px 24px;border-radius:10px;text-decoration:none;font-weight:600;font-size:.9rem;}'
                '.btn-primary{background:#4f46e5;color:#fff;}'
                '.btn-primary:hover{background:#4338ca;}'
                '.btn-outline{border:1px solid #e2e8f0;color:#1e293b;}'
                '.btn-outline:hover{background:#f1f5f9;}'
                '</style></head><body>'
                '<div class="card">'
                '<h1>You aren\'t signed in</h1>'
                f'<p>You need to sign in to access this page on {settings.NODE_NAME}.</p>'
                '<div class="actions">'
                '<a href="/auth/login" class="btn-primary">Sign In</a>'
                '<a href="/" class="btn-outline">Back to Home</a>'
                '</div></div></body></html>'
            )
            return HTMLResponse(content=html, status_code=401)
    # For all other HTTP exceptions, return the default JSON response
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


# Include Routers — core (always)
app.include_router(setup.router)
app.include_router(auth.router)
app.include_router(posts.router)
app.include_router(media.router)
app.include_router(community.router)
app.include_router(users.router)
app.include_router(admin.router)
app.include_router(federation.router)

# Include Routers — optional (only if enabled)
if calendar:    app.include_router(calendar.router)
if games:       app.include_router(games.router)
if wikipedia:   app.include_router(wikipedia.router)
if maps:        app.include_router(maps.router)
if family_photos: app.include_router(family_photos.router)
if discussions: app.include_router(discussions.router)
if voice:       app.include_router(voice.router)
if mail:        app.include_router(mail.router)
if chat:        app.include_router(chat.router)
if chat_ex:     app.include_router(chat_ex.router)
if create:      app.include_router(create.router)


@app.post("/channels/{channel_id}/topic/update")
async def update_channel_topic_compat(
    channel_id: int,
    topic: str = Form(""),
    voice_channel_id: Optional[int] = Form(None),
    current_user: models.User = Depends(auth.get_current_user),
    db: AsyncSession = Depends(database.get_db),
):
    """
    Compatibility alias for channel topic updates.
    Accepts legacy relative form actions that post to /channels/... instead of /chat/channels/...
    """
    return await chat.update_channel_topic(
        channel_id=channel_id,
        topic=topic,
        voice_channel_id=voice_channel_id,
        current_user=current_user,
        db=db,
    )

# Mount static assets and media directories
app.mount("/static", StaticFiles(directory=str(settings.BASE_DIR / "static")), name="static")
app.mount("/media", StaticFiles(directory=settings.MEDIA_ROOT), name="media")

@app.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    current_user: Optional[models.User] = Depends(auth.get_current_user_optional)
):
    from fastapi.templating import Jinja2Templates
    from . import server_utils
    from .database import AsyncSessionLocal
    
    templates = Jinja2Templates(directory=str(settings.BASE_DIR / "templates"))
    
    async with AsyncSessionLocal() as db:
        # Redirect to setup wizard if no admin exists yet
        if not await setup._has_admin(db):
            return RedirectResponse(url="/setup", status_code=303)

        # Fetch active modules
        active_modules = await server_utils.get_active_modules(db)
        visible_module_bar_modules = server_utils.filter_visible_module_bar_modules(
            active_modules,
            current_user.module_bar_config if current_user else None,
        )

    return templates.TemplateResponse(
        request=request, 
        name="home.html", 
        context={
            "node_name": settings.NODE_NAME, 
            "platform_name": settings.PLATFORM_NAME,
            "user": current_user,
            "modules": active_modules,
            "active_modules": active_modules,
            "visible_module_bar_modules": visible_module_bar_modules,
        }
    )


@app.get("/connect-server", response_class=HTMLResponse)
async def connect_server_page(
    request: Request,
    current_user: Optional[models.User] = Depends(auth.get_current_user_optional),
):
    from fastapi.templating import Jinja2Templates
    from . import server_utils
    from .database import AsyncSessionLocal

    templates = Jinja2Templates(directory=str(settings.BASE_DIR / "templates"))

    async with AsyncSessionLocal() as db:
        active_modules = await server_utils.get_active_modules(db)
        visible_module_bar_modules = server_utils.filter_visible_module_bar_modules(
            active_modules,
            current_user.module_bar_config if current_user else None,
        )

    return templates.TemplateResponse(
        request=request,
        name="connect_server.html",
        context={
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
            "user": current_user,
            "modules": active_modules,
            "active_modules": active_modules,
            "visible_module_bar_modules": visible_module_bar_modules,
        },
    )


@app.get("/about", response_class=HTMLResponse)
async def about_page(request: Request):
    from fastapi.templating import Jinja2Templates

    templates = Jinja2Templates(directory=str(settings.BASE_DIR / "templates"))
    return templates.TemplateResponse(
        request=request,
        name="about.html",
        context={
            "platform_name": settings.PLATFORM_NAME,
        },
    )


@app.get("/.well-known/node-info")
async def node_info():
    return {
        "node_name": settings.NODE_NAME,
        "version": "0.1.0",
        "federation_enabled": settings.FEDERATION_ENABLED,
        "server_id": server_identity.get_or_create_server_id(),
    }


@app.get("/.well-known/server-id")
async def server_id_info():
    return {"server_id": server_identity.get_or_create_server_id()}


@app.get("/.well-known/local-ca.crt")
async def local_ca_certificate():
    ca_path = server_utils.local_ca_cert_path()
    if not ca_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Local CA certificate not found yet. Start the Podman HTTPS stack once, then retry.",
        )
    return FileResponse(
        path=str(ca_path),
        media_type="application/x-x509-ca-cert",
        filename="house-fantastico-local-ca.crt",
    )


@app.get("/.well-known/connect-profile")
async def connect_profile():
    async with database.AsyncSessionLocal() as db:
        server_settings = await server_utils.get_server_settings(db)

    domain = server_utils.normalize_secure_local_domain(server_settings.secure_local_domain)
    ip = server_utils.normalize_secure_local_ip(server_settings.secure_local_ip)
    http_port = (os.getenv("APP_HTTP_PORT", "8001") or "8001").strip()
    https_port = (os.getenv("APP_HTTPS_PORT", "8443") or "8443").strip()
    server_id = server_identity.get_or_create_server_id()
    secure_url = f"https://{domain}:{https_port}" if domain else None
    first_run_http = f"http://{ip}:{http_port}" if ip else None
    ca_url = f"{secure_url}/.well-known/local-ca.crt" if secure_url else None

    return {
        "server_id": server_id,
        "server_name": server_settings.server_name,
        "secure_mode_enabled": bool(server_settings.secure_mode_enabled),
        "secure_local_domain": domain,
        "secure_local_ip": ip,
        "first_run_http_url": first_run_http,
        "secure_url": secure_url,
        "external_server_url": getattr(server_settings, "external_server_url", None) or None,
        "ca_certificate_url": ca_url,
        "hosts_entry": f"{ip} {domain}" if ip and domain else None,
    }


@app.post("/.well-known/handshake")
async def handshake(payload: HandshakeRequest):
    local_id = server_identity.get_or_create_server_id()
    target_id = (payload.target_server_id or "").strip().lower()
    peer_id = (payload.peer_server_id or "").strip().lower()
    if not target_id or not peer_id:
        return {"ok": False, "detail": "target_server_id and peer_server_id are required"}
    if target_id != local_id:
        return {"ok": False, "detail": "target_server_id does not match this server"}

    policy = await _get_external_join_policy()
    if policy == "none":
        return {
            "ok": False,
            "detail": "External connections are disabled by admin policy",
            "policy": policy,
        }

    # Accept a pre-hashed password from the client so no plaintext ever travels.
    supplied_hash = (payload.password_hash or "").strip() or None
    if supplied_hash and not supplied_hash.startswith("$argon2"):
        return {"ok": False, "detail": "password_hash must be an argon2 hash string"}

    req = server_models.JoinRequest(
        target_server_id=local_id,
        peer_server_id=peer_id,
        requested_username=(payload.requested_username or "").strip() or None,
        requested_display_name=(payload.requested_display_name or "").strip() or None,
        note=(payload.note or "").strip() or None,
        status="approved" if policy == "all" else "pending",
        allocated_password=supplied_hash,
    )

    try:
        async with database.AsyncSessionLocal() as db:
            db.add(req)
            await db.commit()
            await db.refresh(req)
    except OperationalError:
        # Keep handshake endpoint stable during first upgrade before table exists.
        return {
            "ok": False,
            "detail": "join_requests table is unavailable; restart server to initialize schema",
        }

    # Handshake identity keying: both parties identify by persistent server_id.
    return {
        "ok": True,
        "request_id": req.id,
        "status": req.status,
        "policy": policy,
        "requires_admin_approval": req.status == "pending",
        "server_id": local_id,
        "peer_server_id": peer_id,
        "nonce": payload.nonce,
    }


@app.post("/api/request-join")
async def web_request_join(payload: WebJoinRequest):
    """
    Web-based join request — submitted from the home page by unauthenticated visitors.
    Creates a JoinRequest with a hashed password so the admin can approve
    and the user can then register with those credentials.
    """
    from . import crud_users
    from .auth_utils import get_password_hash

    try:
        username = (payload.username or "").strip().lower()
        display_name = (payload.display_name or "").strip() or None
        password = (payload.password or "").strip()

        if not username or len(username) < 3:
            return JSONResponse({"ok": False, "detail": "Username must be at least 3 characters."}, status_code=400)
        if not password or len(password) < 6:
            return JSONResponse({"ok": False, "detail": "Password must be at least 6 characters."}, status_code=400)

        local_id = server_identity.get_or_create_server_id()
        policy = await _get_external_join_policy()
        if policy == "none":
            return JSONResponse({"ok": False, "detail": "Join requests are disabled by admin."}, status_code=403)

        # Check if username already exists
        async with database.AsyncSessionLocal() as db:
            existing = await crud_users.get_user_by_username(db, username=username)
            if existing:
                return JSONResponse({"ok": False, "detail": "That username is already taken."}, status_code=409)

            # Check for duplicate pending request
            dup = await db.execute(
                select(server_models.JoinRequest).where(
                    server_models.JoinRequest.requested_username == username,
                    server_models.JoinRequest.target_server_id == local_id,
                    server_models.JoinRequest.status == "pending",
                )
            )
            if dup.scalar_one_or_none():
                return JSONResponse({"ok": False, "detail": "A request for that username is already pending."}, status_code=409)

            # Hash the password using the same system as auth (passlib argon2)
            pw_hash = get_password_hash(password)

            req = server_models.JoinRequest(
                target_server_id=local_id,
                peer_server_id="web-direct",
                requested_username=username,
                requested_display_name=display_name,
                note="Submitted from the web join form",
                status="approved" if policy == "all" else "pending",
                allocated_password=pw_hash,
            )
            db.add(req)
            await db.commit()
            await db.refresh(req)

        if req.status == "approved":
            return {"ok": True, "status": "approved", "detail": "Your request has been approved! You can now log in with your credentials."}
        return {"ok": True, "status": "pending", "detail": "Your request has been submitted. An admin will review it shortly."}

    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"ok": False, "detail": "An internal error occurred. Please try again later."}, status_code=500)


@app.get("/.well-known/join-requests/{request_id}")
async def handshake_status(request_id: int, peer_server_id: str):
    try:
        async with database.AsyncSessionLocal() as db:
            result = await db.execute(
                select(server_models.JoinRequest).where(server_models.JoinRequest.id == request_id)
            )
            req = result.scalar_one_or_none()
            if not req:
                raise HTTPException(status_code=404, detail="Join request not found")
            if (req.peer_server_id or "").strip().lower() != (peer_server_id or "").strip().lower():
                raise HTTPException(status_code=403, detail="Peer server ID mismatch")
            return {
                "request_id": req.id,
                "status": req.status,
                "target_server_id": req.target_server_id,
                "peer_server_id": req.peer_server_id,
                "requested_username": req.requested_username,
                "allocated_username": req.requested_username if req.status == "approved" else None,
                "has_password": bool(req.allocated_password),
                "created_at": str(req.created_at) if req.created_at else None,
                "updated_at": str(req.updated_at) if req.updated_at else None,
            }
    except HTTPException:
        raise
    except Exception as e:
        import logging as _log; _log.getLogger(__name__).exception("Status lookup failed")
        raise HTTPException(status_code=500, detail="Status lookup failed. Please try again later.")


@app.post("/.well-known/password-reset-request")
async def password_reset_request(payload: PasswordResetPayload):
    """
    Public endpoint for clients to submit a password reset request.
    The new password hash is stored pending admin approval.
    """
    username = (payload.username or "").strip()
    peer_id = (payload.peer_server_id or "").strip().lower()
    new_hash = (payload.new_password_hash or "").strip()

    if not username:
        return {"ok": False, "detail": "Username is required."}
    if not peer_id:
        return {"ok": False, "detail": "peer_server_id is required."}
    if not new_hash.startswith("$argon2"):
        return {"ok": False, "detail": "new_password_hash must be an argon2 hash string."}

    # Verify the username exists
    try:
        async with database.AsyncSessionLocal() as db:
            result = await db.execute(
                select(models.User).where(models.User.username == username)
            )
            user = result.scalar_one_or_none()
            if not user:
                return {"ok": False, "detail": "No account found with that username."}

            req = server_models.PasswordResetRequest(
                username=username,
                peer_server_id=peer_id,
                new_password_hash=new_hash,
                status="pending",
            )
            db.add(req)
            await db.commit()
            await db.refresh(req)

            return {
                "ok": True,
                "request_id": req.id,
                "status": req.status,
                "detail": "Password reset request submitted. Awaiting admin approval.",
            }
    except Exception as e:
        return {"ok": False, "detail": "Failed to submit reset request. Please try again later."}


@app.get("/.well-known/password-reset-requests/{request_id}")
async def password_reset_status(request_id: int, peer_server_id: str):
    """Check the status of a password reset request."""
    try:
        async with database.AsyncSessionLocal() as db:
            result = await db.execute(
                select(server_models.PasswordResetRequest).where(
                    server_models.PasswordResetRequest.id == request_id
                )
            )
            req = result.scalar_one_or_none()
            if not req:
                raise HTTPException(status_code=404, detail="Reset request not found")
            if (req.peer_server_id or "").strip().lower() != (peer_server_id or "").strip().lower():
                raise HTTPException(status_code=403, detail="Peer server ID mismatch")
            return {
                "request_id": req.id,
                "status": req.status,
                "username": req.username,
                "created_at": str(req.created_at) if req.created_at else None,
            }
    except HTTPException:
        raise
    except Exception as e:
        import logging as _log; _log.getLogger(__name__).exception("Password reset status lookup failed")
        raise HTTPException(status_code=500, detail="Status lookup failed. Please try again later.")
