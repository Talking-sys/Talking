# Talking SDK for games

Copyright (C) 2026 hateaboutyouone. Contact: hateaboutyou111@icloud.com.

Embed **instant translation** in a game: the studio ships **one** script and **one** voice. At runtime the game sends text or audio to Talking and gets the line in the player's language (and, optionally, new speech audio).

Русская версия: [`README.ru.md`](README.ru.md). Plan and engine bindings: [`PLAN.md`](PLAN.md). HTTP contract: [`openapi-sdk-v1.yaml`](openapi-sdk-v1.yaml).

The **client** libraries live under `sdk/` and are **intended MIT** (closed-source games can embed them). Confirmation of that split is **pending** the copyright holder — see `sdk/NOTICE.md`. The **server** stays GNU GPL v3. This page is not legal advice.

## What you get

| Call | Input | Output |
|---|---|---|
| `list_languages` | — | Whisper language list from the server (same codes as the Talking app) |
| `translate_text` | string, `source` (default `auto`), `target` | translated string, provider, timing; optional TTS audio |
| `translate_audio` | WAV / PCM bytes, stream, or file | recognised text + translation; optional TTS audio |
| `translate_text_stream` | same as text | SSE envelope (v1: one final translation event) |

`set_target_language` / `get_target_language` store a **client default** so each call does not repeat `target=`. There is no server-side game session in v1.

## Install

Python (from a repo checkout):

```bash
pip install -e sdk/python
```

JavaScript: copy `sdk/js` or `npm install ./sdk/js` (package name `talking-sdk`). PyPI / npm publish is not done in this skeleton.

Unity / Unreal / Godot: interface sketches in `sdk/bindings/` — call HTTP until those plugins exist.

## Init

**Never put an API key in source, screenshots, or logs.**

```python
import os
from talking_sdk import TalkingClient

client = TalkingClient(
    server_url=os.environ["TALKING_SERVER_URL"],
    api_key=os.environ["TALKING_API_KEY"],
    app_id=os.environ.get("TALKING_APP_ID", "my-game"),
    timeout=15.0,          # seconds
    max_retries=3,
    offline=False,         # True → no network; cached languages only
    target_language="es",
)
```

JavaScript:

```js
const client = new TalkingClient({
  serverUrl: process.env.TALKING_SERVER_URL,
  apiKey: process.env.TALKING_API_KEY,
  appId: process.env.TALKING_APP_ID,
  timeout: 15000,
  targetLanguage: "es",
});
```

Server side (Talking process):

```bash
# placeholder only — use a long random secret in production
SDK_API_KEYS=my-game:change-me
SDK_RATE_LIMIT_PER_MIN=60
APP_TRANSLATE_PROVIDERS=stub   # or grok,google,stub
```

Format of `SDK_API_KEYS`: `app_id:secret,other:secret2`. If empty, `/api/sdk/v1` returns **503** `sdk_disabled`.

Send `Authorization: Bearer <secret>` (or `X-Talking-Api-Key`). Optional `X-Talking-App-Id` must match the id bound to that secret.

## Translate

```python
client.set_target_language("de")
print(client.get_target_language())

for lang in client.list_languages():
    if lang.code == "de":
        print(lang.en, lang.ru)

line = client.translate_text("The bridge is out.", source="en")
print(line.translation, line.provider, line.elapsed_ms)

clip = client.translate_audio(open("line.wav", "rb"), format="wav", return_audio=False)
print(clip.text, "→", clip.translation)
```

`source='auto'`: text uses the translation provider's auto-detect; audio uses Whisper's detected language.

Async Python: `AsyncTalkingClient` (same methods, `await`). JS methods are already promises. Pass `signal: AbortSignal` in JS.

## Supported languages

The list is **not invented here**. It is Whisper's set in `talking/languages.py` (`WHISPER_LANGUAGES`), also served as `GET /api/languages` (public UI) and `GET /api/sdk/v1/languages` (authenticated SDK). Examples: `en`, `ru`, `de`, `fr`, `es`, `zh`, `ja`, `ko`, `ar`, `hi`, `pt`, `tr`, … (~100 codes). Always prefer `list_languages()` at runtime so a server upgrade cannot desync the game.

## Latency and limits (v1 skeleton)

| Limit | Default | Override |
|---|---|---|
| Text length | 4000 characters | `SDK_MAX_TEXT_CHARS` |
| Audio body | 5 MiB | `SDK_MAX_AUDIO_BYTES` |
| Audio formats | `wav`, `pcm_s16le`, `pcm_f32le` (16 kHz mono preferred) | — |
| Rate limit | 60 requests / minute / API key | `SDK_RATE_LIMIT_PER_MIN` |
| Translate timeout | 15 s | `APP_TRANSLATE_TIMEOUT` / client `timeout` |
| TTS | off unless `APP_TTS=1` and `APP_TTS_PROVIDERS=gtts` | `return_audio=true` then yields `audio` (base64) or `audio_error` |

Expect ~STT time + MT time on audio (hundreds of ms to a few seconds with stub/Grok; Whisper `large-v3` is slower on CPU). The stub translator (`APP_TRANSLATE_PROVIDERS=stub`) is instant and prefixes `[src→tgt]`.

HTTP **429** includes `Retry-After` and `error.retry_after`. Clients retry 429/502/503/504 automatically.

## Privacy

- SDK requests are **stateless**: they do not enter the 3-minute rolling buffer, chat, or per-user history.
- Utterance **text** is sent to the configured translation providers (Grok / Google fallback / stub). The GPL core's privacy template still applies: segment text without an account binding.
- The server **logs app id, hashed key id, language codes, and sizes — not the line of dialogue and not the API key**.
- Recommended production shape: the **game server** holds the Talking API key and proxies `/api/sdk/v1`. Do not ship long-lived keys in a client binary.
- Players should be told that voice clips leave the device when you call `translate_audio`.
- Contact for data requests: hateaboutyou111@icloud.com. Templates: `/privacy` on the server. Not legal advice.

## Errors (client)

| Type (Python) | Typical HTTP | Meaning |
|---|---|---|
| `AuthError` | 401 | missing / wrong key, or app id mismatch |
| `RateLimitError` | 429 | per-key bucket empty |
| `InvalidRequestError` | 400 / 415 / 422 | empty text, bad WAV, `no_speech` |
| `UnsupportedLanguageError` | 400 | code not in Whisper list |
| `PayloadTooLargeError` | 413 | over text/audio cap |
| `ServiceUnavailableError` | 503 | keys not configured, STT/MT down, forced offline |
| `TalkingTimeoutError` | 504 | deadline |
| `OfflineError` | — | client `offline=True` |
| `NetworkError` | — | transport failure after retries |

`repr(client)` and debug logs redact `Authorization`.

## Examples

- Python tavern loop: `sdk/examples/python_game_demo/`
- Browser form: `sdk/examples/browser/` (CORS: set `APP_CORS_ORIGINS` if the page is not on the Talking origin)

## Roadmap

See [`PLAN.md`](PLAN.md): Unity/Unreal/Godot packages, token SSE, short-lived player tokens, optional on-device packs.
