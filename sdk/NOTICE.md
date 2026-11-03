# NOTICE — Talking game-embed SDK

Copyright (C) 2026 hateaboutyouone. Contact: hateaboutyou111@icloud.com.

## Dual licensing (PENDING owner confirmation)

Client libraries under `sdk/` (Python, JavaScript, engine binding sketches, and
examples) are **intended** to be licensed under the **MIT Licence** (`sdk/LICENSE`)
so closed-source games can embed the client without inheriting GPL from it.

The Talking **server** (`talking/` package, FastAPI app, STT / translation / TTS
pipeline, this repository's root `LICENSE`) remains **GNU GPL v3**.

**This split is a product decision PENDING confirmation by the copyright holder
(hateaboutyouone).** Until that confirmation, treat `sdk/LICENSE` as the intended
client licence. It is **not** legal advice. Show counsel the current GPL v3 and
MIT texts before you ship a closed-source game, sell, or fund.

Do **not** copy GPL server modules into `sdk/`. The MIT client talks to Talking
only over the documented HTTP API (`/api/sdk/v1`).

Network use of a hosted Talking service is a different question from statically
linking GPL server code. Verify against current licence texts.
