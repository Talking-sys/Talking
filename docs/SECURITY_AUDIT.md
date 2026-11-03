# Security audit (skeleton)

Seven layers. Stage 1 records the map; M13 fills ratings (critical → low) and turns pip-audit / bandit into gating CI.

Contact: hateaboutyou111@icloud.com.

| # | Layer | Stage 1 status | Later (M13 / commercial repo) |
|---|---|---|---|
| 1 | Backend | CSP, nosniff, frame-ancestors none, WS origin check, HMAC WS token, rate buckets, SDK API keys hashed in logs + per-key bucket. Unfurl: DNS-then-IP pin, block private/loopback/link-local/metadata, http(s) :80/:443 only, redirect cap, timeouts, max body, no cookies. Message edit: author-only, 48 h window, separate PATCH rate limit, revision history never on the wire. M10: `__Host-access` / `__Secure-refresh`, CSRF, hashed user ids in logs | AES-256-GCM fields, pgcrypto option. Google/Apple/email/passkeys still later |
| 2 | Frontend | Same-origin static, no eval, mic over HTTPS, camera=(self) only for notes. Mini-browser: empty sandbox, http(s) only, no javascript:/data:, external-open fallback. Preview images from `/api/unfurl/image/` (re-encoded, EXIF stripped) | Broader in-app navigation history (M7) |
| 3 | Admin | **Not in this repo** — stub `/api/commercial/admin/health`. `Message.to_admin()` / `redact_for_admin()` strip **text, transcripts, translations, previews** (`content_redacted`). | Secret token in header or one-time cookie form, never in URL; 2FA. Admin must **not** read chat transcripts. |
| 4 | Licences | Stub `/api/commercial/license` | Signed licences, public key only in the client build, revocation list |
| 5 | Docker | Non-root user 10001, no secrets in image, healthcheck | Read-only rootfs, dropped caps, no docker rebuild from admin by default |
| 6 | Data | Local files 0700, no secrets in git; chat media UUID names + magic-byte sniff + expiring/one-time download tokens; live voice-translation PCM is never persisted. M10 stage 7: user-initiated deletion (fresh session + 7-day default grace), JSON export with short-lived auth download, user-facing `auth_events`, 90-day purge. Logs HMAC-hash user ids (`LOG_SALT`). | Hashed user_id in logs, 3-minute auto-delete of translated segments, backup TTL |
| 7 | Dependencies | permissive/GPL-compatible `requirements.txt`; copyleft TTS optional and off | pip-audit + bandit gating, licence scan on CI |

Do not store cards. Payment webhooks are verified in the proprietary service, not here.
