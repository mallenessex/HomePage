from pathlib import Path
from typing import List, Optional
import json

from pydantic import BaseModel, Field

from .config import settings


class HealthcheckSpec(BaseModel):
    url: Optional[str] = None
    timeout_seconds: int = 10


class RouteSpec(BaseModel):
    module_path: str
    upstream: Optional[str] = None


class ServiceSpec(BaseModel):
    name: str
    required: bool = True


class ModuleManifest(BaseModel):
    name: str
    version: str = "1"
    runtime: str = "podman-compose"
    compose_file: str
    services: List[ServiceSpec] = Field(default_factory=list)
    routes: List[RouteSpec] = Field(default_factory=list)
    healthcheck: Optional[HealthcheckSpec] = None
    install_policy: str = "on_enable"  # on_enable, manual


def get_manifest_path(module_name: str) -> Path:
    return Path(settings.MODULE_MANIFESTS_DIR) / f"{module_name}.json"


def load_manifest(module_name: str) -> Optional[ModuleManifest]:
    path = get_manifest_path(module_name)
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return ModuleManifest(**raw)
