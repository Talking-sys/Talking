#!/usr/bin/env python3
"""Tiny text-game loop showing Talking instant translation.

The innkeeper only 'speaks' the player's chosen language. You type in any
language; the SDK translates to the innkeeper's language and back.

Never hard-code secrets. Export them in the environment:

    export TALKING_SERVER_URL=http://127.0.0.1:8000
    export TALKING_API_KEY=change-me          # must match SDK_API_KEYS on the server
    export TALKING_APP_ID=demo                # optional
    python main.py

Server (from the repo root, stub translator, no model download):

    SDK_API_KEYS=demo:change-me APP_TRANSLATE_PROVIDERS=stub python main.py serve
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python" / "src"))

from talking_sdk import ConfigError, TalkingClient, TalkingError  # noqa: E402

LINES = [
    "Welcome, traveler! What will you have?",
    "The stew is hot and the ale is cold. Anything else?",
    "Safe roads. Come back when the rain stops.",
]


def _client() -> TalkingClient:
    url = os.environ.get("TALKING_SERVER_URL", "").strip()
    key = os.environ.get("TALKING_API_KEY", "").strip()
    app = os.environ.get("TALKING_APP_ID", "demo").strip()
    if not url or not key:
        print(
            "Set TALKING_SERVER_URL and TALKING_API_KEY in the environment.\n"
            "Example:\n"
            "  export TALKING_SERVER_URL=http://127.0.0.1:8000\n"
            "  export TALKING_API_KEY=change-me\n"
            "Do not paste real keys into this file.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return TalkingClient(url, key, app_id=app, timeout=20.0)


def main() -> int:
    target = (os.environ.get("TALKING_TARGET_LANG") or "ru").strip().lower()
    try:
        client = _client()
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 2
    client.set_target_language(target)
    print("Talking tavern  —  /lang <code>  /langs  /quit")
    print(f"Innkeeper language: {target}")
    try:
        langs = client.list_languages()
        print(f"{len(langs)} languages from server.")
    except TalkingError as e:
        print(f"Could not list languages: {e}", file=sys.stderr)
        return 1
    try:
        for line in LINES:
            try:
                spoken = client.translate_text(line, source="en", target=target)
                print(f"\nInnkeeper: {spoken.translation}")
            except TalkingError as e:
                print(f"\nInnkeeper (untranslated): {line}  [{e.code}]")
            raw = input("You: ").strip()
            if not raw:
                continue
            if raw in ("/quit", "/q", "quit"):
                break
            if raw.startswith("/lang "):
                client.set_target_language(raw.split(None, 1)[1])
                target = client.get_target_language() or target
                print(f"(innkeeper now speaks {target})")
                continue
            if raw in ("/langs", "/languages"):
                print(", ".join(x.code for x in langs[:40]), "...")
                continue
            try:
                heard = client.translate_text(raw, source="auto", target=target)
                print(f"Innkeeper hears: {heard.translation}")
            except TalkingError as e:
                print(f"(translation failed: {e.code})")
    except (EOFError, KeyboardInterrupt):
        print()
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
