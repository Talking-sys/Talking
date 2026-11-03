# Talking

Real-time two-way speech translation. **Open core, GPL v3.**

Copyright (C) 2026 hateaboutyouone. Contact: **hateaboutyou111@icloud.com**.

Stage **v0.1.0** is a runnable skeleton: FastAPI + WebSocket, Whisper STT, Grok/free/stub translation, 3-minute buffer, offline JSONL queue, mobile-first HTML mic UI. Chat, games, auth, and all monetization ship as later modules (`docs/ROADMAP.md`). Commercial admin / licensing / Talk-coins / P2P / analytics live in a **separate proprietary repo** and talk to this core **only over HTTP/WS** — see `docs/ARCHITECTURE.md` and `NOTICE.md`.

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
| `DATABASE_URL` | empty | Postgres/Neon. Empty = local files |
| `REDIS_URL` | empty | Optional |
| `S3_ENDPOINT` `S3_BUCKET` `S3_ACCESS_KEY` `S3_SECRET_KEY` `S3_REGION` | empty | R2/MinIO/AWS archive. Empty = local JSONL |
| `COMMERCIAL_API_URL` / `COMMERCIAL_API_TOKEN` / `COMMERCIAL_WEBHOOK_SECRET` | empty | Proprietary service. Empty = stubs |
| `APP_SECRET_KEY` | auto file | HMAC for WS tokens (do not commit) |

Never commit `.env` or keys. GPL TTS (`edge-tts`, piper / espeak-ng) is listed in `requirements-optional.txt` and stays **disabled**.

## Using the UI

1. Pick target language and Whisper model (phones: `base` or `small`).
2. Set pause (default 800 ms).
3. Tap the mic. Client VAD cuts on pause; server VAD is the fallback.
4. Say «переведи на немецкий» to switch target locally (no API).
5. If the other person speaks the target language, speech is translated back.

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

## Docs

- `docs/ARCHITECTURE.md` — folders and API boundary
- `docs/ROADMAP.md` — next PRs, including unverified vendor names
- `docs/specs/OWNER_SPEC.md` — owner spec verbatim (human contribution)
- `NOTICE.md` — licence caveats, **not legal advice**
- `docs/GOOGLE_SCOPES.md` — Google OAuth checklist
- `docs/SECURITY_AUDIT.md` — seven layers (skeleton)

## Licence

[GNU GPL v3](LICENSE). Optional copyleft TTS extras are not part of the default build.
