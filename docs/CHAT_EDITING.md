# Chat message editing (Telegram-like)

Copyright (C) 2026 hateaboutyouone. Contact: hateaboutyou111@icloud.com.

Users can edit the **text** of their own already-sent messages. Edited
messages show **«изменено» / `edited`** next to the time, like Telegram.
This is not ROADMAP **M4** (full chat); it extends the in-memory preview
store from the link-preview PR so the parallel notes PR can adopt the same
fields.

## Behaviour (this PR)

| Rule | Telegram | Talking |
|---|---|---|
| Who | Author only | Author only (`sender_id`). 403 otherwise. M10 binds this to `current_user`; body `sender_id` is ignored (mismatch → 403). |
| What | Text + captions | Text only. Caption kinds are reserved in `CAPTION_KINDS` but rejected with 422 until a caption UI exists. |
| Length | 4096 (app) | 500 chars (`MAX_TEXT`), same as send. |
| Window | ~48 h from original send | `CHAT_EDIT_WINDOW_S` (default **172800**). `0` = unlimited. Expired → **409**. |
| History | Not shown in the thread | Stored as `original_text` + `revisions` on the server. **Never** in `to_dict()` / `to_public()`. |
| Unfurl | Card follows the first URL | Re-unfurl when the first URL changes; drop the card when URLs disappear; keep the card if the first URL is unchanged. |
| Translation | Re-translates | Translation fields + `MessageTranslationCache` are invalidated on a real change. |
| Rate limit | Flood wait | `CHAT_EDIT_RATE_PER_MIN` (default 20 / IP), separate from send. |
| Same text | No “edited” mark | 200 with `changed: false`; `edited_at` stays unset. |

## API

```
PATCH /api/chat/messages/{id}
{ "text": "...", "sender_id": "local", "conversation_id": "local" }
```

| Status | When |
|---|---|
| 200 | Saved (`changed: true\|false`) |
| 400 | Invalid JSON |
| 403 | Not the author |
| 404 | Unknown id |
| 409 | Edit window expired |
| 422 | Empty, too long, missing `text`, or kind not editable |
| 429 | Edit rate limit |

Live clients: `{"type": "message_edited", "message": <public>}` on `/ws/chat`
(and on `app.state.chat_hub`).

`GET /api/chat/messages` also returns `edit_window_s` and `max_text`.
`GET /api/chat` stays **501**.

Public message payload adds:

```json
{ "edited_at": 1730000000.0, "edit_count": 1, "edited": true }
```

Private (store only): `original_text`, `revisions: [{ "text", "at" }, ...]`.

## Client

On own text messages: ⋮ / long-press / right-click → **Изменить** / **Edit**.
Composer grows a Telegram-style banner (original one-liner + **×**).
**↑** in an empty composer edits the last own text message. **Esc** cancels.
**Enter** saves. RU/EN via `<html lang>` (`chat-edit.js`).

## Merge / conflict notes with PR #3 (voice / video notes)

PR #3 (`cursor/telegram-round-notes-82e2`) is a **file-backed** dialog store
(`talking/chat/store.py`, `models.py`, `routes.py`, `/chat`, `/ws/chat`).
This PR originally kept the **in-memory** `MessageStore` from the link-preview
slice. **Landed together:** one `ChatStore`, one `Message` (`kind` canonical,
`type` as alias), one `/ws/chat`. `MessageStore` is an adapter over that store
(`GET/POST/PATCH /api/chat/messages` write the same JSONL as notes).

### Adopt these fields on PR #3 `Message`

Add to `talking/chat/models.py` `Message` (and `to_public()` / `from_dict()`):

```python
edited_at: float | None = None
edit_count: int = 0
original_text: str | None = None          # persist in JSONL, omit from to_public()
revisions: list[dict] = field(default_factory=list)  # persist, omit from to_public()
```

`to_public()` should call `talking.chat.edits.public_edit_fields(self)` so
clients on `/chat` get `edited` / `edited_at` without seeing history.

`ChatStore.update_message` already rewrites a JSONL line — reuse it after
`apply_text_edit`.

### Shared helpers (do not fork)

