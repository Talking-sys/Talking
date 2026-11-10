# Group conversations (M6 slice)

Copyright (C) 2026 hateaboutyouone. Contact: hateaboutyou111@icloud.com.

Shared groups on the existing file-backed `ChatStore`. Polls, live location, and
Postgres are **out of scope**. Admin views still go through `Message.to_admin()`
and never receive message bodies.

## Behaviour

- Anyone signed in can create a group (name, optional https/emoji avatar, description).
- Roles: **owner** / **admin** / **member**. Creator is owner.
- Owner/admin: rename, avatar, add/kick members, invite links.
- Owner only: promote/demote admin, transfer, dissolve.
- Members post and leave. Leaving owner auto-transfers to an admin, else the next member.
- Member cap: `CHAT_GROUP_MAX_MEMBERS` (default 20). If `COMMERCIAL_API_URL` is set, unpaid/trial/unreachable fail-closed to `CHAT_GROUP_TRIAL_MAX_MEMBERS` (default 5).
- Invite links: opaque token stored as SHA-256, expiry + max uses. Token returned **once**.
- Only members can list or post. WebSocket events fan out to member user ids only (never `*`).
- Each member has a language. Text is translated into every distinct member language, cached, and always attempted even when source equals target. Provider failure returns the original with `translation_status=fallback`. Voice-command utterances are not sent to the translator.

## API

```
POST   /api/chat/groups
GET    /api/chat/groups
GET    /api/chat/groups/{id}
PATCH  /api/chat/groups/{id}
DELETE /api/chat/groups/{id}
POST   /api/chat/groups/{id}/leave
POST   /api/chat/groups/{id}/members          {user_id, role?, lang?}
PATCH  /api/chat/groups/{id}/members/{uid}    {role?, lang?}
DELETE /api/chat/groups/{id}/members/{uid}
POST   /api/chat/groups/{id}/transfer         {user_id}
POST   /api/chat/groups/{id}/invites          {expires_in?, max_uses?}
GET    /api/chat/groups/{id}/invites
DELETE /api/chat/groups/{id}/invites/{invite_id}
GET    /api/chat/groups/join/{token}
POST   /api/chat/groups/join                  {token, lang?}
```

Group dialogs also appear in `GET /api/chat/dialogs` (`kind: group`) and accept
`POST /api/chat/dialogs/{id}/messages` / `GET /api/chat/messages?conversation_id=`.
