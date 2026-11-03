# Premium voice transcription (on-demand)

Telegram-style «Расшифровать» / **Transcribe** (`→А`) on `voice_note` and
`video_note`. This PR **replaces PR #3’s automatic STT-on-upload** with an
on-demand, **premium-gated** job. Audio is recognized on the Talking server;
the GPL core never ships proprietary licensing code.

Copyright (C) 2026 hateaboutyouone. Contact: hateaboutyou111@icloud.com.

## Why local faster-whisper (default)

| Option | In this repo | Privacy | Licence next to GPL core |
|---|---|---|---|
| **faster-whisper** (default) | Already `talking/stt.py` | PCM stays on the server | MIT (CTranslate2 / faster-whisper) |
| OpenAI / xAI-compatible HTTP | Thin client, **off** | Audio would leave the server | HTTP only — no vendor SDK |

Default `CHAT_STT_PROVIDER=faster-whisper` because the owner required
**private, GPL-compatible STT** and the engine already uses Whisper with
**language auto-detect**. A cloud provider (`openai-compatible`) exists as a
pluggable `TranscriptionProvider` and is used only when
`CHAT_STT_PROVIDER=openai-compatible` **and** `CHAT_STT_API_URL` +
`CHAT_STT_API_KEY` are set. Do not turn that on if notes must not leave the box.

ffmpeg decodes WebM/Opus/MP4/OGG; WAV uses the stdlib. Missing ffmpeg → job
`failed` with a reason; the note itself stays.

## Pipeline

1. Upload stores the note with **no** transcript (`transcript_status` empty).
2. Premium user taps **Расшифровать** → `POST /api/chat/messages/{id}/transcribe`.
3. Server checks entitlement (below). Fail closed → **403** `premium_required`.
4. Job status: `pending` → `done` | `failed`. In-process `ThreadPoolExecutor`.
5. Repeat taps are **idempotent**: a `done` row is cached on the message; an
   in-flight job is not restarted. `failed` may be retried.
6. Optional translation uses the existing translator (viewer `target_lang`).
7. `/ws/chat` emits `chat.transcript` and an updated `chat.message`.

`CHAT_TRANSCRIBE_ON_UPLOAD=1` restores PR #3 auto-fill **only if the uploader
is entitled**. Default is off.

### Storage

Transcript fields live **on the message row** (JSONL):

`transcript`, `src_lang`, `transcript_provider`, `transcript_model`,
`transcript_created_at`, `transcript_duration`, plus existing
`translation` / `tgt_lang`.

They are **deleted with the message** (`DELETE /api/chat/messages/{id}` removes
the JSONL line and the media blob). `CHAT_RETENTION_SECONDS` (0 = keep) purges
old rows the same way — there is no separate transcript store.

Admin must not read them. There is no admin message API in this repo.
`Message.to_admin()` / `redact_for_admin()` strip STT fields for M11.

## Premium gating (commercial boundary)

```text
core  --HTTP-->  COMMERCIAL_API_URL/v1/license/entitlements
                 (fallback GET /v1/license/status, tier paid|trial|premium)
```

`talking.commercial.client.CommercialClient.has_entitlement(user, "voice_transcription")`

| Situation | Result |
|---|---|
| `COMMERCIAL_API_URL` set, service grants the flag or a paid/trial tier | allow |
| Service unreachable / HTTP error | **deny** (fail closed) |
| URL empty (stub) | deny |
| `COMMERCIAL_DEV_ENTITLEMENTS=voice_transcription` or `COMMERCIAL_DEV_PREMIUM=1` | allow in development/test only |
| `APP_ENV=production` | **ignores** the dev override |

Decisions are cached (`COMMERCIAL_ENTITLEMENT_CACHE_TTL`, default 60 s; denials
≤ 10 s). No proprietary code is imported.

Free users: `CHAT_TRANSCRIBE_FREE_MODE=lock` (default) shows a locked button and
an upsell toast; `teaser` adds hint copy; `hidden` hides the button. STT never
runs for them.

## HTTP + WS

