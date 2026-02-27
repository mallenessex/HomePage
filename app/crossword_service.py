from __future__ import annotations

import json
import os
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from . import models
from .config import settings

VALID_EDITIONS = {"daily", "sunday", "special"}

_DAILY_LEAD_WORDS: tuple[tuple[str, str], ...] = (
    ("BALL", "Round toy that bounces."),
    ("WALL", "A vertical structure in a room."),
    ("HALL", "A large entry corridor."),
    ("CALL", "To phone someone."),
    ("FALL", "Season after summer."),
)

_COMMON_WORD_CLUES: dict[str, str] = {
    "AREA": "Amount of surface space.",
    "LEAD": "To guide others.",
    "LADY": "A polite term for a woman.",
    "SATOR": "First word in the historic Sator square.",
    "GATOR": "Informal name for an alligator.",
    "AREPO": "Second word in the Sator square.",
    "TENET": "A guiding principle.",
    "OPERA": "A musical stage drama.",
    "ROTAS": "Final word in the Sator square.",
}

_SUNDAY_SQUARES: tuple[tuple[str, ...], ...] = (
    ("SATOR", "AREPO", "TENET", "OPERA", "ROTAS"),
    ("GATOR", "AREPO", "TENET", "OPERA", "ROTAS"),
)


def normalize_edition(value: Optional[str], *, default: str = "daily") -> str:
    key = (value or default).strip().lower()
    if key not in VALID_EDITIONS:
        raise ValueError(f"Unsupported crossword edition: {value!r}")
    return key


def local_today() -> date:
    return datetime.now().astimezone().date()


def default_edition_for_date(publish_date: date) -> str:
    return "sunday" if publish_date.weekday() == 6 else "daily"


def parse_publish_date(value: Optional[str]) -> date:
    if not value:
        return local_today()
    raw = value.strip()
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid publish_date {value!r}; expected YYYY-MM-DD.") from exc


def decode_payload_json(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    if isinstance(parsed, dict):
        return parsed
    return {}


def serialize_puzzle(puzzle: models.CrosswordPuzzle, *, include_payload: bool = False) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": puzzle.id,
        "publish_date": puzzle.publish_date.isoformat(),
        "edition": puzzle.edition,
        "title": puzzle.title,
        "source": puzzle.source,
        "status": puzzle.status,
        "created_at": puzzle.created_at.isoformat() if puzzle.created_at else None,
    }
    if include_payload:
        item["payload"] = decode_payload_json(puzzle.payload_json or "{}")
    return item


def _archive_root() -> Path:
    return settings.BASE_DIR / "data" / "crosswords" / "archive"


def _archive_candidates(publish_date: date, edition: str) -> list[Path]:
    day = publish_date.isoformat()
    root = _archive_root()
    return [
        root / f"{day}-{edition}.json",
        root / edition / f"{day}.json",
        root / day / f"{edition}.json",
    ]


def _read_local_archive_payload(publish_date: date, edition: str) -> Optional[dict[str, Any]]:
    for candidate in _archive_candidates(publish_date, edition):
        if not candidate.exists():
            continue
        try:
            loaded = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(loaded, dict):
            return loaded
    return None


def _read_remote_archive_payload(publish_date: date, edition: str) -> Optional[dict[str, Any]]:
    base = os.getenv("CROSSWORD_ARCHIVE_BASE_URL", "").strip().rstrip("/")
    if not base:
        return None
    day = publish_date.isoformat()
    candidates = (
        f"{base}/{edition}/{day}.json",
        f"{base}/{day}-{edition}.json",
    )
    for url in candidates:
        try:
            with urllib.request.urlopen(url, timeout=4) as response:
                status = int(getattr(response, "status", 200) or 200)
                if status >= 400:
                    continue
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _persist_archive_snapshot(publish_date: date, edition: str, payload: dict[str, Any]) -> None:
    path = _archive_root() / edition / f"{publish_date.isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def _build_word_square_payload(
    publish_date: date,
    edition: str,
    rows: list[str],
    title: str,
    custom_clues: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    upper_rows = [row.strip().upper() for row in rows]
    size = len(upper_rows)
    if size == 0 or any(len(row) != size for row in upper_rows):
        raise ValueError("Word-square generator requires a square NxN set of rows.")

    clue_map = dict(_COMMON_WORD_CLUES)
    if custom_clues:
        clue_map.update(custom_clues)

    across_numbers = [1] + [size + i for i in range(1, size)]
    across = []
    for index, word in enumerate(upper_rows):
        number = across_numbers[index]
        across.append(
            {
                "number": number,
                "row": index,
                "col": 0,
                "length": len(word),
                "answer": word,
                "clue": clue_map.get(word, f"Word square entry: {word}."),
            }
        )

    down = []
    for col in range(size):
        word = "".join(row[col] for row in upper_rows)
        down.append(
            {
                "number": col + 1,
                "row": 0,
                "col": col,
                "length": len(word),
                "answer": word,
                "clue": clue_map.get(word, f"Down entry: {word}."),
            }
        )

    return {
        "version": 1,
        "title": title,
        "date": publish_date.isoformat(),
        "edition": edition,
        "rows": size,
        "cols": size,
        "grid": upper_rows,
        "clues": {
            "across": across,
            "down": down,
        },
        "notes": "Generated from a deterministic word-square seed for this date.",
    }


