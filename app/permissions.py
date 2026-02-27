"""
Permission utilities for role-based access control.
"""
from fastapi import HTTPException, status
from typing import Optional
from . import models

class UserRole:
    """User role constants."""
    ADMIN = "admin"
    ADULT = "adult"
    PARENT = "parent"  # legacy value kept for backward compatibility
    CHILD = "child"


def adult_role_values() -> tuple[str, str]:
    """
    Role values treated as adult-equivalent.
    Includes legacy `parent` for existing records.
    """
    return (UserRole.ADULT, UserRole.PARENT)


def is_adult_role_value(role_value: Optional[str]) -> bool:
    role = (role_value or "").strip().lower()
    return role in adult_role_values()


def normalize_role_value(role_value: Optional[str]) -> str:
    """
    Normalize incoming role values to canonical values.
    - `parent` -> `adult`
    """
    role = (role_value or "").strip().lower()
    if role == UserRole.PARENT:
        return UserRole.ADULT
    return role


def is_adult(user: Optional[models.User]) -> bool:
    return bool(user) and is_adult_role_value(getattr(user, "role", None))

class ContentFilterLevel:
    """Content filter level constants."""
    STRICT = "strict"      # Blocks most content, very limited
    MODERATE = "moderate"  # Balanced filtering
    RELAXED = "relaxed"    # Minimal filtering

def require_admin(user: models.User):
    """
    Decorator/check function to ensure user is an admin.
    Raises HTTPException if not authorized.
    """
    if not user or user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return True

def require_parent_or_admin(user: models.User):
    """
    Ensure user is at least an adult (or admin).
    """
    if not user or (user.role != UserRole.ADMIN and not is_adult(user)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Adult or admin access required"
        )
    return True

def can_manage_user(manager: models.User, target_user: models.User) -> bool:
    """
    Check if manager can manage target_user.
    - Admins can manage anyone
    - Adults can manage their own children
    - Users can manage themselves
    """
    if manager.role == UserRole.ADMIN:
        return True
    
    if manager.id == target_user.id:
        return True
    
    if is_adult(manager) and target_user.parent_id == manager.id:
        return True

    # Multi-custody support: if relationship is available, allow listed custodians.
    try:
        if is_adult(manager) and any(c.id == manager.id for c in (target_user.custodians or [])):
            return True
    except Exception:
        pass
    
    return False

def require_can_manage(manager: models.User, target_user: models.User):
    """
    Raises exception if manager cannot manage target_user.
    """
    if not can_manage_user(manager, target_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to manage this user"
        )
    return True

def can_post(user: models.User) -> bool:
    """
    Check if user has permission to create posts.
    """
    if user.role == UserRole.ADMIN:
        return True
    
    if is_adult(user):
        return True
    
    # Child accounts check their policy
    return user.can_post

def can_follow_external_users(user: models.User) -> bool:
    """
    Check if user can follow users outside their family/node.
    """
    if user.role == UserRole.ADMIN or is_adult(user):
        return True
    
    return user.can_follow_external

def get_content_filter_level(user: models.User) -> str:
    """
    Get the content filter level for a user.
    """
    if user.role == UserRole.ADMIN or is_adult(user):
        return ContentFilterLevel.RELAXED
    
    return user.content_filter_level or ContentFilterLevel.MODERATE

def is_within_screen_time_limit(user: models.User, minutes_used_today: int) -> bool:
    """
    Check if user is within their daily screen time limit.
    Returns True if within limit or no limit set.
    """
    if user.role == UserRole.ADMIN or is_adult(user):
        return True
    
    if user.max_daily_screen_time is None:
        return True
    
    return minutes_used_today < user.max_daily_screen_time