| Helper | File | PR #3 should |
|---|---|---|
| `check_can_edit` / `apply_text_edit` / `CAPTION_KINDS` | `talking/chat/edits.py` | Import. Map `type` via `message_kind()` (already accepts `kind` **or** `type`). |
| `MessageTranslationCache.invalidate_message` | `talking/chat/translation_cache.py` | Call on text/caption change; drop `translation` / `transcript` display of the **body** if you later allow caption edits. Do not wipe a voice-note **STT transcript** when the (empty) text body is unchanged. |
| `ChatHub.publish_edit` | `talking/chat/events.py` | PR #3 has `hub.py` with the same `subscribe`/`publish`. Keep **one** instance on `app.state.chat_hub`. Prefer PR #3's `hub.py` and move `publish_edit` onto it, or keep `events.py` and stop creating a second hub. |
| `PATCH /api/chat/messages/{id}` | `talking/chat/http.py` | Either keep this path (link-preview pane) **and** add `PATCH /api/chat/dialogs/{id}/messages/{mid}` that calls the same helpers, or route both through one function. |
| `/ws/chat` | `talking/chat/events.py` `register_chat_ws` | **Idempotent**: skips if the path is already registered. PR #3's `register_chat` can own the socket; this PR will not double-bind. Event name to emit: **`message_edited`**. |
| UI | `static/chat-edit.js` + `chat-edit.css` | Include on `chat.html`. Bind ⋮ / long-press / ↑ / Esc on `#chatForm` (PR #3's composer). Do not fork the strings. |

### Concrete file conflicts

| File | This PR | PR #3 | Merge |
|---|---|---|---|
| `talking/chat/__init__.py` | `partial: [link_previews, message_edit]`, exports edits | Notes module, `GET /api/chat` live | Union `partial`; do not drop either export. |
| `talking/chat/models.py` | **absent** | `Message.type`, no edit fields | Add edit fields + `public_edit_fields`. |
| `talking/chat/messages.py` | In-memory `ChatMessage.kind` | **absent** | Keep until M4 replaces both stores. |
| `talking/chat/store.py` | **absent** | JSONL + `update_message` | After edit, `update_message(uid, msg)`. |
| `talking/chat/http.py` vs `routes.py` | `POST/GET/PATCH /api/chat/messages` | `GET/POST /api/chat/dialogs/{id}/messages` | Keep both prefixes during the overlap; share helpers. |
| `talking/chat/hub.py` vs `events.py` | `events.ChatHub` + `/ws/chat` | `hub.ChatHub` + `/ws/chat` | One hub, one WS route. |
| `talking/server.py` | `register_message_routes` + `register_chat_ws` | `register_chat(app)` | Call both; WS skip-if-exists. CORS must keep **PATCH**. |
| `talking/config.py` / `.env.example` | `CHAT_EDIT_WINDOW_S`, `CHAT_EDIT_RATE_PER_MIN` | note size/quota settings | Additive. |
| `talking/static/index.html` | `#previewChat` + `#editBanner` + `#chatMenu` | recorder on `/` and `/chat` | Keep banner ids; notes UI is a sibling. |
| `talking/static/chat.js` | **absent** | composer + notes | Import `chat-edit.js`. |
| `tests/test_server.py` | `GET /api/chat` is 501 | chat is live | Notes PR already updates this; keep 501 **only** while this branch is based on link-previews. |
| `tests/test_unfurl_api.py` | `partial` includes `message_edit` | may not exist | Additive assertion. |

### What PR #3 must **not** do

- Expose `original_text` / `revisions` on `to_public()` (Telegram does not show
  other users’ edit history in the thread).
- Allow editing `voice_note` / `video_note` **media** via this PATCH (kind
  stays 422). Future **captions** can opt into `CAPTION_KINDS` + edit the
  caption/`text` field only.
- Re-run STT because a caption changed.
- Bind a second `/ws/chat`.

## Config

| Variable | Default | Purpose |
|---|---|---|
| `CHAT_EDIT_WINDOW_S` | `172800` | Seconds from `created_at`. `0` = no expiry |
| `CHAT_EDIT_RATE_PER_MIN` | `20` | PATCH / IP |

## Tests

`tests/test_chat_edit.py` (API + store), `tests/test_chat_edit_frontend.py`
(Node state machine: edit mode, ArrowUp, Esc), `tests/test_chat_edit_ui.py`
(Playwright banner + edited label).
