# Auth (M10 stages 0–1 + phone + Sign in with Apple)

Copyright (C) 2026 hateaboutyouone. Contact: hateaboutyou111@icloud.com.

This document describes **what shipped** in the auth PRs and **what is left**.
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
| `local` (default in development and tests) | Home Wi-Fi translator, pytest, Playwright | Single operator id (`USER_ID` or `local`). Not an anonymous user. HTTP uses that identity; WebSockets still require a signed token. |
| `accounts` | Hosted multi-user | Cookie sessions, CSRF, phone + Apple (when configured). Unauthenticated clients are not users. Missing WS cookies/tokens close the socket. |

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
- `identities` unique (`provider`, `subject`). Providers here: `phone`, `apple`.
  Email columns are used for Apple (hashed + AES-GCM). Phone subject is `HMAC(phone)`;
  `phone_enc` is AES-GCM with `DB_ENCRYPTION_KEY` (hash-only if the key is missing).
  Apple subject is Apple’s opaque `sub` (needed to match server-to-server notifications).
  There is **no anonymous provider** and no anonymous account creation.
- Storage interface: in-memory (`AUTH_MODE=local` and tests) and Postgres (`AUTH_MODE=accounts`
  with `DATABASE_URL`).
- CI runs Postgres and an Alembic upgrade / downgrade / upgrade test (`TEST_DATABASE_URL`).

Passkeys, email magic links, and Google OAuth are **not** implemented here.
Apple OAuth state/nonce/PKCE live in `login_challenges` (`kind=apple_oauth`, 10 min, single use).

## Stage 1 — sessions and server-side identity

Cookies (nothing in `localStorage`):

| Cookie | TTL | Notes |
|---|---|---|
| `__Host-access` | ~15 min | HMAC over `session_id` + `version`. `httpOnly`, `Secure`, `SameSite=Lax`, `Path=/`. |
| `__Secure-refresh` | ~30 days | `Path=/api/auth`. Rotated on every refresh. Store **hash only**. Reuse of an old refresh token **revokes the whole family** and closes sockets. |

Non-production also sets unprefixed `access` / `refresh` aliases so HTTP TestClient works.
In production the `__Host-` / `__Secure-` names are required (HTTPS).

Endpoints:

- `POST /api/auth/recover` — recovery code → new session (single use), if a code was issued
- `POST /api/auth/refresh`
- `POST /api/auth/logout` / `POST /api/auth/logout-all`
- `GET /api/auth/me` / `GET /api/auth/sessions` / `DELETE /api/auth/sessions/{id}`
- `GET /api/auth/methods` / `DELETE /api/auth/methods/{id}` — unlink a sign-in method (not the last one)
- `GET /api/auth/providers` — which sign-in methods are configured (`apple` is false until env is set)

CSRF: `SameSite=Lax` + `Origin` / `Sec-Fetch-Site` + `X-CSRF-Token` on POST/PATCH/DELETE
when a session cookie is present. **Empty `Origin` is rejected.** CORS with credentials
only for an explicit allowlist (`PUBLIC_ORIGIN` and `APP_CORS_ORIGINS`). Exempt:
`/api/sdk/`, commercial webhook, Apple `form_post` callback, Apple server-to-server
notifications.

Identity lives in `talking/identity.py`. `request_user_id` uses the signed-in session
in `accounts` mode and the operator id in `local` mode. Request-body `sender_id` is
ignored; a mismatch is **403**. Messages are readable/editable only by participants
(file store is already per-user; in-memory tests check senders).

WebSockets (`/ws`, `/ws/chat`, `/ws/translate/live`): session cookie **or** a short
`/api/ws-token` query param, plus Origin (CSWSH). A missing token is never “local”
(including in `AUTH_MODE=local`). `/api/ws-token` is signed-in only; in `accounts`
mode it is **60 s** and bound to the session. `SocketRegistry` closes sockets of
revoked sessions (`logout-all`, family reuse).

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

## Sign in with Apple

**Off unless configured.** Empty Apple env vars keep every Apple route at **503
`apple_unconfigured`**. Partial config (some vars set, not all) is refused at
startup. Production does **not** require Apple.

Flow: authorization **code** + **PKCE S256** + **state** + **nonce**. The authorize
request uses `response_mode=form_post` (required by Apple when asking for `name`
and `email`). Redirect URI is the env value only — **exact match**, `https://`, no
caller-supplied override.

1. `GET /api/auth/apple/start` — redirect to Apple (sign-in / registration).
2. `POST /api/auth/apple/start` `{intent: "link"}` — same redirect, bound to the
   **current** user. Requires a **fresh** session (10 minutes) and CSRF. GET +
   `intent=link` is rejected (CSRF).
3. `POST /api/auth/apple/callback` (also GET query) — Apple `form_post`. Exchange
   code at Apple’s token endpoint with a **server-generated client-secret JWT**
   (ES256, team id / key id / Services ID; private key from env or file). Validate
   the ID token against Apple JWKS (`iss=https://appleid.apple.com`, `aud` = client
   id, `exp`, `nonce`).
