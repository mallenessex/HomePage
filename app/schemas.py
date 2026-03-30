from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, date

# Token Schemas
class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: Optional[str] = None

# User Schemas
class UserBase(BaseModel):
    username: str
    display_name: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None

class UserCreate(UserBase):
    password: str

class UserUpdate(BaseModel):
    display_name: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    custom_css: Optional[str] = None
    theme_preset: Optional[str] = None
    hidden_modules: Optional[List[str]] = None
    birthdate: Optional[date] = None

class UserResponse(UserBase):
    id: int
    public_key: str
    created_at: datetime
    is_admin: bool

    class Config:
        from_attributes = True

# Post Schemas
class PostBase(BaseModel):
    title: Optional[str] = None
    body: str
    media_path: Optional[str] = None
    media_type: Optional[str] = None
    include_in_family_photos: bool = False
    mood: Optional[str] = None
    visibility: str = "public"

class PostCreate(PostBase):
    pass

class PostResponse(PostBase):
    id: int
    author_id: int
    created_at: datetime
    signature: Optional[str] = None
    author: Optional[UserResponse] = None

    class Config:
        from_attributes = True

class Timeline(BaseModel):
    items: List[PostResponse]

# Calendar Schemas
class CalendarEventBase(BaseModel):
    title: str
    description: Optional[str] = None
    start_time: datetime
    end_time: datetime
    location: Optional[str] = None

class CalendarEventCreate(CalendarEventBase):
    pass

class CalendarEventResponse(CalendarEventBase):
    id: int
    user_id: int
    is_external: bool
    external_source: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True
