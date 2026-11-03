# Google OAuth scopes checklist

Talking requests **only non-sensitive** OpenID scopes. Phone SMS sign-in is in v1 (Twilio Verify); this checklist is Google OAuth only.

| Scope | Use | Sensitive? | In v1? |
|---|---|---|---|
| `openid` | Subject identifier | No | Yes |
| `email` | Account recovery / display | No | Yes |
| `profile` | Display name / avatar (optional; not persisted today) | No | Yes |
| Gmail, Drive, Contacts, Calendar, … | — | Yes | **Never** |

## Verification checklist (M10 Google OIDC)

Code-enforced (this repo):

- [x] OAuth client ID **per platform** (Web, iOS, Android) — env vars; production refuses reused IDs
- [x] Redirect URIs must be exact-match **HTTPS** (http / fragments / userinfo rejected)
- [x] No `access_type=offline` (authorization URL omits it; no Google refresh token stored)
- [x] State + PKCE S256 on the authorization code flow; ID-token nonce
- [x] Do not log ID tokens or `sub` in plaintext; hash user ids (`LOG_SALT`); JWTs redacted
- [x] Matching emails do **not** auto-merge accounts (link only when signed in + `confirm: true`)

Operator / Google Cloud Console (fill when clients exist — never commit real IDs):

- [ ] **Talking Dev** project: web + Android + iOS clients; redirect `https://<dev-host>/api/auth/google/callback`
- [ ] **Talking Production** project: **different** clients; production HTTPS origins and redirect URIs only
- [ ] Support email in Google Cloud Console: **hateaboutyou111@icloud.com**
- [ ] Privacy policy URL live (`/privacy`) — template ships; counsel text is M16
- [ ] Terms URL live (`/terms`) — template ships; counsel text is M16
- [ ] Account deletion path (Google Play / Apple also require this) — M10 stage 7

This file is an engineering checklist, not a Google policy interpretation.