4. `POST /api/auth/apple/notifications` — Apple server-to-server JWT. Handles
   `consent-revoked` / `account-delete` (unlink Apple; if it was the last method,
   mark the user `deleting` and revoke sessions) and `email-disabled` (drop stored
   email). Unknown `sub` → 200 (idempotent). Invalid JWT → 401.
5. `GET /api/auth/providers` — `{apple: true/false, ...}` for the future onboarding UI.

**Never auto-merge.** Matching email (including Hide My Email / private relay) does
**not** attach to another account. The only Apple identity key is `sub`. Linking an
Apple `sub` that already belongs to someone else → **409 `apple_taken`**.

**Private relay and first-sign-in name.** Hide My Email addresses
(`@privaterelay.appleid.com` / `is_private_email`) are stored hashed + encrypted on
the identity, never logged, never used as a merge key, never returned from
`/api/auth/methods`. Apple sends `name` **only on the first authorization**; later
callbacks omit it. If the account has no nickname, the given name is used when it
is unique; last name is not stored.

Client secret, authorization codes, ID tokens, refresh tokens, and the `.p8` are
never written to the repo, logs, or the frontend bundle.

### What the owner needs from the Apple Developer account

Paid **Apple Developer Program** membership. Then:

1. **App ID** (bundle id, e.g. `com.talking.app`) with **Sign in with Apple** enabled.
   On that App ID, set the **Server-to-Server Notification Endpoint** to
   `https://<production-host>/api/auth/apple/notifications` (HTTPS only).
2. **Services ID** (web client id, e.g. `com.talking.web`) — this is `APPLE_CLIENT_ID`.
   Do not reuse the iOS bundle id as the web client. Enable Sign in with Apple on it.
   Return URLs must **exactly** equal `APPLE_REDIRECT_URI`
   (`https://<production-host>/api/auth/apple/callback`). Register the production
   domain under associated domains. No `http://`, no extra trailing slash unless the
   env value has one.
3. **Key** (Keys → Sign in with Apple) — download the `.p8` **once**. `APPLE_KEY_ID`
   is the key id; `APPLE_TEAM_ID` is Membership → Team ID. Put the PEM in
   `APPLE_PRIVATE_KEY` (or `APPLE_PRIVATE_KEY_FILE` pointing outside the repo).
   Rotate by minting a new key; never commit `.p8`.
4. **Privacy / terms** URLs live (same as Google). Support email:
   **hateaboutyou111@icloud.com**.
5. iOS / Android each get their **own** client (bundle id / package). This PR
   implements the **web** Services ID authorization-code flow. Native clients can
   still POST the authorization code to the same callback after a future platform
   client is registered.
6. Sign in with Apple must have **equal prominence** next to Google on the
   onboarding screens (stage 6). This PR is backend only.
7. Apple requires an account-deletion path (stage 7 UI). The S2S
   `account-delete` / `consent-revoked` handler is already wired.

## Left for later stages (2–7)

| Stage | Scope |
|---|---|
| 2 | Email magic link / one-time code (10 min, hashed). Reuse `login_challenges`. |
| 3 | Google OAuth (`openid email profile` only). No auto-merge on email. |
| 4 | Passkeys / WebAuthn. |
| 5 | ~~Sign in with Apple~~ **done** (this document). Equal-prominence **button** is stage 6. |
| 6 | Onboarding screens (Google / Apple / email / passkey / phone). No anonymous accounts. |
| 7 | Account deletion, export, audit log UI; 90-day `auth_events` purge job. Full wipe after Apple `account-delete` when other methods remain is still a product decision. |

## Effort (implementation, not calendar)

Stages 0–1 were scoped at about **2–3 focused days**. Phone is about **+1–1.5 days**
(provider abstraction, E.164, abuse controls, link/no-merge). Apple is about
**+1 day** once the Developer account, Services ID, key, and exact redirect URI
exist. Remaining stages 2–4, 6–7 are about **7–10 days** depending on Google
console setup. Whole M10 plan including phone and Apple: roughly **12–16 days**
of implementation once secrets and provider accounts exist.

## Env (placeholders only — never commit real values)

See `.env.example`: `AUTH_MODE`, `PUBLIC_ORIGIN`, `LOG_SALT`, `DB_ENCRYPTION_KEY`,
`DATABASE_URL`, `APP_SECRET_KEY`, `SMS_PROVIDER`, `TWILIO_ACCOUNT_SID`,
`TWILIO_AUTH_TOKEN`, `TWILIO_VERIFY_SERVICE_SID`, `SMS_COUNTRY_ALLOWLIST`,
`SMS_DEFAULT_REGION`, `APPLE_CLIENT_ID`, `APPLE_TEAM_ID`, `APPLE_KEY_ID`,
`APPLE_PRIVATE_KEY` / `APPLE_PRIVATE_KEY_FILE`, `APPLE_REDIRECT_URI`.
