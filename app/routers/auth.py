from fastapi import APIRouter, Depends, HTTPException, status, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import and_, select
from typing import Annotated, Optional
from datetime import timedelta
from sqlalchemy.sql import func

from .. import schemas, crud_users, auth_utils, database, models, server_models, server_identity, server_utils
from ..config import settings

router = APIRouter(
    prefix="/auth",
    tags=["auth"]
)

templates = Jinja2Templates(directory=str(settings.BASE_DIR / "templates"))

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token")


async def _consume_approved_join_request(db: AsyncSession, username: str) -> bool:
    local_server_id = server_identity.get_or_create_server_id()
    settings_row = await server_utils.get_server_settings(db)
    policy = (getattr(settings_row, "external_join_policy", None) or "conditional").strip().lower()
    if policy == "none":
        return False
    if policy == "all":
        return True

    result = await db.execute(
        select(server_models.JoinRequest)
        .where(
            and_(
                server_models.JoinRequest.target_server_id == local_server_id,
                server_models.JoinRequest.requested_username == username,
                server_models.JoinRequest.status == "approved",
                server_models.JoinRequest.consumed_at.is_(None),
            )
        )
        .order_by(server_models.JoinRequest.created_at.desc())
    )
    req = result.scalars().first()
    if not req:
        return False
    req.consumed_at = func.now()
    await db.commit()
    return True

async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(database.get_db)
):
    """
    Dependency to get the current authenticated user from token (Header or Cookie).
    """
    # 1. Try Authorization Header
    auth_header = request.headers.get("Authorization")
    token = None
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]
    
    # 2. Try Cookie if no header
    if not token:
        cookie_token = request.cookies.get("access_token")
        if cookie_token and cookie_token.startswith("Bearer "):
            token = cookie_token[7:]

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = auth_utils.jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except auth_utils.JWTError:
        raise credentials_exception
        
    user = await crud_users.get_user_by_username(db, username=username)
    if user is None:
        raise credentials_exception
    return user

async def get_current_user_optional(
    request: Request,
    db: AsyncSession = Depends(database.get_db)
):
    """
    Optional version of get_current_user that returns None instead of raising.
    """
    try:
        return await get_current_user(request, db)
    except HTTPException:
        return None

@router.get("/login", response_class=HTMLResponse)
async def show_login_page(
    request: Request,
    current_user: Optional[models.User] = Depends(get_current_user_optional)
):
    """
    Renders the login page.
    """
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
            "user": current_user
        }
    )

@router.get("/register", response_class=HTMLResponse)
async def show_registration_page(
    request: Request,
    current_user: Optional[models.User] = Depends(get_current_user_optional)
):
    """
    Renders the registration page.
    """
    return templates.TemplateResponse(
        request=request,
        name="register.html",
        context={
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
            "user": current_user
        }
    )

@router.post("/register/submit")
async def register_submit(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    display_name: Annotated[Optional[str], Form()] = None,
    db: AsyncSession = Depends(database.get_db)
):
    """
    Handles registration form submission.
    """
    existing_user = await crud_users.get_user_by_username(db, username=username)
    if existing_user:
        # In a real app, we'd return a flash message. For now, just an error.
        raise HTTPException(status_code=400, detail="Username already exists")

    approved = await _consume_approved_join_request(db, username)
    if not approved:
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context={
                "node_name": settings.NODE_NAME,
                "platform_name": settings.PLATFORM_NAME,
                "user": None,
                "error": "Registration requires admin approval. Ask an admin to approve your join request first."
            },
            status_code=403,
        )
    
    user_in = schemas.UserCreate(
        username=username,
        password=password,
        display_name=display_name
    )
    
    new_user = await crud_users.create_user(db=db, user=user_in)
    
    # Auto-login: create JWT and set cookie so the user lands on /feed authenticated
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = auth_utils.create_access_token(
        data={"sub": new_user.username}, expires_delta=access_token_expires
    )
    response = RedirectResponse(url="/feed", status_code=303)
    response.set_cookie(
        key="access_token",
        value=f"Bearer {access_token}",
        httponly=True,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite="lax",
        secure=(settings.PROTOCOL == "https")
    )
    return response

@router.post("/register", response_model=schemas.UserResponse)
async def register(user: schemas.UserCreate, db: AsyncSession = Depends(database.get_db)):
    """
    Register a new user.
    """
    db_user = await crud_users.get_user_by_username(db, username=user.username)
    if db_user:
        raise HTTPException(status_code=400, detail="Username already registered")

    approved = await _consume_approved_join_request(db, user.username)
    if not approved:
        raise HTTPException(status_code=403, detail="Registration requires admin approval")
    
    return await crud_users.create_user(db=db, user=user)

@router.post("/token", response_model=schemas.Token)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: AsyncSession = Depends(database.get_db)
):
    """
    OAuth2 compatible token login, retrieve an access token for future requests.
    """
    user = await crud_users.get_user_by_username(db, username=form_data.username)
    if not user or not auth_utils.verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = auth_utils.create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

@router.post("/login")
async def login_submit(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    db: AsyncSession = Depends(database.get_db)
):
    """
    Handles login form submission, sets cookie, and redirects.
    """
    user = await crud_users.get_user_by_username(db, username=username)
    if not user or not auth_utils.verify_password(password, user.password_hash):
        # Redisplay login with error
        return templates.TemplateResponse(
            request=request,
            name="login.html", 
            context={
                "node_name": settings.NODE_NAME, 
                "platform_name": settings.PLATFORM_NAME,
                "user": None,
                "error": "Invalid credentials"
            }
        )
    
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = auth_utils.create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    
    response = RedirectResponse(url="/feed", status_code=303)
    response.set_cookie(
        key="access_token", 
        value=f"Bearer {access_token}", 
        httponly=True,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite="lax",
        secure=(settings.PROTOCOL == "https")
    )
    return response

@router.get("/me", response_model=schemas.UserResponse)
async def read_users_me(
    current_user: Annotated[models.User, Depends(get_current_user)]
):
    """
    Get current logged in user details.
    """
    return current_user


@router.post("/logout")
async def logout():
    """
    Clear auth cookie and return to login page.
    """
    response = RedirectResponse(url="/auth/login", status_code=303)
    response.delete_cookie(
        key="access_token",
        httponly=True,
        samesite="lax",
        secure=(settings.PROTOCOL == "https"),
    )
    return response
