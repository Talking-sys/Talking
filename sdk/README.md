# Talking game-embed SDK

Let a game send **text or audio** and get a **translation** in the player's language, so the studio does not voice or localise every language separately.

| Piece | Licence | Path |
|---|---|---|
| Python client | MIT (pending confirmation — `NOTICE.md`) | [`python/`](python/) |
| JavaScript / TypeScript client | MIT (pending) | [`js/`](js/) |
| Unity / Unreal / Godot sketches | MIT (pending) | [`bindings/`](bindings/) |
| Examples | MIT (pending) | [`examples/`](examples/) |
| Talking **server** that implements `/api/sdk/v1` | GNU GPL v3 | `talking/sdk_api.py` in the repo root |

Developer guide (English + Russian), limits, privacy, and roadmap: **[`docs/sdk/`](../docs/sdk/)**.

Install the Python client from a checkout:

```bash
pip install -e sdk/python
```

```python
import os
from talking_sdk import TalkingClient

client = TalkingClient(
    os.environ["TALKING_SERVER_URL"],
    os.environ["TALKING_API_KEY"],   # never hard-code
    app_id=os.environ.get("TALKING_APP_ID", ""),
)
client.set_target_language("de")
print(client.translate_text("The gate is locked.").translation)
```

JavaScript:

```js
import { TalkingClient } from "talking-sdk";

const client = new TalkingClient({
  serverUrl: process.env.TALKING_SERVER_URL,
  apiKey: process.env.TALKING_API_KEY,
  targetLanguage: "de",
});
const r = await client.translateText("The gate is locked.");
```
