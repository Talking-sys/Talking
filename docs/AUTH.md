# Auth (M10 stages 0–1 + phone)

Copyright (C) 2026 hateaboutyouone. Contact: hateaboutyou111@icloud.com.

This document describes **what shipped** in the first auth PR, the anonymous-account
product follow-up, and **what is left**.
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
  `login_challenges` (phone OTP / future email codes). Follow-up `002_anonymous_accounts`
  adds `users.last_seen_at` for inactivity expiry.
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
- `GET /api/auth/anonymous/policy` — public capability catalog + current anonymous limits
- `POST /api/auth/recover` — recovery code → new session (single use; replacement policy below)
- `POST /api/auth/recovery/rotate` — signed-in: invalidate unused codes and show a new one once
- `POST /api/auth/upgrade/confirm` — explicit merge of anonymous data into a different existing account
- `POST /api/auth/refresh`
- `POST /api/auth/logout` / `POST /api/auth/logout-all`
- `GET /api/auth/me` / `GET /api/auth/sessions` / `DELETE /api/auth/sessions/{id}`
- `GET /api/auth/methods` / `DELETE /api/auth/methods/{id}` — unlink a sign-in method (not the last one)

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

## Anonymous accounts (product)

Anonymous is a **first-class hosted account**, not a throwaway cookie. Creation stores
only a UUID, an `anonymous` identity, hashed recovery code, and hashed IP on the session.
No phone, email, or nickname is required.

`AUTH_MODE=local` is the home Wi-Fi translator and is **not** subject to these limits.

### Capability limits

`GET /api/auth/anonymous/policy` and `GET /api/auth/me` → `capabilities` describe what
the current user may do. Named capabilities:

| Capability | Default anonymous | Notes |
|---|---|---|
| `chat` | allowed | Text chat |
| `chat_notes` | allowed | Voice/video notes + uploads |
| `voice_translate` | allowed | Live + record |
| `history` | allowed | 3-minute rolling buffer |
| `settings` | allowed | Pause / languages / Whisper model |
| `transcription` | **denied** | Premium on-demand STT; upgrade first |
| `talk_coins` | **denied** | Commercial |
| `checkout` | **denied** | Commercial |
| `games` | **denied** | M8 placeholder still 501 for verified users |
| `groups` | **denied** | Future M6 |
| `nickname` | **denied** | Unique public nick (M15); avoids squatting |
| `commercial` | **denied** | Licence proxy |

Allowlist: `ANON_CAPABILITIES` (comma-separated; `none` / `all`). Subtract with
`ANON_DENIED_CAPABILITIES`. Denied calls return **403** `capability_denied`.

Anonymous also gets tighter **rate/quota** caps (verified users keep the existing
chat/WS numbers):

| Env | Default | vs verified |
|---|---|---|
| `ANON_CHAT_MSG_PER_MIN` | 5 | 10 |
| `ANON_CHAT_UPLOAD_PER_MIN` | 5 | 20 |
| `ANON_CHAT_QUOTA_BYTES` | 50 MiB | 500 MiB |
| `ANON_VOICE_TRANSLATE_RECORD_PER_MIN` | 8 | 20 |
| `ANON_WS_CONNECTS_PER_MIN` | 10 | 20 |

### Upgrade in place (keep this account)

Linking a **verified** sign-in method attaches it to the **current** user id. Chat,
notes, media, and history stay put. Phone is implemented today
(`POST /api/auth/phone/link`, or `POST /api/auth/phone/verify` while signed in).
The same `AuthService.upgrade_with_claim(IdentityClaim)` path is how email, Google,
Apple, and passkeys will plug in later (`provider` + `subject` + optional encrypted
email/phone fields).

A signed-in anonymous user who verifies a phone is upgraded, not signed in as a
new user.

### No silent merge

If that verified method already belongs to a **different** account:

- **409** `{provider}_taken` (phone → `phone_taken`)
- `merge_required` is **true** only when the caller is still anonymous
- Response includes a short-lived `merge_token` (`ANON_MERGE_TTL_S`, default 10 min)

Merge does **not** run unless the user posts `POST /api/auth/upgrade/confirm` with
`{"confirm": true, "merge_token": "..."}`. That moves file-backed chat/media/history
onto the existing account, deletes the anonymous auth rows, and issues a session
for the existing user. Verified users who try to link a taken method get 409
**without** a merge token.

### Recovery-code UX

Decision: **a replacement code is issued after use** (`ANON_RECOVERY_REISSUE=always`,
the default). The used code stays invalid. Rationale: the recovery code is the only
secret for an anonymous account; consuming it without a replacement locks the person
out if they later lose the cookie.

