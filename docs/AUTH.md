# Auth (M10 stages 0–1 + phone + stage 7 privacy)

Copyright (C) 2026 hateaboutyouone. Contact: hateaboutyou111@icloud.com.

This document describes **what shipped** in the first auth PR and **what is left**.
Google OAuth still requests only `openid email profile` — see `docs/GOOGLE_SCOPES.md`.
Commercial entitlements stay behind `COMMERCIAL_API_URL` and are keyed by opaque user UUIDs.

The owner later **added phone number sign-in / sign-up**. Codes arrive by **SMS**.
That overrides the original “no SMS in v1” line in `docs/specs/OWNER_SPEC.md` §13
and `docs/ROADMAP.md` M10. The verbatim spec is unchanged; the addendum records
the decision (including the later change that SMS via Twilio Verify is the
production path).

## Modes

| `AUTH_MODE` | When | Behaviour |
|---|---|---|
| `local` (default in development and tests) | Home Wi-Fi translator, pytest, Playwright | Single user id (`USER_ID` or `local`). No cookies required. Existing chat/WS tests stay green. |
| `accounts` | Hosted multi-user | Cookie sessions, CSRF, anonymous + phone. Missing WS cookies/tokens close the socket. |

`AUTH_MODE=local` is **refused** when `APP_ENV=production`. Production also requires
`APP_SECRET_KEY` (≥32 chars) and `LOG_SALT`. Production `accounts` requires `DATABASE_URL`.
Production also requires `SMS_PROVIDER=twilio` plus `TWILIO_ACCOUNT_SID`,
`TWILIO_AUTH_TOKEN`, and `TWILIO_VERIFY_SERVICE_SID`. `SMS_PROVIDER=console` (or
unset, which defaults to console) is **refused** in production.

## Stage 0 — Postgres foundation

- SQLAlchemy 2 (async, asyncpg) + Alembic.
- Initial migration `001_auth_foundation`: `users`, `identities`, `sessions`,
  `recovery_codes`, `auth_events` (no PII, **90-day retention** comment),
  `login_challenges` (phone OTP / future email codes).
- `users.status` is `active | deleting | deleted`. Nickname unique and nullable.
  `profile_private` defaults true. User ids are UUIDs.
- `identities` unique (`provider`, `subject`). Providers in this PR: `anonymous`, `phone`.
  Email columns exist for later stages. Phone subject is `HMAC(phone)`; `phone_enc` is
  AES-GCM with `DB_ENCRYPTION_KEY` (hash-only if the key is missing).
- Storage interface: in-memory (`AUTH_MODE=local` and tests) and Postgres (`AUTH_MODE=accounts`
  with `DATABASE_URL`).
- CI runs Postgres and an Alembic upgrade / downgrade / upgrade test (`TEST_DATABASE_URL`).

Passkeys, email `login_tokens`, OAuth states, and WebAuthn challenges are **not**
implemented here (stubs only via unused identity providers).

## Stage 1 — sessions and server-side identity

Cookies (nothing in `localStorage`):

| Cookie | TTL | Notes |
|---|---|---|
| `__Host-access` | ~15 min | HMAC over `session_id` + `version`. `httpOnly`, `Secure`, `SameSite=Lax`, `Path=/`. |
| `__Secure-refresh` | ~30 days | `Path=/api/auth`. Rotated on every refresh. Store **hash only**. Reuse of an old refresh token **revokes the whole family** and closes sockets. |

Non-production also sets unprefixed `access` / `refresh` aliases so HTTP TestClient works.
In production the `__Host-` / `__Secure-` names are required (HTTPS).

Endpoints:

