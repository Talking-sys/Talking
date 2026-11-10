# Google OAuth scopes checklist

Talking will request **only non-sensitive** OpenID scopes. Phone SMS sign-in is in v1 (Twilio Verify); this checklist is Google OAuth only.

| Scope | Use | Sensitive? | In v1? |
|---|---|---|---|
| `openid` | Subject identifier | No | Yes |
| `email` | Account recovery / display | No | Yes |
| `profile` | Display name / avatar (optional) | No | Yes |
| Gmail, Drive, Contacts, Calendar, … | — | Yes | **Never** |

## Verification checklist (fill in M10)

- [ ] OAuth client ID **per platform** (Web, iOS, Android) — do not reuse one client
- [ ] Authorized JavaScript origins and redirect URIs match production HTTPS only
- [ ] Support email in Google Cloud Console: **hateaboutyou111@icloud.com**
- [ ] Privacy policy URL live (`/privacy`)
- [ ] Terms URL live (`/terms`)
- [ ] No `access_type=offline` unless a refresh token is actually required (it should not be)
- [ ] State + PKCE on the authorization code flow
- [ ] Do not log ID tokens or `sub` in plaintext; hash user ids (`LOG_SALT`)
- [ ] Account deletion path (Google Play / Apple also require this)
- [ ] Matching emails do **not** auto-merge accounts

This file is an engineering checklist, not a Google policy interpretation.
