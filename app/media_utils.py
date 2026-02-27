import shutil
import uuid
from pathlib import Path
from fastapi import UploadFile, HTTPException
from .config import settings

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp", "audio/mpeg", "audio/wav", "audio/ogg"}
MAX_SIZE = 50 * 1024 * 1024 # 50 MB

async def save_upload_file(file: UploadFile) -> dict:
    """
    Saves an uploaded file to the media directory and returns metadata.
    """
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid file type. Allowed: {ALLOWED_TYPES}")
    
    # Generate unique filename
    extension = file.filename.split(".")[-1] if "." in file.filename else "bin"
    unique_name = f"{uuid.uuid4()}.{extension}"
    media_path = Path(settings.MEDIA_ROOT) / unique_name
    
    # Ensure directory exists
    media_path.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        # We need to use await for a file.read() if it was large, 
        # but shutil.copyfileobj works on the spool file object.
        with media_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        file_size = media_path.stat().st_size
        if file_size > MAX_SIZE:
            media_path.unlink()
            raise HTTPException(status_code=400, detail="File too large")
            
        return {
            "filename": unique_name, 
            "url": f"/media/{unique_name}", 
            "type": file.content_type,
            "size": file_size
        }
    except Exception as e:
        if media_path.exists():
            media_path.unlink() # Cleanup partial writes
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")
