# Talking

Real-time two-way speech translation. **Open core, GPL v3.**

Copyright (C) 2026 hateaboutyouone. Contact: **hateaboutyou111@icloud.com**.

Stage **v0.1.0** is a runnable skeleton: FastAPI + WebSocket, Whisper STT, Grok/free/stub translation, 3-minute buffer, offline JSONL queue, mobile-first HTML mic UI. A thin **chat notes** slice (`/chat`) adds Telegram-style voice notes and round video notes (кружочки), with **on-demand Premium transcription** (local faster-whisper; gated via the commercial API). **Voice translation** (`/translate`) adds two press-and-hold modes: live conversation (partial + final) and record-for-translation. A **link-preview** module unfurls URLs server-side (Open Graph / Twitter Card / oEmbed) with SSRF guards, a Telegram-style card, and a sandboxed mini-browser. Own **text messages can be edited** (PATCH, «изменено» / edited, 48 h window). Full chat, games, auth, and all monetization ship as later modules (`docs/ROADMAP.md`). Commercial admin / licensing / Talk-coins / P2P / analytics live in a **separate proprietary repo** and talk to this core **only over HTTP/WS** — see `docs/ARCHITECTURE.md` and `NOTICE.md`.

This README is not legal advice.

## Quick start (local, no keys)

Python 3.12:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # leave keys empty
python main.py serve          # http://127.0.0.1:8000
```

Phone on the same Wi-Fi (browsers require HTTPS for `getUserMedia`):

```bash
python main.py serve --self-signed
```

Open `https://<lan-ip>:8000`, accept the warning, tap the mic. Default TTS is the **browser Web Speech API**.

Tests (fake STT + stub translator, no model download):

```bash
pytest -q
# notes / preview smoke uses Playwright Chromium + fake media devices:
python -m playwright install chromium   # once
```

## Docker

```bash
docker compose up --build
# app :8000  postgres :5432  redis :6379
# optional S3 (MinIO): docker compose --profile s3 up
```

Compose sets `DATABASE_URL` and `REDIS_URL`. The app still runs if those pings fail in a bare `python main.py serve` with empty URLs. Image default Whisper model is `base` so a laptop CPU can boot; override with `WHISPER_MODEL=large-v3`.

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `XAI_API_KEY` | empty | Grok translation. Empty → free Google fallback → stub |
| `XAI_MODEL` | `grok-4-fast-non-reasoning` | Override if xAI retires the id |
| `APP_TRANSLATE_PROVIDERS` | `grok,google,stub` | Chain |
| `WHISPER_MODEL` | `large-v3` | `base` / `small` / `medium` / `large-v3` |
| `APP_PAUSE_MS` | `800` | VAD pause 300–3000 |
| `APP_MAX_SEGMENT_S` | `18` | Utterance cap (15–20 s) |
| `APP_BUFFER_SECONDS` | `180` | Live + offline ring |
| `APP_TTS` / `APP_TTS_PROVIDERS` | off / empty | Default = browser. Set `gtts` for MIT server TTS |
| `DATABASE_URL` | empty | Postgres/Neon. Empty = local files. Required for `AUTH_MODE=accounts` in production |
| `REDIS_URL` | empty | Optional. Unfurl cache and auth rate limits use Redis when set, else in-memory |
| `AUTH_MODE` | `local` | `local` = single-user (dev/tests). `accounts` = cookies + CSRF. Refused in production if `local` |
| `PUBLIC_ORIGIN` | empty | Canonical https origin; CORS credentials allowlist with `APP_CORS_ORIGINS` |
| `LOG_SALT` | empty | HMAC salt for user ids in logs. Required in production |
| `DB_ENCRYPTION_KEY` | empty | AES-GCM for phone (and later email) at rest |
| `SMS_PROVIDER` | `console` | `console` = local/dev sink only (refused in production). `twilio` = Verify SMS (voice is explicit fallback). See `docs/AUTH.md` |
| `GOOGLE_CLIENT_ID_WEB` / `_ANDROID` / `_IOS` | empty | Google Sign-In (OIDC). Off unless set plus an HTTPS `GOOGLE_REDIRECT_URI`. Web also needs `GOOGLE_CLIENT_SECRET_WEB`. See `docs/AUTH.md` |
| `S3_ENDPOINT` `S3_BUCKET` `S3_ACCESS_KEY` `S3_SECRET_KEY` `S3_REGION` | empty | R2/MinIO/AWS archive. Empty = local JSONL |
| `COMMERCIAL_API_URL` / `COMMERCIAL_API_TOKEN` / `COMMERCIAL_WEBHOOK_SECRET` | empty | Proprietary service. Empty = stubs |
| `COMMERCIAL_DEV_ENTITLEMENTS` / `COMMERCIAL_DEV_PREMIUM` | empty / off | Local premium stub (ignored in production) |
| `CHAT_STT_PROVIDER` | `faster-whisper` | On-demand note STT. Cloud `openai-compatible` is off |
| `VOICE_TRANSLATE_LIVE_PREMIUM` | `0` | `1` gates live hold-to-talk on the commercial licence hook; default free |
| `APP_SECRET_KEY` | auto file | HMAC for cookies and WS tokens (do not commit). Required in production |
| `UNFURL_STUB` | `0` | `1` skips outbound HTTP (tests / demo cards) |
| `UNFURL_RATE_PER_MIN` | `20` | Unfurl API rate limit per IP |
| `CHAT_MSG_RATE_PER_MIN` | `10` | Spec: 10 messages / min |
| `CHAT_EDIT_WINDOW_S` | `172800` | Telegram-like edit window (seconds from send; `0` = unlimited) |
| `CHAT_EDIT_RATE_PER_MIN` | `20` | PATCH edits / IP |

