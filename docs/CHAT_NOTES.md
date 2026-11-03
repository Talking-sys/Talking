# Chat notes slice (кружочки)

Telegram-style voice notes and round video notes. This is **not** ROADMAP **M4**
(full chat). It is the smallest GPL chat plumbing the notes feature needs, so M4
can extend it instead of replacing it.

## What shipped

| Piece | Where | M4/M5 should |
|---|---|---|
| Dialog + message file store | `talking/chat/store.py` | Swap JSONL for Postgres; keep `to_public()` |
| `text` / `voice_note` / `video_note` | `talking/chat/models.py` | Add stickers, files, geo, polls as new `type`s |
| Magic-byte sniff, UUID names, 500 MB quota | `talking/chat/media.py` | Reuse for gallery/files (M5) |
| Expiring play tokens + one-time downloads | `DownloadTokens` | Keep; never serve blobs without a token |
| Hold / slide-left cancel / slide-up lock FSM | `talking/chat/gestures.py` + `static/record-gestures.js` | Do not fork the constants |
| HTTP + `/ws/chat` | `talking/chat/routes.py` | Add nicks, block, 10/min stays |
| Optional STT + translation under the note | `talking/chat/notes.py` | Same engine as live translation |

Auth is **M10** (`docs/AUTH.md`). Dialogs are owned by `current_user`. Groups
(M6 slice) share the same store — see `docs/CHAT_GROUPS.md`. Admin must not
grow a read API here.

## Gestures (Telegram)

One record button next to the composer.

1. Short tap toggles **voice** ↔ **round video**.
2. Press-and-hold (220 ms) starts recording.
3. Release sends.
4. Slide left and release cancels.
5. Slide up past the lock threshold locks hands-free; Stop previews, Send sends.
6. Hard cap **60 s** auto-sends.

Video is `getUserMedia` + `MediaRecorder`, square-cropped on a canvas when
`captureStream` exists, shown as a circle with a progress ring. Voice notes show
a waveform bubble. Circles may autoplay **muted** while on screen.

## Transcripts

Transcription is **on-demand and premium-gated** (Telegram Premium-style
«Расшифровать»). Upload does **not** run STT. See `docs/VOICE_TRANSCRIPTION.md`.

`CHAT_TRANSCRIBE_ON_UPLOAD=1` restores the old auto-fill path but still requires
`has_entitlement(user, "voice_transcription")`.

Press-and-hold **voice translation** (live conversation + record-for-translation) lives on `/translate` and reuses this FSM, `decode_pcm16k`, and the same `transcript` / `translation` fields. See `docs/VOICE_TRANSLATION.md`.

## Limits (owner spec)

- Notes ≤ 60 s
- Voice file ≤ 8 MB, video file ≤ 25 MB (files-in-chat cap)
- 500 MB / user
- 10 messages / minute
- Text ≤ 500 characters
- MIME decided by **magic bytes**, not the client `Content-Type`
- Stored name is a UUID; client filenames are ignored (dangerous extensions rejected)
