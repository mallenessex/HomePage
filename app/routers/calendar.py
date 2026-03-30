from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Annotated, Optional
import os
from datetime import datetime, timedelta
from calendar import isleap
from types import SimpleNamespace

from .. import schemas, database, models, auth_utils, server_utils
from . import auth
from ..config import settings
import google_auth_oauthlib.flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
import json

router = APIRouter(
    prefix="/calendar",
    tags=["calendar"]
)

templates = Jinja2Templates(directory=str(settings.BASE_DIR / "templates"))

@router.get("/", response_class=HTMLResponse)
async def view_calendar(
    request: Request,
    db: AsyncSession = Depends(database.get_db),
    current_user: Optional[models.User] = Depends(auth.get_current_user_optional)
):
    if not current_user:
        return RedirectResponse(url="/auth/login", status_code=303)
        
    # Get events for a reasonable range
    now = datetime.now()
    start_range = now.replace(day=1, hour=0, minute=0, second=0) - timedelta(days=7)
    end_range = start_range + timedelta(days=60)
    
    result = await db.execute(
        select(models.CalendarEvent)
        .where(models.CalendarEvent.start_time >= start_range)
        .where(models.CalendarEvent.start_time <= end_range)
        .order_by(models.CalendarEvent.start_time)
    )
    events = result.scalars().all()

    # Add synthetic annual birthday events from user profile birthdates.
    users_res = await db.execute(
        select(models.User).where(models.User.birthdate.is_not(None))
    )
    users_with_birthdays = users_res.scalars().all()

    for profile_user in users_with_birthdays:
        if not profile_user.birthdate:
            continue
        month = profile_user.birthdate.month
        day = profile_user.birthdate.day
        for year in range(start_range.year - 1, end_range.year + 2):
            target_day = day
            if month == 2 and day == 29 and not isleap(year):
                target_day = 28
            start_dt = datetime(year, month, target_day, 0, 0, 0)
            end_dt = start_dt + timedelta(hours=1)
            if start_dt < start_range or start_dt > end_range:
                continue
            events.append(
                SimpleNamespace(
                    id=(profile_user.id * 1000000) + (year * 10) + 9,
                    title=f"{profile_user.display_name or profile_user.username}'s Birthday",
                    start_time=start_dt,
                    end_time=end_dt,
                    location="Birthday",
                    is_external=False,
                )
            )

    events.sort(key=lambda e: e.start_time)
    
    # Check for external sync options
    sync_options = []
    if settings.GOOGLE_CLIENT_ID:
        sync_options.append("google")
    if settings.MICROSOFT_CLIENT_ID:
        sync_options.append("outlook")
        
    active_modules = await server_utils.get_active_modules(db)
    
    return templates.TemplateResponse(
        request=request,
        name="calendar.html",
        context={
            "events": events,
            "user": current_user,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
            "sync_options": sync_options,
            "active_modules": active_modules
        }
    )


@router.post("/events/submit")
async def create_event(
    title: Annotated[str, Form()],
    start_time: Annotated[str, Form()],
    end_time: Annotated[str, Form()],
    description: Annotated[Optional[str], Form()] = None,
    location: Annotated[Optional[str], Form()] = None,
    db: AsyncSession = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    # Parse times (HTML5 datetime-local gives YYYY-MM-DDTHH:MM)
    try:
        start_dt = datetime.fromisoformat(start_time)
        end_dt = datetime.fromisoformat(end_time)
    except ValueError:
        # Fallback for some browsers
        try:
             start_dt = datetime.strptime(start_time, "%Y-%m-%dT%H:%M")
             end_dt = datetime.strptime(end_time, "%Y-%m-%dT%H:%M")
        except:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DDTHH:MM")
        
    new_event = models.CalendarEvent(
        title=title,
        description=description,
        start_time=start_dt,
        end_time=end_dt,
        location=location,
        user_id=current_user.id
    )
    
    db.add(new_event)
    await db.commit()
    
    return RedirectResponse(url="/calendar", status_code=303)

@router.get("/sync/google")
async def sync_google_placeholder(request: Request, current_user: models.User = Depends(auth.get_current_user), db: AsyncSession = Depends(database.get_db)):
    active_modules = await server_utils.get_active_modules(db)
    return templates.TemplateResponse(
        request=request,
        name="calendar_sync_info.html",
        context={"provider": "Google", "user": current_user, "node_name": settings.NODE_NAME, "active_modules": active_modules}
    )

@router.get("/sync/google/start")
async def sync_google_start(request: Request, current_user: models.User = Depends(auth.get_current_user)):
    # ── Privacy gate: Google Calendar sync contacts external Google servers ──
    if not current_user.is_admin:
        raise HTTPException(
            status_code=403,
            detail="Google Calendar sync is admin-only because it connects to external Google servers.",
        )
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise HTTPException(status_code=400, detail="Google credentials not configured in .env")
        
    client_config = {
        "web": {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uris": [f"{request.base_url}calendar/sync/google/callback"],
        }
    }
    
    flow = google_auth_oauthlib.flow.Flow.from_client_config(
        client_config,
        scopes=['https://www.googleapis.com/auth/calendar.readonly']
    )
    
    # Use localhost or IP depending on how user is accessing
    flow.redirect_uri = f"{request.base_url}calendar/sync/google/callback"
    
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )
    
    # Store state for verification in callback (could use session/cookie)
    # For now we'll just redirect
    return RedirectResponse(url=authorization_url)

