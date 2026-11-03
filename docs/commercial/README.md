# Proprietary commercial modules — interface only

This directory describes the **HTTP/WS contract** between the GPL core and a
**separate** All-rights-reserved repository.

- Copyright holder of the commercial code: **hateaboutyouone**
- Contact: hateaboutyou111@icloud.com
- This run **does not create** that repository.

Core implementation: `talking/commercial/client.py` (outbound HTTP + disabled stubs).
Schema: `openapi-commercial-v1.yaml`.

`has_entitlement(user_id, "voice_transcription")` is the only premium check the
chat notes slice uses. Local override: `COMMERCIAL_DEV_ENTITLEMENTS` /
`COMMERCIAL_DEV_PREMIUM` (ignored in production). See `docs/VOICE_TRANSCRIPTION.md`.

Never copy commercial source into `Talking-sys/Talking`.