- `POST /api/auth/anonymous` — create anonymous account; return a recovery code **once**; store hash only
- `POST /api/auth/recover` — recovery code → new session (single use)
- `POST /api/auth/refresh`
- `POST /api/auth/logout` / `POST /api/auth/logout-all`
- `GET /api/auth/me` / `GET /api/auth/sessions` / `DELETE /api/auth/sessions/{id}`
- `GET /api/auth/methods` / `DELETE /api/auth/methods/{id}` — unlink a sign-in method (not the last one)
- `GET /api/auth/events` — the caller's own security events (no IP hashes, tokens, or phones)
- `GET /api/auth/account` / `POST /api/auth/account/delete` / `POST /api/auth/account/delete/cancel`
- `POST /api/auth/export` / `GET /api/auth/export/{id}` / `GET /api/auth/export/{id}/download`

CSRF: `SameSite=Lax` + `Origin` / `Sec-Fetch-Site` + `X-CSRF-Token` on POST/PATCH/DELETE
when a session cookie is present. CORS with credentials only for an explicit allowlist
(`PUBLIC_ORIGIN` and `APP_CORS_ORIGINS`). Exempt: `/api/sdk/`, commercial webhook.

`current_user` (via `AuthMiddleware` + `require_user`) replaces `_uid_from_app` in chat,
voice-translate, history, and commercial licence checks. Request-body `sender_id` is
ignored; a mismatch is **403**. Messages are readable/editable only by participants
(file store is already per-user; in-memory tests check senders).

WebSockets (`/ws`, `/ws/chat`, `/ws/translate/live`): session cookie **or** a short
`/api/ws-token` query param, plus Origin (CSWSH). In `accounts` mode a missing token
is no longer “local”. `/api/ws-token` is signed-in only, **60 s**, bound to the session.
`SocketRegistry` closes sockets of revoked sessions (`logout-all`, family reuse).

Auth endpoints are rate-limited (Redis if `REDIS_URL` is up, else memory). Logs hash
`user_id` with `LOG_SALT` and redact phones, emails, tokens, and codes.

Chat storage remains file-based, keyed by the user UUID (or `local` in local mode).

## Phone (owner add-on)

