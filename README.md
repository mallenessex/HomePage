# HOMEPAGE

Family-first local social platform with optional federation, modular features, and a contained desktop client.

Each server (node) has its own name — for example, **House Fantastico**.

## Current Scope

- FastAPI web app with role-based accounts (`admin`, `parent`, `child`)
- Family/custody management for child accounts
- Module system (enable/disable in Admin Modules)
- Chat server + channel model
- Chat channel groups with inherited group permissions
- Text + voice channels
- Private DMs
- Message reactions
- Voice module (WebRTC signaling + TURN support)
- Mail module (local mailbox + federated scaffolding)
- External join request workflow keyed by server ID
- Separate contained client app for remote users (`client_app.py`)

## Run

Windows (recommended):

```bat
run_server.bat
```

Linux/macOS:

```bash
chmod +x run_server.sh
./run_server.sh
```

macOS Finder double-click launcher:

```bash
chmod +x run_server.command
chmod +x run_server_macos.command
```

Then double-click `run_server.command`.
If you want the Terminal window to stay open after exit, use `run_server_macos.command` instead.

PowerShell (cross-platform, optional):

```powershell
pwsh -File ./run_server.ps1
```

Behavior:
- If Podman is available: starts containerized stack (`podman-compose.yml`)
- Otherwise: falls back to local `uvicorn` on `http://127.0.0.1:8001`

Voice for remote devices:
- Use `https://<host-lan-ip>:8443` (not `http://...:8001`) so browsers/webviews allow mic access.
- Run `run_server.bat` as Administrator once to configure LAN HTTPS port proxy on Windows.

Secure mode (`.local`) flow:
- Open `http://<host-lan-ip>:8001` on first run.
- In `/admin/settings`, enable `Secure Mode`, set `<name>.local` + LAN IP, then save.
- Restart with `run_server.bat` (Administrator).
- In the client app, use `Fetch Secure Profile` + `Apply Secure Setup` to automate CA/hosts setup.
- In `/admin/settings`, use `Automated Setup Scripts` to download OS-specific setup files for client machines.
- In `/admin/settings`, use `Server Host Checklist` for host-OS-specific launcher guidance.
- In `/admin/settings`, use `Client Connection Share` to copy/download the exact `Server URL` and `Target Server ID`.
- In `/admin/settings`, use `Download LAN Windows Client (.zip)` for same-network users and `Download Off-Network Windows Client (.zip)` for remote users.
- Move clients to `https://<name>.local:8443` (WSS is used automatically for voice signaling).

## Important Admin Pages

- `/admin/settings`: server identity, federation/join policy, users, join requests
- `/admin/modules`: global module enable/disable
- `/admin/chat-settings`: chat servers, roles, channel groups, group permissions
- `/admin/family`: child account + custody management

## Client

- Source: `client_app.py`
- Setup/build docs: `CLIENT_APP_SETUP.md`

## Docs

- `MODULE_ARCHITECTURE.md`: module runtime architecture
- `CLIENT_APP_SETUP.md`: client install/build/use
- `HTTPS_SETUP.md`: TLS proxy setup
- `NETWORK_LOCKDOWN.md`: safer network exposure guidance
