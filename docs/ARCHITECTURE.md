# Architecture — Talking open core (Stage 1)

Copyright (C) 2026 hateaboutyouone. Contact: hateaboutyou111@icloud.com.

Talking is an **open-core** product:

| Layer | Licence | Repository | Contents |
|---|---|---|---|
| Open core | GNU GPL v3 | `Talking-sys/Talking` (this repo) | Real-time translation, 3-minute buffer, offline JSONL queue, chat (later), games (later), WebSocket API, HTML/JS client |
| Commercial modules | All rights reserved, copyright holder **hateaboutyouone** | **Separate proprietary repo** (not created in this run) | Admin panel, licensing, Talk-coins, P2P payments, analytics |

The two codebases **must not be linked**. They talk only over an **open HTTP/WebSocket API**. The GPL core never `import`s proprietary packages, never vendors their source, and never ships their binaries.

This document is a technical description, **not legal advice**. Verify the boundary against current GPL v3 text, local law, and counsel before shipping a combined product. See `NOTICE.md`.

## Runtime (Stage 1)

```
Phone / browser (HTTPS)
  mic → AudioWorklet 16 kHz PCM
  client VAD (energy; Silero ONNX optional drop-in)
  Web Speech API TTS (default)
        │  WSS /ws  and  WSS /ws/translate/live (hold-to-talk partials)
        ▼
FastAPI (Python 3.12)
  Session → STT (faster-whisper) → local voice commands
         → Translator (Grok if XAI_API_KEY, else free Google, else stub)
         → RollingBuffer 180 s + OfflineQueue JSONL
         → optional S3-compatible archive
  LiveTranslateSession → WindowedWhisperSTT (VAD windows) → same Translator
  POST /api/translate/record → decode_pcm16k + transcribe_and_translate
        │  HTTP only
        ▼
Commercial service (optional, COMMERCIAL_API_URL)
  /v1/license /v1/analytics /v1/talk-coins /v1/payments /v1/admin
```

Works **locally with no keys**: stub translator, local JSONL/SQLite, no Postgres/Redis/S3 required. Docker Compose can attach Postgres, Redis, and optional MinIO.

## Plugin / API boundary

Core **exposes**:

- `GET /api/sdk/v1` — game-embed SDK info (API-key auth)
- `GET /api/sdk/v1/languages` — Whisper language list for game clients
- `POST /api/sdk/v1/translate/text` — text in, translation out (optional TTS)
- `POST /api/sdk/v1/translate/text/stream` — same, SSE envelope
- `POST /api/sdk/v1/translate/audio` — STT then translation (wav/pcm)
- `GET /api/commercial/status` — whether a commercial backend is configured
- `GET /api/commercial/license` — proxy/stub
- `GET /api/commercial/talk-coins` — proxy/stub
- `POST /api/commercial/checkout` — proxy/stub
- `GET /api/commercial/admin/health` — proxy/stub
- `POST /api/commercial/webhook` — inbound signed events from the proprietary service

Core **calls** (if `COMMERCIAL_API_URL` is set), never the other way around for business logic:

- `GET {COMMERCIAL_API_URL}/v1/license/status`
- `GET {COMMERCIAL_API_URL}/v1/license/entitlements` — premium flags such as `voice_transcription` (fail closed if unreachable)
- `POST {COMMERCIAL_API_URL}/v1/analytics/event`
- `GET {COMMERCIAL_API_URL}/v1/talk-coins/balance`
- `POST {COMMERCIAL_API_URL}/v1/payments/checkout`
- `GET {COMMERCIAL_API_URL}/v1/admin/health`

Contract: `docs/commercial/openapi-commercial-v1.yaml`. Implementation of those paths lives **only** in the proprietary repo.

If `COMMERCIAL_API_URL` is empty, every call returns a disabled stub (`{"ok": false, "disabled": true}`). The translator, buffer, and UI still run.

## What must not enter this repo

- Admin UI, licence key issuance/revocation, trial ads timers, Stripe/PayPal/Apple Pay live code, Talk-coins ledger, analytics warehouse, P2P nick transfers, secret signing keys.
- GPL TTS engines in the default extra (`edge-tts`, `espeak-ng` / piper / pyttsx3). They may exist only in `requirements-optional.txt`, off by default.

## Folder map

```
talking/                 GPL open core package
  server.py              FastAPI + WebSocket
  sdk_api.py             /api/sdk/v1 game-embed HTTP (keys, rate limit)
  engine.py              STT → command → translate → buffer
  stt.py / translate.py / tts.py / vad.py
  buffer.py / offline_queue.py / archive.py
  commands.py / languages.py
  auth/                  sessions, cookies, anonymous product limits + phone (M10)
  commercial/            HTTP client + stubs (no proprietary code)
  chat/                  unified store (notes + text + edits + on-demand premium STT); M4 extends this
  voice_translate/       press-and-hold live WS + record HTTP (reuses STT/translator)
  unfurl/                SSRF-safe Open Graph / Twitter / oEmbed unfurl + image proxy
  games/                 placeholder (M8)
  static/                mobile-first HTML/JS (+ chat.html, link-preview.js / mini-browser)
sdk/                     intended MIT client (PENDING owner confirmation)
  python/  js/  bindings/  examples/  LICENSE  NOTICE.md
docs/sdk/                game SDK guide (EN + RU), plan, OpenAPI
docs/specs/              owner spec, human contribution evidence
```

## Data stores

| Need | Stage 1 | Later |
|---|---|---|
| 3-minute live buffer | SQLite + WAV on disk | same |
| Offline queue | JSONL ring, delete after ACK | same |
| History > 3 min | local JSONL; S3 if `S3_*` set | Cloudflare R2 |
| Accounts / chat | file-backed dialogs + notes (`data/chat/`); auth tables via Alembic when `AUTH_MODE=accounts` | Postgres (Neon / Aurora) in M4 for chat; auth is M10 (`docs/AUTH.md`) |
| Rate limits / pubsub | in-process | Redis |
| Link-preview cache | in-process; Redis if `REDIS_URL` | same |
