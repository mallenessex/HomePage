from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.sql import func
from .models import Base

class ServerSettings(Base):
    """
    Server-wide configuration settings.
    Only one row should exist in this table.
    """
    __tablename__ = "server_settings"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # Server Identity
    server_name = Column(String, default="Family Node")
    server_description = Column(Text, nullable=True)
    
    # Content Policy
    forbidden_tags = Column(Text, default="")  # Comma-separated list
    warning_tags = Column(Text, default="")    # Comma-separated list
    
    # Federation Settings
    federation_enabled = Column(Integer, default=1)  # SQLite uses 1/0 for boolean
    allow_external_follows = Column(Integer, default=0)
    external_join_policy = Column(String, default="conditional")  # conditional, none, all
    secure_mode_enabled = Column(Integer, default=0)  # 0=off, 1=on
    secure_local_domain = Column(String, nullable=True)  # e.g. house-fantastico.local
    secure_local_ip = Column(String, nullable=True)  # LAN IP used for hosts/profile hints
    external_server_url = Column(String, nullable=True)  # Public URL for off-network client access
    
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

class ContentTag(Base):
    """
    Content tags for categorization and filtering.
    """
    __tablename__ = "content_tags"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False, index=True)
    category = Column(String, default="general")  # general, forbidden, warning
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class PolicyViolation(Base):
    """
    Log of content policy violations for tracking and reporting.
    """
    __tablename__ = "policy_violations"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    violation_type = Column(String, nullable=False)  # forbidden, warning
    matched_tag = Column(String, nullable=False)  # The tag that was matched
    content_preview = Column(Text, nullable=True)  # First 200 chars of content
    action_taken = Column(String, nullable=False)  # blocked, flagged
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class PlatformModule(Base):
    """
    Modular components of the platform (e.g. Wikipedia, Games, Feed).
    """
    __tablename__ = "platform_modules"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False) # Internal ID: wikipedia, games, feed, etc.
    display_name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    icon = Column(String, nullable=True)
    color = Column(String, default="#4f46e5")
    
    is_enabled = Column(Integer, default=1) # 1 = enabled, 0 = disabled
    config_json = Column(Text, default="{}") # Module-specific settings
    
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


class JoinRequest(Base):
    """
    Pending/approved enrollment request from a remote client.
    Registration is blocked until request is approved by an admin.
    """
    __tablename__ = "join_requests"

    id = Column(Integer, primary_key=True, index=True)
    target_server_id = Column(String, nullable=False, index=True)
    peer_server_id = Column(String, nullable=False, index=True)
    requested_username = Column(String, nullable=True, index=True)
    requested_display_name = Column(String, nullable=True)
    note = Column(Text, nullable=True)
    status = Column(String, default="pending", index=True)  # pending, approved, rejected
    approved_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    allocated_password = Column(String, nullable=True)  # argon2 hash supplied by client at handshake
    consumed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


class PasswordResetRequest(Base):
    """
    Pending password reset request submitted from a client.
    Admin must approve before the user's password_hash is updated.
    """
    __tablename__ = "password_reset_requests"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, nullable=False, index=True)
    peer_server_id = Column(String, nullable=False, index=True)
    new_password_hash = Column(String, nullable=False)  # argon2 hash
    status = Column(String, default="pending", index=True)  # pending, approved, rejected
    approved_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
