# NOTICE

**Open core:** GNU GPL v3 (see `LICENSE`).
**Copyright (C) 2026 hateaboutyouone.**
**Developer / rightsholder contact:** hateaboutyou111@icloud.com
  (licensing, support, data requests, security).

Commercial modules (admin, licensing, Talk-coins, P2P payments, analytics) are **not** part of this repository. They are intended for a **separate** repository under an **All rights reserved** licence, copyright holder hateaboutyouone, and may communicate with this core **only** over the documented HTTP/WS API. See `docs/ARCHITECTURE.md`.

## Caveats — not legal advice

Verify every statement below against the **current** texts of:

- GNU GPL v3 (`LICENSE` in this repo, https://www.gnu.org/licenses/gpl-3.0.html)
- xAI terms (Enterprise / API)
- Cursor Terms of Service
- DeepSeek, Qwen / Alibaba Model Studio, Cohere, NAVER CLOVA, Meta MMS, Nominatim usage policies
- Apple, Google, Stripe, PayPal, Blink developer agreements (when those modules exist)
- Applicable copyright law in your jurisdiction (including US Copyright Office guidance on AI-assisted works)

Owner’s current understanding (must be re-checked before sale or investment):

- Combining GPL core with proprietary modules via a network API is a different legal question from statically linking them. **Do not treat this layout as a proven GPL-compliance strategy.** Counsel should review it.
- Meta MMS weights are published under **CC-BY-NC 4.0**, which is **not** a GPL-compatible licence for bundling inside a GPL distribution. A future language-router module must not vendor those weights into this repo.
- Optional TTS extras (`edge-tts` LGPL-3, `piper-tts` / espeak-ng GPL-3) are listed in `requirements-optional.txt` and are **disabled**. Installing them into a proprietary distribution has copyleft consequences — check before enabling.
- AI-tool output may be non-unique; providers keep rights in their models/infrastructure; feedback may be assigned to providers; brand guidelines may require attribution. In the US, copyright protects **human** contribution (specifications, architecture, selection and editing). Specs in `docs/specs/` are kept as evidence of that contribution.
- Provider terms change. This file is a snapshot of intent, not a warranty.

**This is not legal advice. Show a lawyer the current licence texts before you sell, fund, or combine the proprietary repo with this core.**

## Game embed SDK — dual licensing PENDING confirmation

Client libraries under `sdk/` (Python, JavaScript, engine binding sketches, examples) are **intended** to be licensed under the **MIT Licence** (`sdk/LICENSE`) so closed-source games can embed the HTTP client without inheriting GPL from it. The Talking **server** (`talking/`, FastAPI, STT/MT/TTS) remains **GNU GPL v3**.

**This split is PENDING confirmation by the copyright holder (hateaboutyouone).** Until then, treat `sdk/LICENSE` as intent, not a finished legal product. Do not copy GPL server modules into `sdk/`. Details: `sdk/NOTICE.md`. Not legal advice.

## English summary

This repository is GPL v3 open core (translation, chat, games, buffer). Proprietary monetization modules belong in a separate All-rights-reserved repo owned by hateaboutyouone and must talk to core only over HTTP/WS. Client libraries under `sdk/` are intended MIT, pending owner confirmation. Caveats above are not legal advice; verify against current law and licence texts.