**Provider:** [Twilio Verify](https://www.twilio.com/docs/verify) over **httpx** (no Twilio SDK).

**SMS is the production path.** The owner reversed the earlier “no SMS in v1”
decision: verification codes must arrive by SMS. Twilio Verify SMS is the
primary/default channel. A voice call (“call me instead”) is an **explicit
user-chosen fallback only**, never the default. Phone codes are **not**
delivered by email (`channel=email` is rejected).

Why Twilio Verify: OTP generation, attempt limits, and a fraud/pumping guard
live on Twilio’s side; the same API can place the fallback voice call when the
user asks. Vonage Verify remains a documented alternative (`SMS_PROVIDER=vonage`
returns “not configured” until an adapter is written) and is refused in
production.

`SMS_PROVIDER=console` is a **local development / test sink only** (hash of a
6-digit code, 10 min TTL, 5 attempts, single use; `debug_code` is returned only
when `APP_ENV` is test/development). It never prints the destination number or
code. Startup (and `POST /api/auth/phone/start`) refuses console or missing
Twilio credentials when `APP_ENV=production`.

Flow:

1. `POST /api/auth/phone/start` `{phone, channel?: "sms"|"call"}` — omitted
   channel defaults to **`sms`**. E.164 via `phonenumbers` (Apache-2.0). Country
   allowlist `SMS_COUNTRY_ALLOWLIST`. Rate limits: **3 sends / 15 min per
   number**, **10 / hour per IP**, **60 s resend cooldown**.
2. `POST /api/auth/phone/verify` `{phone, code}` — find or **create** an account (never
   merge with a different signed-in user).
3. `POST /api/auth/phone/link` `{phone, code}` — attach the number to the **current** user.
   Requires a **fresh** session (issued within 10 minutes). If the number already belongs
   to someone else → **409 `phone_taken`** (no auto-merge).
4. Unlink: `DELETE /api/auth/methods/{id}`.

Phones and codes are never logged. Identity `subject` is HMAC, not the number.

## Left for later stages (2–6)

| Stage | Scope |
|---|---|
| 2 | Email magic link / one-time code (10 min, hashed). Reuse `login_challenges`. |
| 3 | Google OAuth (`openid email profile` only). No auto-merge on email. |
| 4 | Passkeys / WebAuthn. |
| 5 | Sign in with Apple, equal prominence, private relay. |
| 6 | Onboarding screens (Google / Apple / email / passkey / phone / anonymous). |

## Stage 7 — deletion, export, audit

Shipped on top of stages 0–1. `users.status` `deleting` / `deleted` is used.

**Deletion** (`POST /api/auth/account/delete`):

- Accounts mode only. Body `{ "confirm": "DELETE" }`.
- Requires a **fresh** session (`auth_at` within 10 minutes — same window as phone link). Refresh does not count as a fresh login.
- Configurable cooling-off: `AUTH_DELETE_GRACE_S` (default **7 days** / 604800 seconds). Cancellable with `POST /api/auth/account/delete/cancel` until `delete_after`.
- After the grace period (or immediately when grace is 0) the job **hard-deletes** identities, sessions, recovery codes, login challenges, and export files; **anonymizes** the user row (`status=deleted`, nickname and langs cleared); wipes chat JSONL, attachments, history, and local archive for that user id; revokes leftover sessions and closes WebSockets.
- A background worker (60 s) also runs due deletions, expires export files, and purges `auth_events` older than `AUTH_EVENTS_RETENTION_DAYS` (default 90).
- Application logs still HMAC-hash `user_id` with `LOG_SALT`. `auth_events` keep the UUID until the 90-day purge (no phones/emails/tokens in `meta`).

**Export** (`POST /api/auth/export`):

- Async job (runs inline in test/dev) writing a JSON archive of the caller's profile, methods (decrypted phone/email when `DB_ENCRYPTION_KEY` exists, never the ciphertext or HMAC subject), sessions (no refresh hashes), security events, chat, attachment **metadata**, history, and archive segments.
- Rate limit: **3 / 24 h per user**, **10 / 24 h per IP**.
- Download is **authenticated** and **short-lived**: `GET /api/auth/export/{id}` mints a `download_token` (`AUTH_EXPORT_DOWNLOAD_TTL_S`, default 15 minutes). The file itself expires after `AUTH_EXPORT_TTL_S` (default 1 hour).
- UI: `/account`.

**Audit:** `GET /api/auth/events` lists the caller's sign-ins, method changes, session revocations, deletion/export events. Admin views use `Message.to_admin()` which redacts **text and transcripts** (`content_redacted`).

## Effort (implementation, not calendar)

Stages 0–1 were scoped at about **2–3 focused days**. Phone is about **+1–1.5 days**
(provider abstraction, E.164, abuse controls, link/no-merge). Stage 7 (deletion / export /
audit) is in this follow-up. Remaining stages 2–6 are about **7–10 days** depending on
Apple/Google console setup. Whole M10 plan including phone: roughly **12–16 days** of
implementation once secrets and provider accounts exist.

## Env (placeholders only — never commit real values)

See `.env.example`: `AUTH_MODE`, `PUBLIC_ORIGIN`, `LOG_SALT`, `DB_ENCRYPTION_KEY`,
`DATABASE_URL`, `APP_SECRET_KEY`, `SMS_PROVIDER`, `TWILIO_ACCOUNT_SID`,
`TWILIO_AUTH_TOKEN`, `TWILIO_VERIFY_SERVICE_SID`, `SMS_COUNTRY_ALLOWLIST`,
`SMS_DEFAULT_REGION`, `AUTH_DELETE_GRACE_S`, `AUTH_EXPORT_TTL_S`,
`AUTH_EXPORT_DOWNLOAD_TTL_S`, `AUTH_EVENTS_RETENTION_DAYS`.
