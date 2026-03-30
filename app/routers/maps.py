from __future__ import annotations

import base64
import math
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from .. import database, models, server_utils
from ..config import settings
from . import auth

router = APIRouter(
    prefix="/maps",
    tags=["maps"],
)

templates = Jinja2Templates(directory=str(settings.BASE_DIR / "templates"))

MAPS_ROOT = settings.BASE_DIR / "data" / "maps"
RASTER_TILES_DIR = MAPS_ROOT / "tiles"
MBTILES_PATH = MAPS_ROOT / "base.mbtiles"
RASTER_TILE_EXTENSIONS = ("png", "jpg", "jpeg", "webp")
_TRANSPARENT_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9sX6ix0AAAAASUVORK5CYII="
)


def _normalize_zoom(raw: int) -> int:
    if raw < 0 or raw > 22:
        raise HTTPException(status_code=400, detail="Zoom must be between 0 and 22.")
    return raw


def _tile_media_type(tile_bytes: Optional[bytes] = None) -> str:
    if tile_bytes:
        if tile_bytes.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if tile_bytes.startswith(b"RIFF") and tile_bytes[8:12] == b"WEBP":
            return "image/webp"
    return "image/png"


def _resolve_raster_tile_path(z: int, x: int, y: int) -> Optional[Path]:
    tiles_per_axis = 1 << z
    if y < 0 or y >= tiles_per_axis:
        return None

    wrapped_x = x % tiles_per_axis
    for ext in RASTER_TILE_EXTENSIONS:
        candidate = RASTER_TILES_DIR / str(z) / str(wrapped_x) / f"{y}.{ext}"
        if candidate.exists():
            return candidate
    return None


def _read_mbtiles_tile(z: int, x: int, y: int) -> Optional[bytes]:
    if not MBTILES_PATH.exists():
        return None

    tiles_per_axis = 1 << z
    if x < 0 or y < 0 or y >= tiles_per_axis:
        return None

    wrapped_x = x % tiles_per_axis
    tile_row = (tiles_per_axis - 1) - y

    try:
        with sqlite3.connect(str(MBTILES_PATH)) as conn:
            row = conn.execute(
                """
                SELECT tile_data
                FROM tiles
                WHERE zoom_level = ? AND tile_column = ? AND tile_row = ?
                """,
                (z, wrapped_x, tile_row),
            ).fetchone()
    except sqlite3.Error:
        return None

    if not row:
        return None
    tile_data = row[0]
    if isinstance(tile_data, memoryview):
        return tile_data.tobytes()
    return bytes(tile_data)


def _read_mbtiles_metadata() -> dict[str, str]:
    if not MBTILES_PATH.exists():
        return {}
    try:
        with sqlite3.connect(str(MBTILES_PATH)) as conn:
            rows = conn.execute("SELECT name, value FROM metadata").fetchall()
    except sqlite3.Error:
        return {}
    metadata: dict[str, str] = {}
    for key, value in rows:
        metadata[str(key)] = str(value)
    return metadata


def _tile_folder_sample() -> Optional[tuple[int, int, int]]:
    if not RASTER_TILES_DIR.exists():
        return None
    for z_dir in sorted(RASTER_TILES_DIR.iterdir(), key=lambda item: item.name):
        if not z_dir.is_dir() or not z_dir.name.isdigit():
            continue
        z = int(z_dir.name)
        for x_dir in sorted(z_dir.iterdir(), key=lambda item: item.name):
            if not x_dir.is_dir() or not x_dir.name.isdigit():
                continue
            x = int(x_dir.name)
            for tile_file in sorted(x_dir.glob("*.png")):
                if tile_file.stem.isdigit():
                    return z, x, int(tile_file.stem)
    return None


def _default_view_from_folder_sample(sample: Optional[tuple[int, int, int]]) -> tuple[float, float, int]:
    if not sample:
        return 39.8283, -98.5795, 4
    z, x, y = sample
    lat, lng = tile_xyz_to_lat_lng(z, x, y)
    return lat, lng, max(0, min(z, 14))