| Method | Path |
|---|---|
| POST | `/api/chat/messages/{id}/transcribe` body `{ "target_lang": "ru" }` |
| GET | `/api/chat/messages/{id}/transcript` |
| DELETE | `/api/chat/messages/{id}` |
| GET | `/api/chat` → `transcription.entitled` for the UI |
| WS | `chat.transcript` `{ message_id, status, transcript }` |

Errors: **403** `{ "error": "premium_required", "entitlement": "voice_transcription", "source": "stub"|"unreachable"|"commercial"|"cache" }`,
**400** not a note / duration cap, **404**, **429** (`CHAT_TRANSCRIBE_PER_MIN`,
default 8), **503** if `CHAT_TRANSCRIBE=0`.

Duration cap: `CHAT_TRANSCRIBE_MAX_MS` (default 60 s, same as notes).

## Client

`/chat`: `→А` / «Расшифровать» on voice and кружочки, loading state, expandable
transcript, copy, RU/EN toggle (`html[lang]`, `localStorage.ui_lang`).
Non-premium: locked button + upsell. Accessible: `aria-label`, `aria-busy`,
`aria-disabled`, `aria-live`.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `CHAT_TRANSCRIBE` | `1` | Master switch |
| `CHAT_TRANSCRIBE_ON_UPLOAD` | `0` | Legacy PR #3 auto-STT (still premium-gated) |
| `CHAT_TRANSCRIBE_FREE_MODE` | `lock` | `lock` / `teaser` / `hidden` |
| `CHAT_TRANSCRIBE_PER_MIN` | `8` | Rate limit |
| `CHAT_TRANSCRIBE_MAX_MS` | `60000` | Duration cap |
| `CHAT_RETENTION_SECONDS` | `0` | 0 = keep; else purge with the message |
| `CHAT_STT_PROVIDER` | `faster-whisper` | or `openai-compatible` |
| `CHAT_STT_API_URL` / `CHAT_STT_API_KEY` | empty | Cloud STT, off |
| `COMMERCIAL_DEV_ENTITLEMENTS` | empty | Dev stub, e.g. `voice_transcription` |
| `COMMERCIAL_DEV_PREMIUM` | `0` | Dev stub alias |
| `COMMERCIAL_ENTITLEMENT_CACHE_TTL` | `60` | Seconds |

## Merge notes vs PR #5 and the editing PR

This PR **targets `cursor/telegram-round-notes-82e2` (open PR #3)**, not `main`.
PR #2 is already on `main`. Do not merge this into `main` until #3 lands (or
rebase onto whichever chat model wins).

| | **#3 notes (this stack)** | **#5 link previews** | **Editing agent (on #5)** |
|---|---|---|---|
| Store | File-backed JSONL `ChatStore` | In-memory `MessageStore` | same as #5 |
| Discriminator | `Message.type` | `ChatMessage.kind` | `kind` + `edited_at` |
| UI | `/chat` | `#previewChat` on `/` | preview pane |
| `GET /api/chat` | live notes module | **501** | 501 |
| Transcripts | this PR, on the note row | n/a | n/a |

`to_public()` also emits `kind` as an alias of `type` so a future unified model
can read either name.

**Suggested merge order**

1. Land #5 on `main` if previews should ship independently (lowest chat coupling).
2. Land #3, then **this PR** (transcription is a delta on notes).
3. Rebase the editing PR onto the unified chat model. Edits apply to **text**;
   notes stay immutable except transcript fill + delete. Do not put
   `edited_at` on STT fields.
4. Unification pass: one `Message` with `kind`/`type` synonym, one store, one
   `/api/chat`. Keep `redact_for_admin()` on any admin export. Keep
   `POST .../transcribe` paths.

Overlapping files with #5: `talking/server.py`, `talking/chat/__init__.py`,
`talking/config.py`, `talking/security.py`, `talking/static/index.html`,
docs, `.env.example`. Transcription-only files (`talking/chat/transcribe.py`,
`docs/VOICE_TRANSCRIPTION.md`, `tests/test_voice_transcription.py`) do not
exist on #5.

The editing PR should treat `transcript_*` as system-filled, not user-editable.
Re-unfurl on text edit must not wipe a note transcript (different `kind`/`type`).