Never commit `.env` or keys. GPL TTS (`edge-tts`, piper / espeak-ng) is listed in `requirements-optional.txt` and stays **disabled**.

## Using the UI

1. Pick target language and Whisper model (phones: `base` or `small`).
2. Set pause (default 800 ms).
3. Tap the mic. Client VAD cuts on pause; server VAD is the fallback.
4. Say «переведи на немецкий» to switch target locally (no API).
5. If the other person speaks the target language, speech is translated back.
6. Open `/chat` for кружочки: short tap on the record button toggles voice ↔ round video; hold to record; release to send; slide left to cancel; slide up to lock. Premium users tap **→А / Расшифровать** on a note to transcribe (see `docs/VOICE_TRANSCRIPTION.md`).
7. Open `/translate` for hold-to-talk voice translation: **Живой перевод** streams while the button is held (A↔B, auto-detect, optional TTS); **Запись** records then transcribes and translates (slide-left cancel). Optionally attach the clip to chat as a `voice_note`.
8. The **Чат** pane on `/` accepts a URL; Talking unfurls a preview card. Tap the card for the sandboxed mini-browser (or «Открыть снаружи»). Long-press / ⋮ / ↑ on your own text to **Изменить**; the bubble shows **изменено**.

## Hosting outline (expand later)

Not a production runbook yet — see `docs/ROADMAP.md` module **M12**.

1. Build the `Dockerfile` image; keep secrets in the orchestrator, not in layers.
2. **EKS** in Virginia (`us-east-1`), Frankfurt (`eu-central-1`), Tokyo (`ap-northeast-1`) behind **Global Accelerator**.
3. **Aurora PostgreSQL Global Database** + **PgBouncer**; **ElastiCache Redis**; **SQS** (or Redis streams) for offline replay and archive upload.
4. **HPA** on CPU and in-flight WebSocket sessions.
5. **Cloudflare** CDN + Registrar; origin allowlist; **R2** via `S3_*`.
6. GitHub Actions: tests on every PR. The `deploy` job is **stubbed** (`if: false`) until AWS/Cloudflare secrets exist.

## Open-core boundary

GPL in this repo: translation, buffer, (future) chat and games.

All rights reserved, **separate repo**, hateaboutyouone: admin, licensing, Talk-coins, P2P payments, analytics.

Contract: `docs/commercial/openapi-commercial-v1.yaml`. This run does not create the proprietary repository.

## Game SDK (embed translation in a third-party game)

Studios can send text or audio to Talking and get a translation in the player's language (no per-language voiceover). Clients live in `sdk/` (Python + JS; Unity/Unreal/Godot sketches) and are **intended MIT** — confirmation pending, see `sdk/NOTICE.md`. The server API is `GET/POST /api/sdk/v1/...` (GPL). Guide: `docs/sdk/README.md` (English) and `docs/sdk/README.ru.md`.

```bash
# placeholder key only
SDK_API_KEYS=demo:change-me APP_TRANSLATE_PROVIDERS=stub python main.py serve
export TALKING_SERVER_URL=http://127.0.0.1:8000 TALKING_API_KEY=change-me
python sdk/examples/python_game_demo/main.py
```

Never put real keys in source. Empty `SDK_API_KEYS` disables the SDK routes.

## Docs

- `docs/ARCHITECTURE.md` — folders and API boundary
- `docs/AUTH.md` — M10 sessions, CSRF, phone (Twilio Verify), Google OIDC
- `docs/VOICE_TRANSCRIPTION.md` — premium on-demand note STT, merge notes vs #5 / editing
- `docs/ROADMAP.md` — next PRs, including unverified vendor names
- `docs/sdk/` — game-embed SDK (EN + RU), plan, OpenAPI
- `docs/specs/OWNER_SPEC.md` — owner spec verbatim (human contribution)
- `NOTICE.md` — licence caveats, **not legal advice**
- `docs/GOOGLE_SCOPES.md` — Google OAuth checklist
- `docs/SECURITY_AUDIT.md` — seven layers (skeleton)
- `docs/CHAT_EDITING.md` — Telegram-like edits + merge notes for the notes PR
- `docs/CHAT_NOTES.md` — voice notes / кружочки
- `docs/VOICE_TRANSLATION.md` — live + record hold-to-talk, STT choice, merge notes vs transcription PR / #5 / #6

## Licence

[GNU GPL v3](LICENSE). Optional copyleft TTS extras are not part of the default build.