| `ANON_RECOVERY_REISSUE` | Behaviour |
|---|---|
| `always` (default) | After a successful recover, the response includes a new `recovery_code` (shown once) |
| `never` | Single-use, no replacement. `recovery_reissued: false` |
| `if_anonymous` | Replacement only while the account has no verified method |

Codes are hashed at rest, returned **once**, and never logged. Signed-in users can
call `POST /api/auth/recovery/rotate` to invalidate unused codes and see a new one.

### Inactivity expiry

Anonymous-**only** accounts (no phone/email/google/apple/passkey identity) expire
after `ANON_IDLE_SECONDS` (default **30 days**) of no `users.last_seen_at`.
`last_seen_at` is updated from session use (throttled to once a minute). A background
loop (`ANON_CLEANUP_INTERVAL_S`, default 1 h; **0 disables**) deletes:

- identities, unused recovery codes, login challenges
- sessions (revoked)
- file-backed chat, media, and 3-minute history
- user row is soft-deleted (`status=deleted`, nickname cleared)

`auth_events` stay (no PII, hashed user id). Accounts that already linked a verified
method are never expired by this job. `ANON_IDLE_SECONDS=0` disables expiry.

### Anti-abuse (create)

`POST /api/auth/anonymous` is limited per client IP (`ANON_CREATE_PER_IP` /
`ANON_CREATE_WINDOW_S`, default **5 / hour**, plus `ANON_CREATE_PER_IP_DAY` default
**20 / day**). IPs are stored hashed on sessions. A signed-in accounts user cannot
mint another anonymous account (`400 already_signed_in`).

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
2. `POST /api/auth/phone/verify` `{phone, code}` — if **not** signed in: find or **create**
   an account. If signed in: same as link (in-place upgrade). Never silently merge.
3. `POST /api/auth/phone/link` `{phone, code}` — attach the number to the **current** user.
   Requires a **fresh** session (issued within 10 minutes). If the number already belongs
   to someone else → **409 `phone_taken`**. Anonymous callers also get `merge_required`
   + `merge_token` (explicit confirm only; see above).
4. Unlink: `DELETE /api/auth/methods/{id}`.

Phones and codes are never logged. Identity `subject` is HMAC, not the number.

## Left for later stages (2–7)

| Stage | Scope |
|---|---|
| 2 | Email magic link / one-time code (10 min, hashed). Reuse `login_challenges`. |
| 3 | Google OAuth (`openid email profile` only). No auto-merge on email. |
| 4 | Passkeys / WebAuthn. |
| 5 | Sign in with Apple, equal prominence, private relay. |
| 6 | Onboarding screens (Google / Apple / email / passkey / phone / anonymous). |
| 7 | Account deletion, export, audit log UI; 90-day `auth_events` purge job. |

## Effort (implementation, not calendar)

Stages 0–1 were scoped at about **2–3 focused days**. Phone is about **+1–1.5 days**
(provider abstraction, E.164, abuse controls, link/no-merge). Remaining stages 2–7 are
about **9–12 days** depending on Apple/Google console setup. Whole M10 plan including
phone: roughly **12–16 days** of implementation once secrets and provider accounts exist.

## Env (placeholders only — never commit real values)

See `.env.example`: `AUTH_MODE`, `PUBLIC_ORIGIN`, `LOG_SALT`, `DB_ENCRYPTION_KEY`,
`DATABASE_URL`, `APP_SECRET_KEY`, `SMS_PROVIDER`, `TWILIO_ACCOUNT_SID`,
`TWILIO_AUTH_TOKEN`, `TWILIO_VERIFY_SERVICE_SID`, `SMS_COUNTRY_ALLOWLIST`,
`SMS_DEFAULT_REGION`, `ANON_CAPABILITIES`, `ANON_DENIED_CAPABILITIES`,
`ANON_CREATE_PER_IP`, `ANON_CREATE_WINDOW_S`, `ANON_CREATE_PER_IP_DAY`,
`ANON_CHAT_MSG_PER_MIN`, `ANON_CHAT_UPLOAD_PER_MIN`, `ANON_CHAT_QUOTA_BYTES`,
`ANON_VOICE_TRANSLATE_RECORD_PER_MIN`, `ANON_WS_CONNECTS_PER_MIN`,
`ANON_RECOVERY_REISSUE`, `ANON_IDLE_SECONDS`, `ANON_CLEANUP_INTERVAL_S`,
`ANON_CLEANUP_BATCH`, `ANON_MERGE_TTL_S`.
