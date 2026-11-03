# talking-sdk (Python)

MIT client (see `sdk/NOTICE.md` — dual-licence pending owner confirmation).

```bash
pip install -e sdk/python
```

```python
import os
from talking_sdk import TalkingClient

with TalkingClient(os.environ["TALKING_SERVER_URL"], os.environ["TALKING_API_KEY"]) as t:
    t.set_target_language("ja")
    print(t.list_languages()[:3])
    print(t.translate_text("Hello", source="en").translation)
    # audio: bytes, path, file object, or iterator of chunks
    # print(t.translate_audio("line.wav", format="wav", return_audio=False).translation)
```

Async: `AsyncTalkingClient` with the same methods (`await`).

Errors: `AuthError`, `RateLimitError`, `UnsupportedLanguageError`, `ServiceUnavailableError`, `TalkingTimeoutError`, `OfflineError`, … — see `talking_sdk.errors`. Retries 429/502/503/504 with `Retry-After`. `repr(client)` never contains the API key.

Full docs: [`docs/sdk/README.md`](../../docs/sdk/README.md).
