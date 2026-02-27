from fastapi import APIRouter, File, UploadFile, Depends, HTTPException, status
import shutil
from pathlib import Path
import uuid
from typing import Annotated

from ..config import settings
from . import auth
from .. import models, media_utils

router = APIRouter(
    tags=["media"]
)

@router.post("/api/media/upload")
async def upload_media(
    file: UploadFile = File(...),
    current_user: Annotated[models.User, Depends(auth.get_current_user)] = None
):
    if not current_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    return await media_utils.save_upload_file(file)
