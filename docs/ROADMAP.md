# Roadmap — one module = one future PR

Stage 1 (this PR / tag **v0.1.0**) is the runnable skeleton: folders, FastAPI + WebSocket, HTML mic + feed, buffer, local commands, Docker, tests, CI stub, open-core boundary.

Do **not** implement the rest in the same PR. Each row below is a later pull request.

Unverified or risky third-party names are marked **⚠**. Do not invent APIs for them — confirm the current product id at implementation time.

## Done in Stage 1

- GPL v3 tree, NOTICE, architecture boundary, commercial HTTP stubs
- Python 3.12 FastAPI + `/ws`, env-based config, keyless local run
- Whisper selector `large-v3` / `medium` / `small` / `base` (default large-v3; Docker uses `base`)
- Grok if `XAI_API_KEY`, else free Google, else stub
- Browser Web Speech API TTS; gTTS optional; GPL TTS extras off
- Client VAD (energy / Silero-shaped API) pause 300–3000 ms default 800, segment ~18 s; server VAD fallback
- 3-minute rolling buffer + JSONL offline queue
- Local voice commands (`переведи на немецкий`)
- docker-compose: app, postgres, redis, optional minio; healthchecks; self-signed HTTPS flag
- pytest: server, WS (fake STT/translator), buffer, commands
- GitHub Actions tests; deploy-on-push **disabled** (`if: false`)

## Ordered modules

