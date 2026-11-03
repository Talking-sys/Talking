# Python tavern demo

A ten-line inn. You type in any language; Talking translates for the innkeeper.

```bash
# terminal 1 — repo root
SDK_API_KEYS=demo:change-me APP_TRANSLATE_PROVIDERS=stub python main.py serve

# terminal 2
export TALKING_SERVER_URL=http://127.0.0.1:8000
export TALKING_API_KEY=change-me
export TALKING_TARGET_LANG=ru
python sdk/examples/python_game_demo/main.py
```

`change-me` is a **placeholder**. Put a real key only in the environment, never in source.
