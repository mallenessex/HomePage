from __future__ import annotations

import re
import uuid
from functools import lru_cache
from pathlib import Path

from .config import settings

_VALID_SERVER_ID = re.compile(r"^[a-f0-9]{32}$")


def _server_id_file() -> Path:
    return Path(settings.BASE_DIR) / "data" / "server_id.txt"


def _generate_server_id() -> str:
    return uuid.uuid4().hex


@lru_cache(maxsize=1)
def get_or_create_server_id() -> str:
    path = _server_id_file()
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        existing = path.read_text(encoding="utf-8").strip().lower()
        if _VALID_SERVER_ID.match(existing):
            return existing

    server_id = _generate_server_id()
    path.write_text(server_id, encoding="utf-8")
    return server_id
