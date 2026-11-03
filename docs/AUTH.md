# Auth (M10 stages 0–1 + phone + Google OIDC)

Copyright (C) 2026 hateaboutyouone. Contact: hateaboutyou111@icloud.com.

This document describes **what shipped** in the auth PRs and **what is left**.
Google Sign-In requests only `openid email profile` — see `docs/GOOGLE_SCOPES.md`.
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
| `accounts` | Hosted multi-user | Cookie sessions, CSRF, anonymous + phone + Google (when configured). Missing WS cookies/tokens close the socket. |

`AUTH_MODE=local` is **refused** when `APP_ENV=production`. Production also requires
`APP_SECRET_KEY` (≥32 chars) and `LOG_SALT`. Production `accounts` requires `DATABASE_URL`.
Production also requires `SMS_PROVIDER=twilio` plus `TWILIO_ACCOUNT_SID`,
`TWILIO_AUTH_TOKEN`, and `TWILIO_VERIFY_SERVICE_SID`. `SMS_PROVIDER=console` (or
unset, which defaults to console) is **refused** in production.

## Stage 0 — Postgres foundation

- SQLAlchemy 2 (async, asyncpg) + Alembic.
- Initial migration `001_auth_foundation`: `users`, `identities`, `sessions`,
  `recovery_codes`, `auth_events` (no PII, **90-day retention** comment),
  `login_challenges` (phone OTP / future email codes). `002_google_oauth_states`
  adds `oauth_states` (PKCE verifier encrypted, nonce hashed, ~10 min TTL).
- `users.status` is `active | deleting | deleted`. Nickname unique and nullable.
  `profile_private` defaults true. User ids are UUIDs.
- `identities` unique (`provider`, `subject`). Providers: `anonymous`, `phone`, `google`.
  Email columns are used for verified Google emails (HMAC + AES-GCM). Phone subject is
  `HMAC(phone)`; Google subject is `HMAC(google_sub)`. `phone_enc` / `email_enc` are
  AES-GCM with `DB_ENCRYPTION_KEY` (hash-only if the key is missing).
- Storage interface: in-memory (`AUTH_MODE=local` and tests) and Postgres (`AUTH_MODE=accounts`
  with `DATABASE_URL`).
- CI runs Postgres and an Alembic upgrade / downgrade / upgrade test (`TEST_DATABASE_URL`).

Passkeys, email `login_tokens`, and WebAuthn challenges are **not** implemented here.

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

## Google Sign-In (OIDC)

**Off unless configured.** Empty client IDs, a non-HTTPS redirect URI, or
`GOOGLE_OAUTH_ENABLED=0` leave `/api/auth/google` reporting `{enabled: false}` and
`POST /api/auth/google/start` returns **503 `google_unconfigured`**. Production does
**not** require Google. Use **separate Google Cloud projects** for development and
production, and a **separate OAuth client ID per platform** (web / Android / iOS) —
never reuse one client. Web is a confidential client (`GOOGLE_CLIENT_SECRET_WEB`);
Android and iOS are public clients (PKCE only, no secret).

Hard rules:

- Scopes are exactly `openid email profile`. Incremental consent is not used; do not
  add Gmail, Drive, Contacts, or `access_type=offline`.
- Authorization **code** flow with **PKCE S256**, OAuth **state**, and ID-token **nonce**.
- Redirect URIs are **HTTPS-only** and **exact-match** (no `http://`, no fragments).
  The URI sent to Google is the configured value, never a client-supplied URL.
- ID token checks: `iss` (`https://accounts.google.com` / `accounts.google.com`),
  `aud` (the platform client ID for this dance), `exp` (60 s leeway), `nonce`,
  RS256 signature via Google JWKS (`kid` lookup, refresh once on miss).
- Google access / refresh / ID tokens stay on the server for the exchange and are
  **discarded**. They are never written to cookies, JSON responses, redirects, or
  `localStorage`. Session cookies remain the only browser credentials.
