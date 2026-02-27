# HTTPS Setup (HOMEPAGE)

This stack now includes a TLS reverse proxy (`caddy`) in `podman-compose.yml`.

## What changed

- App traffic is proxied through Caddy on:
  - `http://<domain>` -> redirect to HTTPS
  - `https://<domain>` -> reverse proxy to `web:8000`
- The app runs behind proxy headers so generated URLs and websocket URLs use HTTPS/WSS.

## Required env

Set this in `.env`:

```env
APP_DOMAIN=your-domain-or-public-ip
APP_LAN_IP=your-lan-ip
PROTOCOL=https
DOMAIN=your-domain-or-public-ip
```

Notes:
- Use a hostname (recommended) or public IP.
- For production TLS certificates, use a real DNS hostname.
- Current `Caddyfile` uses `tls internal` (local/internal CA). Browsers/devices must trust this CA.
- For local-family deployments, set `APP_DOMAIN=<name>.local` and map that name to `APP_LAN_IP` on client hosts.

## Ports to expose/forward

- `80/tcp` (HTTP redirect + ACME path if you later switch to public certs)
- `443/tcp` (HTTPS app)
- `3478/tcp` and `3478/udp` (TURN)
- `49160-49200/udp` (TURN relay media)

## Start

```bat
run_server.bat
```

Linux/macOS:

```bash
chmod +x run_server.sh
./run_server.sh
```

PowerShell (cross-platform):

```powershell
pwsh -File ./run_server.ps1
```

Then browse:

`https://<APP_DOMAIN>`