def _default_view_from_metadata(metadata: dict[str, str]) -> tuple[float, float, int]:
    center = (metadata.get("center") or "").strip()
    if center:
        parts = [part.strip() for part in center.split(",")]
        if len(parts) >= 3:
            try:
                lng = float(parts[0])
                lat = float(parts[1])
                zoom = int(float(parts[2]))
                return lat, lng, max(0, min(zoom, 14))
            except ValueError:
                pass

    bounds = (metadata.get("bounds") or "").strip()
    if bounds:
        parts = [part.strip() for part in bounds.split(",")]
        if len(parts) == 4:
            try:
                min_lng, min_lat, max_lng, max_lat = [float(part) for part in parts]
                return (min_lat + max_lat) / 2.0, (min_lng + max_lng) / 2.0, 5
            except ValueError:
                pass

    return 39.8283, -98.5795, 4


def tile_xyz_to_lat_lng(z: int, x: int, y: int) -> tuple[float, float]:
    tiles_per_axis = 1 << z
    x = (x % tiles_per_axis) + 0.5
    y = y + 0.5
    n = math.pi - (2.0 * math.pi * y) / tiles_per_axis
    lng = (x / tiles_per_axis) * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(n)))
    return lat, lng


def discover_map_source() -> dict[str, object]:
    folder_available = RASTER_TILES_DIR.exists()
    mbtiles_available = MBTILES_PATH.exists()
    metadata = _read_mbtiles_metadata() if mbtiles_available else {}

    source_type = "none"
    sample = None
    if folder_available:
        source_type = "tile_folder"
        sample = _tile_folder_sample()
        center_lat, center_lng, default_zoom = _default_view_from_folder_sample(sample)
    elif mbtiles_available:
        source_type = "mbtiles"
        center_lat, center_lng, default_zoom = _default_view_from_metadata(metadata)
    else:
        center_lat, center_lng, default_zoom = 39.8283, -98.5795, 4

    return {
        "available": bool(folder_available or mbtiles_available),
        "source_type": source_type,
        "folder_available": folder_available,
        "mbtiles_available": mbtiles_available,
        "mbtiles_path": str(MBTILES_PATH),
        "tiles_path": str(RASTER_TILES_DIR),
        "metadata": metadata,
        "sample_tile": sample,
        "default_center_lat": center_lat,
        "default_center_lng": center_lng,
        "default_zoom": default_zoom,
    }


@router.get("/", response_class=HTMLResponse)
async def maps_index(
    request: Request,
    lat: Optional[float] = Query(default=None),
    lng: Optional[float] = Query(default=None),
    zoom: Optional[int] = Query(default=None),
    current_user: Optional[models.User] = Depends(auth.get_current_user_optional),
    db: AsyncSession = Depends(database.get_db),
):
    source_info = discover_map_source()
    active_modules = await server_utils.get_active_modules(db)

    center_lat = float(lat) if lat is not None else float(source_info["default_center_lat"])
    center_lng = float(lng) if lng is not None else float(source_info["default_center_lng"])
    center_zoom = int(zoom) if zoom is not None else int(source_info["default_zoom"])
    center_zoom = max(0, min(center_zoom, 14))

    return templates.TemplateResponse(
        request=request,
        name="maps.html",
        context={
            "user": current_user,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
            "active_modules": active_modules,
            "map_source": source_info,
            "initial_lat": center_lat,
            "initial_lng": center_lng,
            "initial_zoom": center_zoom,
        },
    )


@router.get("/api/status")
def maps_status():
    return discover_map_source()


@router.get("/tiles/{z}/{x}/{y}.png")
def get_map_tile(z: int, x: int, y: int):
    z = _normalize_zoom(z)
    if y < 0:
        raise HTTPException(status_code=400, detail="Tile y must be non-negative.")

    tile_path = _resolve_raster_tile_path(z, x, y)
    if tile_path is not None:
        with tile_path.open("rb") as handle:
            media_type = _tile_media_type(handle.read(12))
        return FileResponse(tile_path, media_type=media_type)

    mbtile_data = _read_mbtiles_tile(z, x, y)
    if mbtile_data is not None:
        return Response(content=mbtile_data, media_type=_tile_media_type(mbtile_data[:12]))

    return Response(content=_TRANSPARENT_PNG, media_type="image/png", headers={"X-Tile-Missing": "1"})
