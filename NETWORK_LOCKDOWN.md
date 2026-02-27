# Network Lockdown (Windows-first)

This setup makes client PCs able to use only your HOMEPAGE server while blocking most of the internet.

## What this enforces

- Outbound allowlist for:
  - `443/tcp` to your server (app)
  - `3478/tcp`, `3478/udp` to your server (TURN)
  - `49160-49200/udp` to your server (TURN media relay)
- Optional:
  - DNS (`53`) and NTP (`123`) if your endpoint needs them
- Blocks all other outbound traffic.

## Files

- `ops/windows/firewall_client_lockdown.ps1`
- `ops/windows/firewall_client_unlock.ps1`

## Apply on a client PC (Run PowerShell as Administrator)

Example with only strict allowlist:

```powershell
powershell -ExecutionPolicy Bypass -File .\ops\windows\firewall_client_lockdown.ps1 -ServerIP 192.168.1.50
```

Example with DNS and NTP allowed:

```powershell
powershell -ExecutionPolicy Bypass -File .\ops\windows\firewall_client_lockdown.ps1 -ServerIP 192.168.1.50 -AllowDns -AllowNtp
```

If you use a domain and want FQDN allow too:

```powershell
powershell -ExecutionPolicy Bypass -File .\ops\windows\firewall_client_lockdown.ps1 -ServerIP 192.168.1.50 -ServerFQDN family.example.net
```

## Roll back

```powershell
powershell -ExecutionPolicy Bypass -File .\ops\windows\firewall_client_unlock.ps1
```

## Recommended safety defaults

1. Keep server policy on `Accept Conditionally` in Admin Settings.
2. Keep HTTPS enabled (`443` only for app access).
3. Only install the client app on restricted user accounts.
4. Pair with router/device parental controls for defense in depth.

## Notes

- The script creates **outbound** rules only.
- If voice chat is not needed, you can remove TURN rules and keep only `443/tcp`.
- Test on one machine first before applying fleet-wide.
