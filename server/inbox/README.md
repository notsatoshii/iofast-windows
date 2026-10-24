# iofast phone inbox

Runs on the droplet in /opt/agentos/iofast-inbox (docker compose), behind Caddy at
`https://agentos.rest/iofast-inbox/*`. Keys come from an env file outside the repo.

- `POST /links` (phone key): queue a captured link.
- `GET /links`, `POST /links/{id}/done` (PC key): the PC app pulls and files links.
- `GET /app/phone.json`, `GET /app/phone.apk` (phone key): phone self-update. To release, put
  the signed APK at `app/phone.apk` and `{"versionCode","versionName","sha256","size"}` at
  `app/phone.json`; phones pick it up within 6 hours or on next app start.
