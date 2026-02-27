from pydantic_settings import BaseSettings
import os
import secrets
from pathlib import Path
from typing import Optional


def _resolve_secret_key() -> str:
    """Return a persistent random secret key, generating one on first run."""
    env_val = os.environ.get("SECRET_KEY", "").strip()
    if env_val and env_val != "your-super-secret-key-change-this":
        return env_val

    base_dir = _resolve_base_dir()
    key_file = base_dir / "data" / ".secret_key"
    if key_file.exists():
        stored = key_file.read_text().strip()
        if stored:
            return stored

    # Generate and persist
    key_file.parent.mkdir(parents=True, exist_ok=True)
    new_key = secrets.token_urlsafe(48)
    key_file.write_text(new_key)
    return new_key


def _resolve_base_dir() -> Path:
    """Return the data root.  Accepts HOMEPAGE_DATA_DIR env override for the
    frozen (PyInstaller) server binary; falls back to the repo root for dev."""
    env_val = os.environ.get("HOMEPAGE_DATA_DIR", "").strip()
    if env_val:
        return Path(env_val).resolve()
    return Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    PLATFORM_NAME: str = "HOMEPAGE"
    NODE_NAME: str = "HOMEPAGE"
    SECRET_KEY: str = _resolve_secret_key()
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    
    # Paths — support HOMEPAGE_DATA_DIR for frozen binary
    BASE_DIR: Path = _resolve_base_dir()
    MEDIA_ROOT: str = str(BASE_DIR / "media")
    DATABASE_URL: str = f"sqlite+aiosqlite:///{BASE_DIR}/local.db"
    WIKI_DATA_DIR: str = str(BASE_DIR / "external data" / "wikipedia")
    WIKI_BZ2_PATH: Optional[str] = None
    WIKI_DB_PATH: Optional[str] = None
    
    # Federation
    FEDERATION_ENABLED: bool = True
    DOMAIN: str = "localhost:8001"
    PROTOCOL: str = "http" # https in prod
    EXTERNAL_SERVER_URL: Optional[str] = None
    
    # Safety
    FORBIDDEN_WORDS: list[str] = []  # Configured by admin via settings page
    
    # External Sync
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None
    MICROSOFT_CLIENT_ID: Optional[str] = None
    MICROSOFT_CLIENT_SECRET: Optional[str] = None
    
    # Modular runtime (container control plane)
    MODULE_MANIFESTS_DIR: str = str(BASE_DIR / "manifests" / "modules")
    MODULE_RUNTIME: str = "podman-compose"
    PODMAN_COMPOSE_FILE: str = str(BASE_DIR / "podman-compose.yml")

    # ── Optional module loading ──────────────────────────────────
    # Modules listed here will have their routers loaded at startup.
    # If the file data/enabled_modules.json exists it overrides this default.
    # Core routers (auth, admin, users, media, posts, community, federation)
    # always load regardless of this setting.
    ENABLED_MODULES: Optional[list[str]] = None  # None = load all

    @property
    def loaded_modules(self) -> set[str]:
        """Return the set of optional module names whose routers should be registered."""
        import json as _json

        ALL_OPTIONAL = {"calendar", "games", "wikipedia", "discussions", "voice", "mail", "chat", "create"}

        # 1. Check data/enabled_modules.json
        modules_file = self.BASE_DIR / "data" / "enabled_modules.json"
        if modules_file.exists():
            try:
                raw = _json.loads(modules_file.read_text("utf-8"))
                if isinstance(raw, list):
                    return {str(m).strip().lower() for m in raw if str(m).strip()} & ALL_OPTIONAL
                if isinstance(raw, dict) and "modules" in raw:
                    return {str(m).strip().lower() for m in raw["modules"] if str(m).strip()} & ALL_OPTIONAL
            except Exception:
                pass

        # 2. Check ENABLED_MODULES setting / env
        if self.ENABLED_MODULES is not None:
            return {m.strip().lower() for m in self.ENABLED_MODULES if m.strip()} & ALL_OPTIONAL

        # 3. Default: load everything
        return ALL_OPTIONAL
    
    @property
    def BASE_URL(self) -> str:
        return f"{self.PROTOCOL}://{self.DOMAIN}"
    
    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
