from sqlalchemy import Column, Integer, String, Boolean, DateTime, Date, ForeignKey, Text, Table, BigInteger, UniqueConstraint
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func
import datetime

Base = declarative_base()

child_custody = Table(
    "child_custody",
    Base.metadata,
    Column("child_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("parent_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    display_name = Column(String, nullable=True)
    bio = Column(Text, nullable=True)
    birthdate = Column(Date, nullable=True)
    avatar_url = Column(String, nullable=True)
    custom_css = Column(Text, nullable=True)
    theme_preset = Column(String, default="default")
    module_bar_config = Column(Text, nullable=True)
    password_hash = Column(String, nullable=False)
    
    # Identity (Ed25519 - for internal/future crypto)
    public_key = Column(String, unique=True, nullable=False)
    private_key_enc = Column(String, nullable=False)
    
    # Federation (ActivityPub standard RSA)
    rsa_public_key = Column(String, nullable=True) # PEM format
    rsa_private_key_enc = Column(String, nullable=True) # PEM format, encrypted
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    is_admin = Column(Boolean, default=False)
    
    # Role-based permissions
    role = Column(String, default="adult")  # admin, adult, child (legacy "parent" supported)
    parent_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # For child accounts
    
    # Policy settings (for child accounts, set by adult/admin custodians)
    content_filter_level = Column(String, default="moderate")  # strict, moderate, relaxed
    can_post = Column(Boolean, default=True)
    can_follow_external = Column(Boolean, default=False)  # Can follow users outside the family
    max_daily_screen_time = Column(Integer, nullable=True)  # Minutes, null = unlimited
    
    # Relationships
    posts = relationship("Post", back_populates="author")
    children = relationship("User", backref="parent", remote_side=[id])
    custody_children = relationship(
        "User",
        secondary=child_custody,
        primaryjoin=id == child_custody.c.parent_id,
        secondaryjoin=id == child_custody.c.child_id,
        back_populates="custodians",
    )
    custodians = relationship(
        "User",
        secondary=child_custody,
        primaryjoin=id == child_custody.c.child_id,
        secondaryjoin=id == child_custody.c.parent_id,
        back_populates="custody_children",
    )

class Post(Base):
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=True)
    body = Column(Text, nullable=False)
    
    media_path = Column(String, nullable=True)
    media_type = Column(String, nullable=True) # image/png, audio/mp3
    include_in_family_photos = Column(Boolean, default=False, nullable=False)
    
    # Mood/Music (optional fun fields)
    mood = Column(String, nullable=True)
    listening_to = Column(String, nullable=True)

    author_id = Column(Integer, ForeignKey("users.id"))
    author = relationship("User", back_populates="posts")
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Federation
    signature = Column(String, nullable=True) # Signed by author's key
    visibility = Column(String, default="public") # public, friends, private
    reactions = relationship("PostReaction", back_populates="post", cascade="all, delete-orphan")


class PostReaction(Base):
    __tablename__ = "post_reactions"

    id = Column(Integer, primary_key=True, index=True)
    post_id = Column(Integer, ForeignKey("posts.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    emoji = Column(String, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    post = relationship("Post", back_populates="reactions")
    user = relationship("User")

class Following(Base):
    """
    Remote users that a local user follows.
    """
    __tablename__ = "following"
    
    id = Column(Integer, primary_key=True, index=True)
    local_user_id = Column(Integer, ForeignKey("users.id"))
    
    remote_actor_id = Column(String, nullable=False, index=True)
    remote_inbox_url = Column(String, nullable=True)
    remote_username = Column(String, nullable=True)
    
    status = Column(String, default="pending") # pending, accepted, rejected
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Follower(Base):
    """
    Remote users who follow a local user.
    """
    __tablename__ = "followers"
    
    id = Column(Integer, primary_key=True, index=True)
    local_user_id = Column(Integer, ForeignKey("users.id"))
    
    remote_actor_id = Column(String, nullable=False, index=True) # Full URL of remote actor
    remote_inbox_url = Column(String, nullable=False) # For delivering updates
    remote_username = Column(String, nullable=True) # For display
    
    status = Column(String, default="pending") # pending, accepted
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class ActivityLog(Base):
    """
    Log of received activities for debugging/history.
    """
    __tablename__ = "activity_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    activity_type = Column(String, nullable=False)
    actor_id = Column(String, nullable=False)
    object_id = Column(String, nullable=True) # e.g. URL of post
    raw_json = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class CalendarEvent(Base):
    __tablename__ = "calendar_events"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    location = Column(String, nullable=True)
    
    user_id = Column(Integer, ForeignKey("users.id"))
    user = relationship("User")
    
    # External Sync
    is_external = Column(Boolean, default=False)
    external_source = Column(String, nullable=True) # google, outlook
    external_id = Column(String, nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class ExternalAccount(Base):
    __tablename__ = "external_accounts"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    provider = Column(String, nullable=False) # google, microsoft
    
    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text, nullable=True)
    token_uri = Column(String, nullable=True) # Required for some flows
    client_id = Column(String, nullable=True)
    client_secret = Column(String, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    
    user = relationship("User")

class GameLobby(Base):
    __tablename__ = "game_lobbies"

    id = Column(Integer, primary_key=True, index=True)
    server_name = Column(String, nullable=False)
    game_type = Column(String, nullable=False) # e.g. Scorched Earth
    play_mode = Column(String, nullable=False, default="multiplayer") # multiplayer, single_player
    genre = Column(String, nullable=False, default="general") # puzzle, board_strategy, etc.
    host_username = Column(String, nullable=False) # e.g. @neighbor_dave
    
    player_count = Column(Integer, default=1)
    max_players = Column(Integer, default=8)
    
    status = Column(String, default="online") # pending, online, closed
    join_url = Column(String, nullable=True) # e.g. /games/scorch or external IP
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    user_id = Column(Integer, ForeignKey("users.id"))
    user = relationship("User")

class ChessGame(Base):
    __tablename__ = "chess_games"

    id = Column(Integer, primary_key=True, index=True)
    white_player_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    black_player_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    
    fen = Column(String, default="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
    status = Column(String, default="active") # active, draw, white_win, black_win
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    white_player = relationship("User", foreign_keys=[white_player_id])
    black_player = relationship("User", foreign_keys=[black_player_id])


class CrosswordPuzzle(Base):
    __tablename__ = "crossword_puzzles"
    __table_args__ = (
        UniqueConstraint("publish_date", "edition", name="uq_crossword_puzzles_publish_date_edition"),
    )

    id = Column(Integer, primary_key=True, index=True)
    publish_date = Column(Date, nullable=False, index=True)
    edition = Column(String, nullable=False, default="daily")  # daily, sunday, special
    title = Column(String, nullable=False)
    source = Column(String, nullable=False, default="generated")  # generated, archive_local, archive_remote
    payload_json = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="published")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

class ForumCategory(Base):
    __tablename__ = "forum_categories"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    slug = Column(String, unique=True, index=True, nullable=False)
    order = Column(Integer, default=0)
    
    threads = relationship("ForumThread", back_populates="category")

class ForumThread(Base):
    __tablename__ = "forum_threads"
    
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    view_count = Column(Integer, default=0)
    
    is_pinned = Column(Boolean, default=False)
    is_locked = Column(Boolean, default=False)
    
    category_id = Column(Integer, ForeignKey("forum_categories.id"))
    user_id = Column(Integer, ForeignKey("users.id"))
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
    
    category = relationship("ForumCategory", back_populates="threads")
    author = relationship("User")
    posts = relationship("ForumPost", back_populates="thread", cascade="all, delete-orphan")

class ForumPost(Base):
    __tablename__ = "forum_posts"
    
    id = Column(Integer, primary_key=True, index=True)
    content = Column(Text, nullable=False)
    
    thread_id = Column(Integer, ForeignKey("forum_threads.id"))
    user_id = Column(Integer, ForeignKey("users.id"))
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
    
    thread = relationship("ForumThread", back_populates="posts")
    author = relationship("User")


class VoiceRoom(Base):
    __tablename__ = "voice_rooms"

    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    created_by_username = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class MailMessage(Base):
    __tablename__ = "mail_messages"

    id = Column(Integer, primary_key=True, index=True)
    sender_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    recipient_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    recipient_address = Column(String, nullable=False, index=True)  # local username or remote address
    subject = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False)
    sender_deleted = Column(Boolean, default=False)
    recipient_deleted = Column(Boolean, default=False)
    delivery_status = Column(String, default="delivered")  # delivered, queued_remote, failed
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    sender = relationship("User", foreign_keys=[sender_user_id])
    recipient = relationship("User", foreign_keys=[recipient_user_id])


class ChatServer(Base):
    __tablename__ = "chat_servers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    icon_url = Column(String, nullable=True)
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    owner = relationship("User", foreign_keys=[owner_user_id])
    members = relationship("ChatServerMember", back_populates="server", cascade="all, delete-orphan")
    categories = relationship("ChatCategory", back_populates="server", cascade="all, delete-orphan")
    channels = relationship("ChatChannel", back_populates="server", cascade="all, delete-orphan")
    roles = relationship("ChatRole", back_populates="server", cascade="all, delete-orphan")
    emojis = relationship("ChatEmoji", back_populates="server", cascade="all, delete-orphan")


class ChatServerMember(Base):
    __tablename__ = "chat_server_members"

    id = Column(Integer, primary_key=True, index=True)
    server_id = Column(Integer, ForeignKey("chat_servers.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    nickname = Column(String, nullable=True)
    joined_at = Column(DateTime(timezone=True), server_default=func.now())
    is_banned = Column(Boolean, default=False)

    server = relationship("ChatServer", back_populates="members")
    user = relationship("User", foreign_keys=[user_id])
    role_links = relationship("ChatMemberRole", back_populates="member", cascade="all, delete-orphan")


class ChatRole(Base):
    __tablename__ = "chat_roles"

    id = Column(Integer, primary_key=True, index=True)
    server_id = Column(Integer, ForeignKey("chat_servers.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    color = Column(String, default="#64748b")
    position = Column(Integer, default=0)
    permissions = Column(BigInteger, default=0)
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    server = relationship("ChatServer", back_populates="roles")
    member_links = relationship("ChatMemberRole", back_populates="role", cascade="all, delete-orphan")


class ChatMemberRole(Base):
    __tablename__ = "chat_member_roles"

    id = Column(Integer, primary_key=True, index=True)
    member_id = Column(Integer, ForeignKey("chat_server_members.id"), nullable=False, index=True)
    role_id = Column(Integer, ForeignKey("chat_roles.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    member = relationship("ChatServerMember", back_populates="role_links")
    role = relationship("ChatRole", back_populates="member_links")


class ChatCategory(Base):
    __tablename__ = "chat_categories"

    id = Column(Integer, primary_key=True, index=True)
    server_id = Column(Integer, ForeignKey("chat_servers.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    position = Column(Integer, default=0)

    server = relationship("ChatServer", back_populates="categories")
    channels = relationship("ChatChannel", back_populates="category")
    overwrites = relationship("ChatCategoryPermission", back_populates="category", cascade="all, delete-orphan")


class ChatChannel(Base):
    __tablename__ = "chat_channels"

    id = Column(Integer, primary_key=True, index=True)
    server_id = Column(Integer, ForeignKey("chat_servers.id"), nullable=False, index=True)
    category_id = Column(Integer, ForeignKey("chat_categories.id"), nullable=True, index=True)
    name = Column(String, nullable=False)
    slug = Column(String, nullable=False, index=True)
    channel_type = Column(String, default="text")  # text, voice
    position = Column(Integer, default=0)
    topic = Column(Text, nullable=True)
    linked_voice_room_slug = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    server = relationship("ChatServer", back_populates="channels")
    category = relationship("ChatCategory", back_populates="channels")
    messages = relationship("ChatMessage", back_populates="channel", cascade="all, delete-orphan")
    overwrites = relationship("ChatChannelPermission", back_populates="channel", cascade="all, delete-orphan")


class ChatChannelPermission(Base):
    __tablename__ = "chat_channel_permissions"

    id = Column(Integer, primary_key=True, index=True)
    channel_id = Column(Integer, ForeignKey("chat_channels.id"), nullable=False, index=True)
    role_id = Column(Integer, ForeignKey("chat_roles.id"), nullable=True, index=True)
    member_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    allow_bits = Column(BigInteger, default=0)
    deny_bits = Column(BigInteger, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    channel = relationship("ChatChannel", back_populates="overwrites")


class ChatCategoryPermission(Base):
    __tablename__ = "chat_category_permissions"

    id = Column(Integer, primary_key=True, index=True)
    category_id = Column(Integer, ForeignKey("chat_categories.id"), nullable=False, index=True)
    role_id = Column(Integer, ForeignKey("chat_roles.id"), nullable=True, index=True)
    member_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    allow_bits = Column(BigInteger, default=0)
    deny_bits = Column(BigInteger, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    category = relationship("ChatCategory", back_populates="overwrites")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    server_id = Column(Integer, ForeignKey("chat_servers.id"), nullable=False, index=True)
    channel_id = Column(Integer, ForeignKey("chat_channels.id"), nullable=False, index=True)
    author_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    content = Column(Text, nullable=False)
    media_path = Column(String, nullable=True)
    media_type = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    channel = relationship("ChatChannel", back_populates="messages")
    author = relationship("User", foreign_keys=[author_user_id])
    reactions = relationship("ChatReaction", back_populates="message", cascade="all, delete-orphan")


class ChatEmoji(Base):
    __tablename__ = "chat_emojis"

    id = Column(Integer, primary_key=True, index=True)
    server_id = Column(Integer, ForeignKey("chat_servers.id"), nullable=False, index=True)
    short_code = Column(String, nullable=False)
    unicode_char = Column(String, nullable=True)
    image_url = Column(String, nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    server = relationship("ChatServer", back_populates="emojis")
    created_by = relationship("User", foreign_keys=[created_by_user_id])


class ChatReaction(Base):
    __tablename__ = "chat_reactions"

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(Integer, ForeignKey("chat_messages.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    emoji_code = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    message = relationship("ChatMessage", back_populates="reactions")
    user = relationship("User", foreign_keys=[user_id])


class ChatExServer(Base):
    __tablename__ = "chat_ex_servers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    icon_url = Column(String, nullable=True)
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    owner = relationship("User", foreign_keys=[owner_user_id])
    members = relationship("ChatExServerMember", back_populates="server", cascade="all, delete-orphan")
    categories = relationship("ChatExCategory", back_populates="server", cascade="all, delete-orphan")
    channels = relationship("ChatExChannel", back_populates="server", cascade="all, delete-orphan")
    roles = relationship("ChatExRole", back_populates="server", cascade="all, delete-orphan")
    emojis = relationship("ChatExEmoji", back_populates="server", cascade="all, delete-orphan")


class ChatExServerMember(Base):
    __tablename__ = "chat_ex_server_members"

    id = Column(Integer, primary_key=True, index=True)
    server_id = Column(Integer, ForeignKey("chat_ex_servers.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    nickname = Column(String, nullable=True)
    joined_at = Column(DateTime(timezone=True), server_default=func.now())
    is_banned = Column(Boolean, default=False)

    server = relationship("ChatExServer", back_populates="members")
    user = relationship("User", foreign_keys=[user_id])
    role_links = relationship("ChatExMemberRole", back_populates="member", cascade="all, delete-orphan")


class ChatExRole(Base):
    __tablename__ = "chat_ex_roles"

    id = Column(Integer, primary_key=True, index=True)
    server_id = Column(Integer, ForeignKey("chat_ex_servers.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    color = Column(String, default="#64748b")
    position = Column(Integer, default=0)
    permissions = Column(BigInteger, default=0)
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    server = relationship("ChatExServer", back_populates="roles")
    member_links = relationship("ChatExMemberRole", back_populates="role", cascade="all, delete-orphan")


class ChatExMemberRole(Base):
    __tablename__ = "chat_ex_member_roles"

    id = Column(Integer, primary_key=True, index=True)
    member_id = Column(Integer, ForeignKey("chat_ex_server_members.id"), nullable=False, index=True)
    role_id = Column(Integer, ForeignKey("chat_ex_roles.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    member = relationship("ChatExServerMember", back_populates="role_links")
    role = relationship("ChatExRole", back_populates="member_links")


class ChatExCategory(Base):
    __tablename__ = "chat_ex_categories"

    id = Column(Integer, primary_key=True, index=True)
    server_id = Column(Integer, ForeignKey("chat_ex_servers.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    position = Column(Integer, default=0)

    server = relationship("ChatExServer", back_populates="categories")
    channels = relationship("ChatExChannel", back_populates="category")
    overwrites = relationship("ChatExCategoryPermission", back_populates="category", cascade="all, delete-orphan")


class ChatExChannel(Base):
    __tablename__ = "chat_ex_channels"

    id = Column(Integer, primary_key=True, index=True)
    server_id = Column(Integer, ForeignKey("chat_ex_servers.id"), nullable=False, index=True)
    category_id = Column(Integer, ForeignKey("chat_ex_categories.id"), nullable=True, index=True)
    name = Column(String, nullable=False)
    slug = Column(String, nullable=False, index=True)
    channel_type = Column(String, default="text")  # text, voice
    position = Column(Integer, default=0)
    topic = Column(Text, nullable=True)
    linked_voice_room_slug = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    server = relationship("ChatExServer", back_populates="channels")
    category = relationship("ChatExCategory", back_populates="channels")
    messages = relationship("ChatExMessage", back_populates="channel", cascade="all, delete-orphan")
    overwrites = relationship("ChatExChannelPermission", back_populates="channel", cascade="all, delete-orphan")


class ChatExChannelPermission(Base):
    __tablename__ = "chat_ex_channel_permissions"

    id = Column(Integer, primary_key=True, index=True)
    channel_id = Column(Integer, ForeignKey("chat_ex_channels.id"), nullable=False, index=True)
    role_id = Column(Integer, ForeignKey("chat_ex_roles.id"), nullable=True, index=True)
    member_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    allow_bits = Column(BigInteger, default=0)
    deny_bits = Column(BigInteger, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    channel = relationship("ChatExChannel", back_populates="overwrites")


class ChatExCategoryPermission(Base):
    __tablename__ = "chat_ex_category_permissions"

    id = Column(Integer, primary_key=True, index=True)
    category_id = Column(Integer, ForeignKey("chat_ex_categories.id"), nullable=False, index=True)
    role_id = Column(Integer, ForeignKey("chat_ex_roles.id"), nullable=True, index=True)
    member_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    allow_bits = Column(BigInteger, default=0)
    deny_bits = Column(BigInteger, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    category = relationship("ChatExCategory", back_populates="overwrites")


class ChatExMessage(Base):
    __tablename__ = "chat_ex_messages"

    id = Column(Integer, primary_key=True, index=True)
    server_id = Column(Integer, ForeignKey("chat_ex_servers.id"), nullable=False, index=True)
    channel_id = Column(Integer, ForeignKey("chat_ex_channels.id"), nullable=False, index=True)
    author_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    content = Column(Text, nullable=False)
    media_path = Column(String, nullable=True)
    media_type = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    channel = relationship("ChatExChannel", back_populates="messages")
    author = relationship("User", foreign_keys=[author_user_id])
    reactions = relationship("ChatExReaction", back_populates="message", cascade="all, delete-orphan")


class ChatExEmoji(Base):
    __tablename__ = "chat_ex_emojis"

    id = Column(Integer, primary_key=True, index=True)
    server_id = Column(Integer, ForeignKey("chat_ex_servers.id"), nullable=False, index=True)
    short_code = Column(String, nullable=False)
    unicode_char = Column(String, nullable=True)
    image_url = Column(String, nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    server = relationship("ChatExServer", back_populates="emojis")
    created_by = relationship("User", foreign_keys=[created_by_user_id])


class ChatExReaction(Base):
    __tablename__ = "chat_ex_reactions"

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(Integer, ForeignKey("chat_ex_messages.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    emoji_code = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    message = relationship("ChatExMessage", back_populates="reactions")
    user = relationship("User", foreign_keys=[user_id])


# ── Create Module (GeoCities-style page builder) ────────────────────────

class CreatedPage(Base):
    """A user-built web page hosted at /users/{username}/created/{slug}."""
    __tablename__ = "created_pages"

    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    slug = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False, default="Untitled Page")
    html_content = Column(Text, default="")
    css_content = Column(Text, default="")
    page_json = Column(Text, default="{}")  # Visual editor element tree for round-trip editing
    is_published = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    owner = relationship("User", foreign_keys=[owner_id])
