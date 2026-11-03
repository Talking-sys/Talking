# Talking game-embed SDK — plan

Copyright (C) 2026 hateaboutyouone. Contact: hateaboutyou111@icloud.com.

This document is the roadmap for the **game SDK**: a way for third-party studios to embed Talking instant translation so they do not have to voice or localise every language. It is not legal advice.

Status of this PR: **skeleton**. Python + JS clients and `/api/sdk/v1` are callable. Unity / Unreal / Godot are interface sketches. Token streaming, key issuance UI, and on-device packs are later work.

## Goals

1. Game passes **text** (subtitle, NPC line, chat bubble) or **audio** (voice line, player mic clip).
2. Talking returns **translated text** in the player's language, and optionally **synthesised audio**.
3. Studios keep one source voice / one source script.
4. Closed-source games can **embed the client** (intended MIT — see `sdk/NOTICE.md`) while the server stays GPL.

## What ships in this skeleton

| Layer | Where | Notes |
|---|---|---|
| HTTP API v1 | `talking/sdk_api.py` → `/api/sdk/v1` | text, audio, languages, SSE envelope |
| API-key auth + per-key rate limit | `SDK_API_KEYS`, `SDK_RATE_LIMIT_PER_MIN` | Bearer or `X-Talking-Api-Key` |
| Python client | `sdk/python` | sync + async, retries, typed errors |
| JS / TS client | `sdk/js` | ESM + `.d.ts`, AbortSignal |
| Engine sketches | `sdk/bindings/{unity,unreal,godot}` | same method names, not shippable plugins |
| Examples | `sdk/examples/` | tavern text loop + browser form |
| Docs | `docs/sdk/` EN + RU | this file, README, OpenAPI |

Game SDK traffic **does not** write the phone user's 3-minute rolling buffer, chat, or history. It reuses `Translator`, `STTEngine`, and `TTSEngine` only.

## Public client API (stable intent)

```
init(server_url, api_key, app_id?, timeout?, offline?, max_retries?)
set_target_language(code) / get_target_language()
list_languages() -> from server (Whisper list; not invented)
translate_text(text, source='auto', target=?, return_audio=false)
translate_audio(bytes|stream|path, format, source='auto', target=?, return_audio=false)
translate_text_stream(...)  # SSE envelope; v1 = one final event
```

Errors: auth, rate limit, invalid request, unsupported language/format, payload too large, no speech, translator/STT down, timeout, offline.

Retries: 429 / 502 / 503 / 504 / transport, honour `Retry-After`. Do not retry 400/401/413.

Secrets: API key only in `Authorization`; never in URLs, logs, `repr`, or examples.

## Engine bindings plan

### Unity (C#)

- Package as `com.talking.sdk` (UPM git URL) wrapping `UnityWebRequest`.
- `async Task` API matching `ITalkingClient` in `sdk/bindings/unity/TalkingClient.cs`.
- Audio: `AudioClip` → 16 kHz mono WAV bytes; optional `AudioClip` from returned MP3/WAV.
- Streaming: `UnityWebRequest` download handler that parses SSE.
- Ship a sample scene (NPC dialogue balloon).
- IL2CPP / WebGL: WebGL must use CORS (`APP_CORS_ORIGINS`) and cannot always POST files — JSON `audio_base64` is already accepted.

### Unreal (C++)

- Plugin module `TalkingSdk` using `FHttpModule`.
- Blueprint function library mirroring `TranslateText` / `TranslateAudio`.
- `USoundWave` capture for audio; return `USoundWave` when `return_audio`.
- Retry with `FTimerHandle`; log category `LogTalkingSdk` that strips Authorization.

### Godot 4

- Addon `addons/talking_sdk` with the GDScript class plus a C# variant for Godot-mono.
- `HTTPRequest` + `HTTPClient` for streaming.
- `AudioStreamWav` in, `AudioStreamMP3` out.

Until those packages exist, engines HTTP-call `/api/sdk/v1` or run the Python client as a local sidecar.

## Streaming design

v1 SSE (`POST /translate/text/stream`):

```
event: status
data: {"stage":"translating","request_id":"..."}

event: translation
data: { ... Translation JSON ... }

event: done
data: {"request_id":"..."}
```

Later:

- Token streaming from Grok (delta events) when the provider supports it.
- Audio streaming: chunked upload + partial transcripts (`event: partial`).
- WebSocket `/api/sdk/v1/ws` for live player voice (separate from the phone `/ws` session).

Clients already expose stream methods so studios can adopt the envelope without a breaking change.

## Offline / timeouts

- Client `offline=True`: no network; `list_languages` works only from cache; translate raises `OfflineError`.
- Server `translator.force_offline`: HTTP 503 `translation_unavailable`.
- Default client timeout 15 s (matches `APP_TRANSLATE_TIMEOUT`). Audio STT may use a longer server cap.

On-device MT packs are **out of scope** for v1 (see open decisions).

## Auth and keys (later)

v1 keys are static strings in `SDK_API_KEYS`. A commercial dashboard (separate proprietary repo) should later:

- Issue hashed keys, rotate, revoke.
- Bind app_id, bundle id, origin allowlist.
- Quotas (characters / minutes / day) in addition to the in-process token bucket.

Do not put long-lived keys in a shipped game binary. Prefer a studio backend that mints short-lived tokens (future endpoint).

## Roadmap

| Step | Scope |
|---|---|
| **This PR** | Skeleton API + Python/JS + docs + tests |
| S2 | Token SSE from Grok; mp3/ogg/webm decode; real TTS round-trip tests |
| S3 | Unity UPM package + sample scene |
| S4 | Unreal plugin + Blueprint nodes |
| S5 | Godot addon |
| S6 | Signed short-lived player tokens (commercial service) |
| S7 | Optional on-device fallback (studio-supplied model; not GPL-incompatible weights in this repo) |

## Open decisions (need owner / counsel)

1. **Dual MIT/GPL split** — pending hateaboutyouone confirmation (`sdk/NOTICE.md`).
2. Should SDK audio be archived to S3/JSONL for studio audit, or remain stateless? (v1 = stateless.)
3. Per-app default target language vs client-only `set_target_language`.
4. Publish to PyPI / npm under which org name?
5. Short-lived player tokens vs studio-held keys in game clients.
6. Character quotas vs request rate limits for paid tiers.
7. Whether browser games may call `/api/sdk/v1` directly (CORS) or must proxy via the studio backend (recommended: proxy, so the key never ships).
8. MP3/Ogg/WebM in v1 vs wav/pcm only (v1 = wav / pcm_s16le / pcm_f32le).
9. Target branch for this work: stacked on Stage 1 (`cursor/stage1-skeleton-4358`) until that PR merges, then rebase onto `main`.
