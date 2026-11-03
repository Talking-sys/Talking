# Security audit (skeleton)

Seven layers. Stage 1 records the map; M13 fills ratings (critical → low) and turns pip-audit / bandit into gating CI.

Contact: hateaboutyou111@icloud.com.

| # | Layer | Stage 1 status | Later (M13 / commercial repo) |
|---|---|---|---|
| 1 | Backend | CSP, nosniff, frame-ancestors none, WS origin check, HMAC WS token, rate buckets | AES-256-GCM fields, pgcrypto option, SSRF checks, session TTL |
| 2 | Frontend | Same-origin static, no eval, mic over HTTPS | Mini-browser sandbox without JS/data URLs, EXIF strip |
| 3 | Admin | **Not in this repo** — stub `/api/commercial/admin/health` | Secret token in header or one-time cookie form, never in URL; 2FA |
| 4 | Licences | Stub `/api/commercial/license` | Signed licences, public key only in the client build, revocation list |
| 5 | Docker | Non-root user 10001, no secrets in image, healthcheck | Read-only rootfs, dropped caps, no docker rebuild from admin by default |
| 6 | Data | Local files 0700, no secrets in git | Hashed user_id in logs, 3-minute auto-delete of translated segments, backup TTL |
| 7 | Dependencies | permissive/GPL-compatible `requirements.txt`; copyleft TTS optional and off | pip-audit + bandit gating, licence scan on CI |

Do not store cards. Payment webhooks are verified in the proprietary service, not here.
