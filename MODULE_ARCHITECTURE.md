# Module Runtime Architecture (HOMEPAGE)

## Summary

The HOMEPAGE server is the control plane for all modules.  
Modules are enabled/disabled from the admin UI and can optionally manage container services through Podman Compose.

## Runtime Model

1. FastAPI control plane
- Router registration and module auth guard live in `app/main.py`
- Module metadata is stored in `platform_modules` (`app/server_models.py`)
- Default modules are seeded in `app/server_utils.py`

2. Module lifecycle service
- `app/module_manager.py` is responsible for:
- `enable(module_name)`
- `disable(module_name)`
- `install(module_name)`
- `status(module_name)`
- Runtime backend currently targets `podman compose` / `podman-compose`

3. Manifest-driven runtime wiring
- Manifests are loaded from `manifests/modules/*.json`
- Schema is defined in `app/module_manifest.py`
- Key fields:
- `name`, `version`, `runtime`, `compose_file`
- `services[]`, `routes[]`, `healthcheck`, `install_policy`

## Active Module Set

Core/default modules in this project:
- `feed`
- `discovery`
- `calendar`
- `games`
- `wikipedia`
- `voice`
- `mail`
- `chat`

## Chat Architecture (Current)

- Dedicated admin page: `/admin/chat-settings`
- Data model:
- `chat_servers`, `chat_server_members`, `chat_roles`
- `chat_categories` (channel groups)
- `chat_channels`
- `chat_category_permissions` (group-level inherited overwrites)
- `chat_channel_permissions` (channel-level overwrites)
- Effective permissions are resolved in `app/routers/chat.py`:
- base role bits
- group overwrite
- channel overwrite

## Container Stack

- Compose file: `podman-compose.yml`
- `run_server.bat`:
- prefers Podman stack
- falls back to local `uvicorn` when Podman is unavailable

## Client Integration

- Separate client app (`client_app.py`) connects to server by URL + server ID
- Join workflow:
- submit handshake
- admin approval (policy dependent)
- contained in-app browser session after approval
