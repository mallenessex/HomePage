import logging
import shutil
import uuid
from pathlib import Path
from fastapi import UploadFile, HTTPException
from .config import settings

logger = logging.getLogger(__name__)

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp", "audio/mpeg", "audio/wav", "audio/ogg"}
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}  # types that can carry EXIF
MAX_SIZE = 50 * 1024 * 1024 # 50 MB


def _strip_exif(file_path: Path) -> None:
    """Remove EXIF/metadata from images to protect children's privacy.

    GPS coordinates, device info, timestamps, and other metadata are stripped
    so that uploaded photos cannot leak location or identity data.
    """
    try:
        from PIL import Image
        with Image.open(file_path) as img:
            # Create a clean copy without metadata
            clean = Image.new(img.mode, img.size)
            clean.putdata(list(img.getdata()))
            # Preserve format
            fmt = img.format or "PNG"
            save_kwargs = {}
            if fmt == "JPEG":
                save_kwargs["quality"] = 92
                save_kwargs["subsampling"] = 0  # 4:4:4 — preserve quality
            elif fmt == "WEBP":
                save_kwargs["quality"] = 90
            clean.save(file_path, format=fmt, **save_kwargs)
        logger.info("[media] Stripped EXIF metadata from %s", file_path.name)
    except ImportError:
        logger.warning("[media] Pillow not installed — EXIF metadata was NOT stripped from %s", file_path.name)
    except Exception as exc:
        logger.warning("[media] Failed to strip EXIF from %s: %s", file_path.name, exc)

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

        # Strip EXIF/metadata from images (GPS, device info, etc.)
        if file.content_type in IMAGE_TYPES:
            _strip_exif(media_path)

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
