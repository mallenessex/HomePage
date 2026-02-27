from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text
from .config import settings
from .models import Base
from . import server_models  # Import to register tables

# Create Async Engine
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False, # Set to True for SQL logging
    future=True
)

# Create Session Factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False
)

async def init_db():
    """Initializes the database (creates tables)."""
    async with engine.begin() as conn:
        # verifying that tables exist, or creating them
        # In production, use Alembic migrations instead of this
        await conn.run_sync(Base.metadata.create_all)
        # Lightweight compatibility patch for existing SQLite installs.
        try:
            rows = await conn.execute(text("PRAGMA table_info(server_settings)"))
            columns = {row[1] for row in rows.fetchall()}
            if "external_join_policy" not in columns:
                await conn.execute(
                    text("ALTER TABLE server_settings ADD COLUMN external_join_policy VARCHAR DEFAULT 'conditional'")
                )
            if "secure_mode_enabled" not in columns:
                await conn.execute(
                    text("ALTER TABLE server_settings ADD COLUMN secure_mode_enabled INTEGER DEFAULT 0")
                )
            if "secure_local_domain" not in columns:
                await conn.execute(
                    text("ALTER TABLE server_settings ADD COLUMN secure_local_domain VARCHAR")
                )
            if "secure_local_ip" not in columns:
                await conn.execute(
                    text("ALTER TABLE server_settings ADD COLUMN secure_local_ip VARCHAR")
                )
        except Exception:
            # Keep startup tolerant; policy falls back in app logic if schema lags.
            pass
        try:
            user_rows = await conn.execute(text("PRAGMA table_info(users)"))
            user_columns = {row[1] for row in user_rows.fetchall()}
            if "module_bar_config" not in user_columns:
                await conn.execute(text("ALTER TABLE users ADD COLUMN module_bar_config TEXT"))
            if "birthdate" not in user_columns:
                await conn.execute(text("ALTER TABLE users ADD COLUMN birthdate DATE"))
            # Normalize legacy role naming for inclusive wording.
            await conn.execute(
                text("UPDATE users SET role = 'adult' WHERE lower(role) = 'parent'")
            )
        except Exception:
            # Keep startup tolerant; module bar falls back to full visibility if schema lags.
            pass
        try:
            chat_rows = await conn.execute(text("PRAGMA table_info(chat_messages)"))
            chat_columns = {row[1] for row in chat_rows.fetchall()}
            if "media_path" not in chat_columns:
                await conn.execute(text("ALTER TABLE chat_messages ADD COLUMN media_path VARCHAR"))
            if "media_type" not in chat_columns:
                await conn.execute(text("ALTER TABLE chat_messages ADD COLUMN media_type VARCHAR"))
        except Exception:
            # Keep startup tolerant for pre-chat schema variants.
            pass
        try:
            lobby_rows = await conn.execute(text("PRAGMA table_info(game_lobbies)"))
            lobby_columns = {row[1] for row in lobby_rows.fetchall()}
            if "play_mode" not in lobby_columns:
                await conn.execute(
                    text("ALTER TABLE game_lobbies ADD COLUMN play_mode VARCHAR DEFAULT 'multiplayer'")
                )
            if "genre" not in lobby_columns:
                await conn.execute(
                    text("ALTER TABLE game_lobbies ADD COLUMN genre VARCHAR DEFAULT 'general'")
                )
        except Exception:
            # Keep startup tolerant for older game schema variants.
            pass

async def get_db():
    """Dependency for getting a database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