| ID | PR title | Scope | Notes / ⚠ |
|---|---|---|---|
| **M2** | Whisper production defaults + ONNX/mobile path | Lazy model download, `base`/`small` default on phones, CTranslate2 int8, disk ring buffer under memory pressure | large-v3 is RAM-heavy on CPU; keep selector |
| **M3** | Silero ONNX in the browser | Vendor or fetch MIT Silero VAD ONNX + onnxruntime-web; keep energy fallback | Stage 1 uses energy VAD with a Silero-shaped interface |
| **M4** | Chat core (GPL) | Unique nicks (30-day rename), prefix search, WS messages ≤500 chars, block, 10/min rate limit, instant translation, “show original”, command filter, 3-minute server auto-delete after translation | Admin must **not** read dialogs. Auth is a later module. **Notes + previews + edits + on-demand premium transcription already landed** on one file-backed `ChatStore` / `Message` (`kind` + `type` alias, `/ws/chat`). **Voice translation** (`/translate`, live WS + record HTTP) is a separate slice on top of notes. Extend `talking/chat/`, do not replace. See `docs/CHAT_NOTES.md`, `docs/CHAT_EDITING.md`, `docs/VOICE_TRANSCRIPTION.md`, `docs/VOICE_TRANSLATION.md`. |
| **M5** | Chat attachments + stickers | Camera, gallery, files ≤25 MB, UUID names, 500 MB/user, dangerous-extension denylist, original sticker packs in `assets/` | ZIP admin upload is **commercial** (admin). Reuse `talking/chat/media.py` (magic bytes, quota, download tokens). |
| **M6** | Groups, polls, geo | Groups ≤20 (trial ≤5 is commercial), roles, invite links, live location 15m/1h/8h, Nominatim geocoding, voice addresses | Nominatim requires a valid User-Agent with contact |
| **M7** | Mini-browser, OCR, AI assist | Sandboxed iframe (no JS/data: URLs), Tesseract local OCR, selection menu (copy/cut/paste/translate/ask LLM) | ⚠ Spec says **Qwen-OCR** for Arabic/Asian. Public APIs today are Alibaba **Qwen-VL-OCR** / **qwen3.5-ocr** (`qwen-vl-ocr-2025-11-20`). Confirm the exact Model Studio id. Do not assume a model literally named `Qwen-OCR`. **Link-preview PR already shipped** the mini-browser chrome (`talking/static/link-preview.js`) and server unfurl (`talking/unfurl/`). Extend, do not fork. |
| **M8** | Games | Chess, checkers, giveaway checkers, short/long/classic backgammon, kalah, Ludo (чинжик), Sic Bo; Elo four ladders; bots 3 levels; spectators; drawer UI | Games award **rating points only**, never Talk-coins. Sic Bo table chips are a separate counter configured by **admin** (commercial) |
| **M9** | Language router | Whisper for European; dedicated engines per family | ⚠ **Qwen3-ASR** exists (Apache-2.0, `Qwen/Qwen3-ASR-1.7B` & `0.6B`) — OK to use as a future optional engine. ⚠ **Naver CLOVA CSR** exists (REST, Kor/Eng/Jpn/Chn, 60 s cap, paid NCP keys). ⚠ **Meta MMS** exists for sw/ha/yo but weights are **CC-BY-NC-4.0** — **do not vendor into this GPL repo**; call an external service or keep it out of combined distribution. ⚠ Asian MT: DeepSeek **V4.1 Flash** exists; API model id is `deepseek-flash` (not the marketing string “DeepSeek V4.1 Flash”). ⚠ Arabic MT: spec “Qwen/Command R Plus” is ambiguous. Qwen chat models exist; **Command R+** is Cohere (current ids may be Command A / R7B — verify). RTL UI required. |
| **M10** | Auth (GPL core + commercial flags) | Google (openid, email, profile only), Apple equal prominence, **email magic link/code 10 min hashed** (stage 2 shipped: SMTP production + console sink local/dev only and refused in production; codes and links single-use; no auto-merge), passkeys/WebAuthn, anonymous accounts, **phone SMS via Twilio Verify** (owner add-on 2026-11-03; overrides the earlier “no SMS v1” line; console sink is local/dev only and refused in production; voice call is an explicit “call me instead” fallback, never the default; no email delivery of phone codes), no auto-merge on email or phone | Checklist: `docs/GOOGLE_SCOPES.md`. Stages 0–2 + phone: `docs/AUTH.md`. Remaining: Google, passkeys, Apple, onboarding, deletion. OAuth client per platform |
| **M11** | Commercial service (separate repo) | Admin (token in header or one-time cookie form, **never in URL**), licensing trial/paid, Talk-coins two balances, P2P, analytics, ads timers, Stripe mock, Apple Pay / PayPal / Blink Lightning, IAP on iOS | **New repo, All rights reserved, hateaboutyouone.** Core only sees HTTP. Trial: 1 h / 24 h rolling window, ads every 15 min (two 30 s clips, skip at 15 s, server-timed). This Stage 1 repo must not grow that code. |
| **M12** | Hosting runbook | EKS three regions (Virginia, Frankfurt, Tokyo) + Global Accelerator, Aurora Global, PgBouncer, HPA, SQS/Redis queues, Cloudflare CDN + Registrar | README has an outline only; expand here. CI deploy-on-push stays disabled until secrets exist |
| **M13** | Security hardening | AES-256-GCM for subscription/balance fields (`DB_ENCRYPTION_KEY`), optional pgcrypto, hashed user_id in logs, session TTL, 2FA admin, webhook signatures, no card storage, re-encode + strip EXIF, one-time download tokens, SSRF header checks, BLE X25519+AES-GCM + 6-digit pair, one-time QR, signed licences (public key in build) | Fill `docs/SECURITY_AUDIT.md` seven layers + pip-audit/bandit in CI as gating |
| **M14** | Kivy native client | Second-stage mobile app; BLE phone exchange | Web remains the v1 client |
| **M15** | Profile, onboarding, UI chrome | Telegram-like settings (bg, 12–24 px font, accent), chat wallpapers, logo variants, onboarding buttons, bonuses-for-hours, newbie AI agent, gift grid | Gifts/Talk-coins economy is commercial (M11); GPL core only renders what the API returns |
| **M16** | Legal pages for counsel | Expand privacy/terms templates; data-deletion request path | Templates exist in Stage 1; not legal advice |
| **M17** | Game embed SDK (this module) | `/api/sdk/v1`, MIT Python/JS clients, engine sketches, EN+RU docs | Dual MIT/GPL pending owner confirmation. Do not mix with in-app games (M8) or chat (M4). Follow-ups in `docs/sdk/PLAN.md` |

## Hosting outline (expand in M12)

1. Container image from `Dockerfile`, no secrets in layers.
2. EKS in us-east-1, eu-central-1, ap-northeast-1; AWS Global Accelerator anycast.
3. Aurora PostgreSQL Global Database; PgBouncer in front; Redis (ElastiCache) for ephemeral state.
4. HPA on CPU / in-flight WS sessions; SQS (or Redis streams) for offline replay and archive upload.
5. Cloudflare in front (TLS, CDN, Registrar for the domain); origin only accepts Cloudflare / GA.
6. Object storage: Cloudflare R2 (S3 API) via `S3_*`.
7. GitHub Actions: tests on every PR; deploy-on-push to `main` **after** OIDC/AWS secrets exist (job currently `if: false`).
