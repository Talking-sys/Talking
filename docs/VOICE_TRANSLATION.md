# Voice translation (press-and-hold)

Two hold-to-talk modes on `/translate`, stacked on the Telegram notes slice
(PR #3, branch `cursor/telegram-round-notes-82e2`).

Copyright (C) 2026 hateaboutyouone. GPL v3 open core.

## Modes

| Mode | UI | Transport | Result |
|---|---|---|---|
| **Живой перевод** (live) | Hold (or lock) and speak | `WS /ws/translate/live` — PCM16 16 kHz | Partial + final events, big readable text, optional browser TTS of each **final** |
| **Запись** (record) | Hold, release to send, slide-left cancel | `POST /api/translate/record` | Original + translation; optional chat `voice_note` |

Gestures reuse PR #3’s FSM (`talking/chat/gestures.py` + `static/record-gestures.js`) with `enable_toggle=false` so a short tap does not switch to video. Slide-up still locks hands-free; 60 s cap auto-sends in record mode. Live sessions cap at `VOICE_TRANSLATE_LIVE_MAX_S` (default 120 s).

Two-person conversation: source picker **Авто** + target picker. Whisper’s detected language chooses the side. If the speaker used the *target* language, we translate back into `partner_lang` (the other side). **⇄** swaps the pair; auto-detect stays on the source picker.

## STT + translation choice

**Reuse, do not duplicate.**

| Piece | Where | Live | Record |
|---|---|---|---|
| faster-whisper / `FakeSTTEngine` | `talking/stt.py` | `WindowedWhisperSTT` (partial windows) | `transcribe_and_translate()` |
| Grok → Google → stub | `talking/translate.py` | each partial + final | once per clip |
| WAV/WebM decode | `talking/chat/notes.decode_pcm16k` | n/a (PCM on the socket) | record HTTP |
| Chat `voice_note` | `talking/chat/` | not used | optional `dialog_id` |

Live “streaming” is **VAD-chunked overlapping windows**, not a second recognizer:

1. Client VAD (same `vad.js` as `/`) sends PCM while voiced and `eou` on pause.
2. Every `VOICE_TRANSLATE_PARTIAL_INTERVAL_S` (default 0.8 s) the server re-decodes a trailing `VOICE_TRANSLATE_PARTIAL_WINDOW_S` (3 s) window → `partial`.
3. On `eou` / release / session limit the full utterance is decoded → `final`.

`StreamingSTT` (`feed` / `flush` / `reset`) is the plug point for a future cloud streaming STT (Deepgram, xAI realtime, …). The WebSocket event schema (`partial` / `final` / `speak_browser`) stays.

### Latency trade-offs

| Approach | First partial | Privacy | Keys | Notes |
|---|---|---|---|---|
| **Local faster-whisper windows (this PR)** | ~interval + decode (typically 200–800 ms on `base`/`small`; 1–3 s on `large-v3` CPU) | Audio never leaves the box; only translated *text* hits Grok/Google | Optional `XAI_API_KEY` | No extra vendor. Pick `WHISPER_MODEL=base` on phones. |
| Cloud streaming STT (future plug) | ~200–400 ms first token | Audio leaves the device | Vendor key | Implement `StreamingSTT`; do not fork the WS protocol. |
| Existing `/ws` pipeline | One shot after VAD pause (no partials) | Same as live | Same translator | Kept as the always-on mic on `/`. Live hold-to-talk is `/ws/translate/live`. |

Partials use `beam_size=1` (already the skeleton default) and `without_timestamps=True`. A full dual-decoder (tiny for partials, large for finals) was rejected to keep one model in RAM.

## Premium hook

**Default: live is free in the GPL core** (`VOICE_TRANSLATE_LIVE_PREMIUM=0`).

Set `VOICE_TRANSLATE_LIVE_PREMIUM=1` to require
`GET {COMMERCIAL_API_URL}/v1/license/status`. Entitled when:

- `entitlements` / `modules` / `features` contains `live_translate` (or `voice_translate`), or
- `tier` ∈ `{trial, paid, premium}`, or
- `entitled: true`.

If the commercial URL is empty while the flag is on, live is **denied** (`commercial_unconfigured`). Record-for-translation stays free.

`GET /api/translate` returns `live_allowed` / `live_reason` so the UI can hide the live tab. The WS handshake sends `error: not_entitled` and closes `1008`.

## Privacy

- Live PCM lives only in the current utterance buffer and is dropped on `eou`, `stop`, limit, or disconnect. Nothing is written under `data/`.
- Record without `dialog_id`: decode in memory, transcribe, translate, **drop the blob**. `audio_retained: false`.
- Record with `dialog_id`: stored as a normal chat `voice_note` (UUID name, magic-byte sniff, quota, play tokens) with `transcript` + `translation` filled. Same retention as PR #3 notes.
- TTS is the **browser** Web Speech API of the translated text. The core does not synthesise or store TTS audio.

## HTTP / WS

| Method | Path | Role |
|---|---|---|
| GET | `/translate` | Mobile-first UI (RU/EN) |
| GET | `/api/translate` | Modes, limits, entitlement, privacy blurb |
| POST | `/api/translate/record` | Multipart audio → transcript + translation; optional chat attach |
| WS | `/ws/translate/live?t=` | Same HMAC WS token as `/ws`. Binary PCM16. JSON `start` / `stop` / `eou` / `config` / `swap` / `ping` |

Outbound live events: `config`, `entitlement`, `started`, `stopped`, `partial`, `final`, `speak_browser`, `backpressure`, `limit`, `swap`, `notice`, `error`, `pong`.

Backpressure: if more than `VOICE_TRANSLATE_MAX_PENDING_AUDIO_S` (4 s) of PCM is waiting on a busy STT worker, new frames are dropped and `backpressure` is emitted. The outbound queue (`VOICE_TRANSLATE_MAX_QUEUE`, 64) drops **partials** first when full; finals/limit/error are kept.

Rate limits: `VOICE_TRANSLATE_LIVE_CONNECTS_PER_MIN` (20), `VOICE_TRANSLATE_RECORD_PER_MIN` (20), plus the existing per-connection audio/control buckets.

## Merge notes

This PR **targets `cursor/telegram-round-notes-82e2` (open PR #3)**.

### vs the premium on-demand transcription PR (parallel, on top of #3)

That work adds `POST /api/chat/messages/{id}/transcribe` with a commercial entitlement check. Do **not** add a second Whisper path.

| This PR | Transcription PR should |
|---|---|
| `talking.voice_translate.pipeline.transcribe_and_translate` | Call it (pass `load_stt=True` after the entitlement check) |
| `talking.chat.notes.attach_transcript` stays **skip-if-STT-not-ready** so a note upload never pulls `large-v3` | Keep that for the upload path; on-demand transcribe is the “load it now” button |
| Live WS never persists audio | Unrelated |
| Record with `dialog_id` already fills `transcript` / `translation` | Skip re-transcribing notes that already have `transcript_status=ok` |
| Entitlement helper `live_translate_allowed` is **live-mode only** | Use a separate `transcribe` entitlement name so record/live/on-demand can be sold independently |

Shared files to watch: `talking/chat/notes.py`, `talking/chat/models.py`, `talking/chat/routes.py`, `talking/stt.py`, `talking/commercial/client.py`.

### vs PR #5 (link previews) and PR #6 (message editing)

#5 and #6 sit on a parallel in-memory chat (`kind` field, unfurl, `edited_at`). This branch uses PR #3’s file-backed store and `Message.type` (`text` / `voice_note` / `video_note`). We **did not** add a new message type.

When those land:

1. Keep one `ChatStore` / one `/ws/chat` / one `ChatHub` (PR #3).
2. Copy #5’s `kind` / preview card onto `Message.to_public()`; voice notes have no URL to unfurl.
3. Copy #6’s `edited_at` / `edit_count`; voice notes stay non-editable (already 422 in #6).
4. Our record-mode attach writes the same `transcript` / `translation` / `src_lang` / `tgt_lang` fields #3 already has. #6 clears those on a **text** edit — do not clear them for notes.
5. Do not merge the in-memory preview chat and the file-backed notes store blindly; map fields, don’t replace `talking/chat/store.py`.

## Env

| Variable | Default | Meaning |
|---|---|---|
| `VOICE_TRANSLATE_LIVE_PREMIUM` | `0` | Gate live WS on commercial licence |
| `VOICE_TRANSLATE_LIVE_MAX_S` | `120` | Max hold/lock session |
| `VOICE_TRANSLATE_PARTIAL_INTERVAL_S` | `0.8` | Partial decode cadence |
| `VOICE_TRANSLATE_PARTIAL_WINDOW_S` | `3` | Trailing window for partials |
| `VOICE_TRANSLATE_RECORD_MAX_MS` | `60000` | Record clip cap (same as notes) |
| `VOICE_TRANSLATE_LIVE_CONNECTS_PER_MIN` | `20` | WS connect bucket |
| `VOICE_TRANSLATE_RECORD_PER_MIN` | `20` | Record POST bucket |
| `VOICE_TRANSLATE_MAX_PENDING_AUDIO_S` | `4` | Incoming PCM backpressure |
| `VOICE_TRANSLATE_MAX_QUEUE` | `64` | Outbound WS event queue |

## Tests

- `tests/test_voice_translate_pipeline.py` — direction, swap, windowed STT, skip-if-not-ready
- `tests/test_voice_translate_live.py` — WS partial/final, A↔B + swap, session limit, backpressure, token, premium hook
- `tests/test_voice_translate_record.py` — no-persist, chat attach, validation, rate limit, `/ws/chat` push
- Gesture scripts in `tests/fixtures/gesture_scripts.json` (`enable_toggle: false`)
- `tests/test_playwright_translate.py` — Chromium fake mic, both modes, screenshots