- Identity lookup is `(provider=google, HMAC(sub))` only. **Matching emails do not
  merge accounts.** Linking requires a signed-in user, a **fresh** session (≤10 min),
  and `confirm: true` on start. If that Google account is already on another user →
  **409 `google_taken`**.

Flow:

1. `GET /api/auth/google` — `{enabled, platforms, scopes}` (no client secrets).
2. `POST /api/auth/google/start` `{platform?: "web"|"android"|"ios", intent?: "login"|"link", confirm?: true}`
   — returns `{authorization_url, expires_in}`. Rate limit: **10 / hour / IP**.
3. Google redirects to `GET /api/auth/google/callback?code&state` (browser: 303 to
   `PUBLIC_ORIGIN/?auth=ok` with session cookies). Native clients may `POST` the
   same path with `{code, state}` (`Accept: application/json` or `?format=json`
   returns JSON). Rate limit: **20 / hour / IP**.
4. `intent=link` attaches Google to the **current** user; `intent=login` signs in or
   **creates** a user. Unlink: `DELETE /api/auth/methods/{id}`.

Verified emails are stored HMAC + AES-GCM; unverified emails are dropped. Do not log
ID tokens, `sub`, or client secrets (JWTs are redacted).

Google Cloud Console (operator, not in this repo):

| Project | Use |
|---|---|
| **Talking Dev** | Staging / LAN HTTPS. Separate web/Android/iOS clients. Redirect URI = the exact HTTPS callback (`https://<dev-host>/api/auth/google/callback`). Support email `hateaboutyou111@icloud.com`. |
| **Talking Production** | Production domain only. Different client IDs and secret. Never copy Dev clients into prod env. Authorized JS origins and redirect URIs = production HTTPS only. |

Fill `docs/GOOGLE_SCOPES.md` when the Console clients exist. Privacy `/privacy` and
terms `/terms` templates already ship; counsel still owns the legal text (M16).

## Left for later stages (2–7)

| Stage | Scope |
|---|---|
| 2 | Email magic link / one-time code (10 min, hashed). Reuse `login_challenges`. |
| 3 | Google OIDC — **shipped** (`openid email profile` only; no auto-merge). |
| 4 | Passkeys / WebAuthn. |
| 5 | Sign in with Apple, equal prominence, private relay. |
| 6 | Onboarding screens (Google / Apple / email / passkey / phone / anonymous). |
| 7 | Account deletion, export, audit log UI; 90-day `auth_events` purge job. |

## Effort (implementation, not calendar)

Stages 0–1 were scoped at about **2–3 focused days**. Phone is about **+1–1.5 days**
(provider abstraction, E.164, abuse controls, link/no-merge). Google OIDC is about
**+1 day** (PKCE, JWKS, link/no-merge, mocked endpoints). Remaining stages 2 and 4–7
are about **7–10 days** depending on Apple console setup. Whole M10 plan including
phone and Google: roughly **12–16 days** of implementation once secrets and provider
accounts exist.

## Env (placeholders only — never commit real values)

See `.env.example`: `AUTH_MODE`, `PUBLIC_ORIGIN`, `LOG_SALT`, `DB_ENCRYPTION_KEY`,
`DATABASE_URL`, `APP_SECRET_KEY`, `SMS_PROVIDER`, `TWILIO_ACCOUNT_SID`,
`TWILIO_AUTH_TOKEN`, `TWILIO_VERIFY_SERVICE_SID`, `SMS_COUNTRY_ALLOWLIST`,
`SMS_DEFAULT_REGION`, `GOOGLE_OAUTH_ENABLED`, `GOOGLE_CLIENT_ID_WEB` /
`GOOGLE_CLIENT_ID_ANDROID` / `GOOGLE_CLIENT_ID_IOS`, `GOOGLE_CLIENT_SECRET_WEB`,
`GOOGLE_REDIRECT_URI` (optional per-platform `GOOGLE_REDIRECT_URI_*`).
