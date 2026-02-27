from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import json
from . import models, schemas, auth_utils

async def get_user_by_username(db: AsyncSession, username: str):
    """
    Retrieves a user by username.
    """
    result = await db.execute(select(models.User).where(models.User.username == username))
    return result.scalars().first()

async def create_user(db: AsyncSession, user: schemas.UserCreate):
    """
    Creates a new user, generating their password hash and keypair.
    """
    hashed_password = auth_utils.get_password_hash(user.password)
    public_key_hex, private_key_hex = auth_utils.generate_keypair()
    rsa_public_pem, rsa_private_pem = auth_utils.generate_rsa_keypair()
    
    # Encrypt private key (TODO: use actual encryption with password)
    private_key_enc = private_key_hex
    rsa_private_key_enc = rsa_private_pem

    db_user = models.User(
        username=user.username,
        display_name=user.display_name,
        password_hash=hashed_password,
        public_key=public_key_hex,
        private_key_enc=private_key_enc,
        rsa_public_key=rsa_public_pem,
        rsa_private_key_enc=rsa_private_key_enc
    )
    
    try:
        db.add(db_user)
        await db.commit()
        await db.refresh(db_user)
        return db_user
    except Exception as e:
        await db.rollback()
        raise e


async def create_user_no_commit(db: AsyncSession, user: schemas.UserCreate, pre_hashed_password: str | None = None):
    """
    Creates a new user but does not commit, so callers can include it
    inside a larger transaction.

    If *pre_hashed_password* is provided it is stored directly (must be a
    valid argon2 hash); otherwise ``user.password`` is hashed.
    """
    hashed_password = pre_hashed_password or auth_utils.get_password_hash(user.password)
    public_key_hex, private_key_hex = auth_utils.generate_keypair()
    rsa_public_pem, rsa_private_pem = auth_utils.generate_rsa_keypair()

    db_user = models.User(
        username=user.username,
        display_name=user.display_name,
        password_hash=hashed_password,
        public_key=public_key_hex,
        private_key_enc=private_key_hex,
        rsa_public_key=rsa_public_pem,
        rsa_private_key_enc=rsa_private_pem,
    )
    db.add(db_user)
    await db.flush()
    return db_user

async def update_user(db: AsyncSession, user: models.User, update_data: schemas.UserUpdate):
    """
    Updates user profile data.
    """
    if update_data.display_name is not None:
        user.display_name = update_data.display_name
    if update_data.bio is not None:
        user.bio = update_data.bio
    if update_data.avatar_url is not None:
        user.avatar_url = update_data.avatar_url
    if update_data.custom_css is not None:
        user.custom_css = update_data.custom_css
    if update_data.theme_preset is not None:
        user.theme_preset = update_data.theme_preset
    if update_data.birthdate is not None:
        user.birthdate = update_data.birthdate
    if update_data.hidden_modules is not None:
        normalized_hidden = []
        seen = set()
        for module_name in update_data.hidden_modules:
            if not module_name:
                continue
            cleaned = module_name.strip().lower()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            normalized_hidden.append(cleaned)
        user.module_bar_config = json.dumps({"hidden_modules": normalized_hidden})
        
    await db.commit()
    await db.refresh(user)
    return user
