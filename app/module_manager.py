from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import server_models
from .config import settings
from .module_manifest import load_manifest, ModuleManifest


class ModuleManagerError(Exception):
    pass


class ModuleManager:
    """
    Control-plane service for module lifecycle.
    v1 runtime implementation uses podman-compose service operations.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def enable(self, module_name: str) -> Dict[str, Any]:
        module = await self._get_module(module_name)
        if not module:
            raise ModuleManagerError(f"Unknown module: {module_name}")

        compose_warning: Optional[str] = None
        manifest = load_manifest(module_name)
        if manifest and manifest.install_policy == "on_enable":
            try:
                await self.install(module_name, manifest)
                await self._run_compose(manifest, "up", "-d", *self._service_names(manifest))
            except ModuleManagerError as e:
                # Keep module UX available even when runtime tooling is absent.
                compose_warning = str(e)

        module.is_enabled = 1
        cfg = self._parse_config_json(module.config_json)
        for key, value in self._default_module_config(module_name).items():
            cfg.setdefault(key, value)
        cfg["enabled_by_manager"] = True
        if compose_warning:
            cfg["last_runtime_warning"] = compose_warning
        module.config_json = json.dumps(cfg)
        await self.db.commit()
        return {"module": module_name, "enabled": True, "warning": compose_warning}

    async def disable(self, module_name: str) -> Dict[str, Any]:
        module = await self._get_module(module_name)
        if not module:
            raise ModuleManagerError(f"Unknown module: {module_name}")

        compose_warning: Optional[str] = None
        manifest = load_manifest(module_name)
        if manifest:
            try:
                await self._run_compose(manifest, "stop", *self._service_names(manifest))
            except ModuleManagerError as e:
                # Disable should still succeed even if runtime stop fails.
                compose_warning = str(e)

        module.is_enabled = 0
        updates: Dict[str, Any] = {"enabled_by_manager": False}
        if compose_warning:
            updates["last_runtime_warning"] = compose_warning
        await self._update_module_config(module, updates)
        await self.db.commit()
        return {"module": module_name, "enabled": False, "warning": compose_warning}

    async def install(self, module_name: str, manifest: Optional[ModuleManifest] = None) -> Dict[str, Any]:
        manifest = manifest or load_manifest(module_name)
        if not manifest:
            return {"module": module_name, "installed": False, "reason": "no_manifest"}

        compose_file = self._compose_path(manifest)
        if not compose_file.exists():
            raise ModuleManagerError(f"Compose file not found: {compose_file}")

        # Pull images first for cleaner startup.
        await self._run_compose(manifest, "pull", *self._service_names(manifest))

        module = await self._get_module(module_name)
        if module:
            await self._update_module_config(
                module,
                {
                    "installed": True,
                    "manifest_version": manifest.version,
                    "runtime": manifest.runtime,
                },
            )
            await self.db.commit()

        return {"module": module_name, "installed": True}

    async def status(self, module_name: str) -> Dict[str, Any]:
        module = await self._get_module(module_name)
        if not module:
            raise ModuleManagerError(f"Unknown module: {module_name}")
        return {
            "module": module_name,
            "enabled": module.is_enabled == 1,
            "config": self._parse_config_json(module.config_json),
        }

    async def _get_module(self, module_name: str) -> Optional[server_models.PlatformModule]:
        result = await self.db.execute(
            select(server_models.PlatformModule).where(server_models.PlatformModule.name == module_name)
        )
        return result.scalar_one_or_none()

    async def _update_module_config(self, module: server_models.PlatformModule, updates: Dict[str, Any]) -> None:
        cfg = self._parse_config_json(module.config_json)
        cfg.update(updates)
        module.config_json = json.dumps(cfg)

    def _parse_config_json(self, raw: Optional[str]) -> Dict[str, Any]:
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {}

    def _default_module_config(self, module_name: str) -> Dict[str, Any]:
        if module_name == "voice":
            turn_host = (settings.DOMAIN or "127.0.0.1").split(":")[0]
            return {
                "default_room": {
                    "slug": "family-voice",
                    "name": "Family Voice",
                    "description": "Default encrypted family voice room.",
                },
                "ice_servers": [
                    {
                        "urls": [
                            f"stun:{turn_host}:3478",
                            f"turn:{turn_host}:3478?transport=udp",
                            f"turn:{turn_host}:3478?transport=tcp",
                        ],
                        "username": "voice",
                        "credential": "voice_local_dev",
                    },
                ],
                "e2ee_mode": "webrtc-dtls-srtp",
                "seed_status": "pending_install",
            }
        return {}

    def _compose_path(self, manifest: ModuleManifest) -> Path:
        p = Path(manifest.compose_file)
        if p.is_absolute():
            return p
        return Path(settings.BASE_DIR) / p

    def _service_names(self, manifest: ModuleManifest) -> list[str]:
        return [svc.name for svc in manifest.services if svc.required]

    async def _run_compose(self, manifest: ModuleManifest, *compose_args: str) -> None:
        if settings.MODULE_RUNTIME != "podman-compose":
            raise ModuleManagerError(f"Unsupported runtime: {settings.MODULE_RUNTIME}")

        compose_file = str(self._compose_path(manifest))
        project = f"homepage_{manifest.name}"
        cmd_candidates = [
            ["podman", "compose", "-f", compose_file, "-p", project, *compose_args],
            ["podman-compose", "-f", compose_file, "-p", project, *compose_args],
        ]
        errors: list[str] = []
        for cmd in cmd_candidates:
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True)
            except FileNotFoundError:
                command_name = " ".join(cmd[:2]) if (len(cmd) > 1 and cmd[0] == "podman" and cmd[1] == "compose") else cmd[0]
                errors.append(f"Command not found: {command_name}")
                continue
            if proc.returncode == 0:
                return
            errors.append(
                f"Command failed: {' '.join(cmd)}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
            )

        raise ModuleManagerError("\n\n".join(errors))