@router.get("/sync/google/callback")
async def sync_google_callback(
    request: Request,
    code: str,
    db: AsyncSession = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    client_config = {
        "web": {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "project_id": "family-homepage",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uris": [f"{request.base_url}calendar/sync/google/callback"],
        }
    }
    
    flow = google_auth_oauthlib.flow.Flow.from_client_config(client_config, scopes=['https://www.googleapis.com/auth/calendar.readonly'])
    flow.redirect_uri = f"{request.base_url}calendar/sync/google/callback"
    
    flow.fetch_token(code=code)
    credentials = flow.credentials
    
    # Save tokens to database
    # Check if account already exists
    result = await db.execute(
        select(models.ExternalAccount)
        .where(models.ExternalAccount.user_id == current_user.id)
        .where(models.ExternalAccount.provider == "google")
    )
    account = result.scalars().first()
    
    if not account:
        account = models.ExternalAccount(
            user_id=current_user.id,
            provider="google"
        )
        db.add(account)
    
    account.access_token = credentials.token
    account.refresh_token = credentials.refresh_token
    account.token_uri = credentials.token_uri
    account.client_id = credentials.client_id
    account.client_secret = credentials.client_secret
    account.expires_at = credentials.expiry
    
    await db.commit()
    
    # Run initial sync
    await sync_google_events(account, db, current_user.id)
    
    return RedirectResponse(url="/calendar")

async def sync_google_events(account: models.ExternalAccount, db: AsyncSession, user_id: int):
    creds = Credentials(
        token=account.access_token,
        refresh_token=account.refresh_token,
        token_uri=account.token_uri,
        client_id=account.client_id,
        client_secret=account.client_secret
    )
    
    try:
        service = build('calendar', 'v3', credentials=creds)
        
        # Get events from today onwards
        now = datetime.utcnow().isoformat() + 'Z'
        events_result = service.events().list(
            calendarId='primary', 
            timeMin=now,
            maxResults=50, 
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        google_events = events_result.get('items', [])
        
        for g_event in google_events:
            start = g_event['start'].get('dateTime', g_event['start'].get('date'))
            end = g_event['end'].get('dateTime', g_event['end'].get('date'))
            
            # Convert to datetime objects
            # Google gives 2026-02-12T10:00:00Z or 2026-02-12
            start_dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
            end_dt = datetime.fromisoformat(end.replace('Z', '+00:00'))
            
            # Use external_id to avoid duplicates
            external_id = g_event['id']
            
            result = await db.execute(
                select(models.CalendarEvent).where(models.CalendarEvent.external_id == external_id)
            )
            existing = result.scalars().first()
            
            if not existing:
                new_event = models.CalendarEvent(
                    title=g_event.get('summary', 'Untitled Event'),
                    description=g_event.get('description'),
                    location=g_event.get('location'),
                    start_time=start_dt,
                    end_time=end_dt,
                    user_id=user_id,
                    is_external=True,
                    external_source='google',
                    external_id=external_id
                )
                db.add(new_event)
            else:
                existing.title = g_event.get('summary', 'Untitled Event')
                existing.description = g_event.get('description')
                existing.location = g_event.get('location')
                existing.start_time = start_dt
                existing.end_time = end_dt
                
        await db.commit()
    except Exception as e:
        print(f"Error syncing Google Calendar: {e}")

@router.get("/sync/outlook")
async def sync_outlook_placeholder(request: Request, current_user: models.User = Depends(auth.get_current_user), db: AsyncSession = Depends(database.get_db)):
    active_modules = await server_utils.get_active_modules(db)
    return templates.TemplateResponse(
        request=request,
        name="calendar_sync_info.html",
        context={"provider": "Outlook", "user": current_user, "node_name": settings.NODE_NAME, "active_modules": active_modules}
    )