def _generate_payload(publish_date: date, edition: str) -> dict[str, Any]:
    if edition == "sunday":
        index = publish_date.toordinal() % len(_SUNDAY_SQUARES)
        rows = list(_SUNDAY_SQUARES[index])
        title = f"Family Sunday Crossword - {publish_date.isoformat()}"
        return _build_word_square_payload(publish_date, edition, rows, title)

    lead_word, lead_clue = _DAILY_LEAD_WORDS[publish_date.toordinal() % len(_DAILY_LEAD_WORDS)]
    rows = [lead_word, "AREA", "LEAD", "LADY"]
    title = f"Family Daily Crossword - {publish_date.isoformat()}"
    return _build_word_square_payload(
        publish_date,
        edition,
        rows,
        title,
        custom_clues={lead_word: lead_clue},
    )


def _normalize_payload(payload: dict[str, Any], publish_date: date, edition: str) -> dict[str, Any]:
    normalized = dict(payload)
    normalized["version"] = int(normalized.get("version", 1))
    normalized["date"] = publish_date.isoformat()
    normalized["edition"] = edition
    if not isinstance(normalized.get("title"), str) or not normalized.get("title", "").strip():
        normalized["title"] = f"Family {edition.title()} Crossword - {publish_date.isoformat()}"

    grid = normalized.get("grid")
    if not isinstance(grid, list):
        normalized["grid"] = []
        normalized["rows"] = 0
        normalized["cols"] = 0
    else:
        text_rows = [str(row).upper() for row in grid]
        normalized["grid"] = text_rows
        normalized["rows"] = len(text_rows)
        normalized["cols"] = len(text_rows[0]) if text_rows else 0

    clues = normalized.get("clues")
    if not isinstance(clues, dict):
        normalized["clues"] = {"across": [], "down": []}
    else:
        across = clues.get("across")
        down = clues.get("down")
        normalized["clues"] = {
            "across": across if isinstance(across, list) else [],
            "down": down if isinstance(down, list) else [],
        }
    return normalized


async def get_crossword(db: AsyncSession, publish_date: date, edition: str) -> Optional[models.CrosswordPuzzle]:
    normalized_edition = normalize_edition(edition)
    result = await db.execute(
        select(models.CrosswordPuzzle).where(
            models.CrosswordPuzzle.publish_date == publish_date,
            models.CrosswordPuzzle.edition == normalized_edition,
        )
    )
    return result.scalar_one_or_none()


async def list_crosswords(
    db: AsyncSession,
    *,
    limit: int = 30,
    edition: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> list[models.CrosswordPuzzle]:
    cap = max(1, min(int(limit), 366))
    stmt = select(models.CrosswordPuzzle)
    if edition:
        stmt = stmt.where(models.CrosswordPuzzle.edition == normalize_edition(edition))
    if date_from:
        stmt = stmt.where(models.CrosswordPuzzle.publish_date >= date_from)
    if date_to:
        stmt = stmt.where(models.CrosswordPuzzle.publish_date <= date_to)
    stmt = stmt.order_by(
        models.CrosswordPuzzle.publish_date.desc(),
        models.CrosswordPuzzle.edition.asc(),
    ).limit(cap)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def publish_crossword(
    db: AsyncSession,
    publish_date: date,
    edition: str,
) -> models.CrosswordPuzzle:
    normalized_edition = normalize_edition(edition)

    existing = await get_crossword(db, publish_date, normalized_edition)
    if existing:
        return existing

    payload = _read_local_archive_payload(publish_date, normalized_edition)
    source = "archive_local"
    if payload is None:
        payload = _read_remote_archive_payload(publish_date, normalized_edition)
        source = "archive_remote"
    if payload is None:
        payload = _generate_payload(publish_date, normalized_edition)
        source = "generated"

    payload = _normalize_payload(payload, publish_date, normalized_edition)
    title = str(payload.get("title") or f"Family {normalized_edition.title()} Crossword")

    record = models.CrosswordPuzzle(
        publish_date=publish_date,
        edition=normalized_edition,
        title=title,
        source=source,
        status="published",
        payload_json=json.dumps(payload, ensure_ascii=True),
    )
    db.add(record)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raced = await get_crossword(db, publish_date, normalized_edition)
        if raced:
            return raced
        raise
    await db.refresh(record)

    try:
        _persist_archive_snapshot(publish_date, normalized_edition, payload)
    except Exception:
        # Archive snapshot persistence should not block publish.
        pass

    return record


async def ensure_crosswords_for_date(
    db: AsyncSession,
    publish_date: date,
) -> dict[str, models.CrosswordPuzzle]:
    published: dict[str, models.CrosswordPuzzle] = {}
    published["daily"] = await publish_crossword(db, publish_date, "daily")
    if publish_date.weekday() == 6:
        published["sunday"] = await publish_crossword(db, publish_date, "sunday")
    return published
