"""Application settings. Everything is overridable via environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEVELOPER_EMAIL = "hateaboutyou111@icloud.com"

# Spec: large-v3 default, switchable to base / small / medium.
WHISPER_MODELS = ("base", "small", "medium", "large-v3")
DEFAULT_WHISPER_MODEL = "large-v3"


def _env(name: str, default: str) -> str:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on", "да")


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


@dataclass
class Settings:
    lang_a: str = field(default_factory=lambda: _env("APP_LANG_A", "ru"))
    lang_b: str = field(default_factory=lambda: _env("APP_LANG_B", "en"))

    whisper_model: str = field(default_factory=lambda: _env("WHISPER_MODEL", DEFAULT_WHISPER_MODEL))
    whisper_device: str = field(default_factory=lambda: _env("WHISPER_DEVICE", "auto"))
    whisper_compute_type: str = field(default_factory=lambda: _env("WHISPER_COMPUTE_TYPE", "auto"))
    whisper_beam_size: int = field(default_factory=lambda: _env_int("WHISPER_BEAM_SIZE", 1))
    whisper_cpu_threads: int = field(default_factory=lambda: _env_int("WHISPER_CPU_THREADS", 0))
    whisper_vad_filter: bool = field(default_factory=lambda: _env_bool("WHISPER_VAD_FILTER", True))

    xai_api_key: str = field(default_factory=lambda: _env("XAI_API_KEY", ""))
    xai_base_url: str = field(default_factory=lambda: _env("XAI_BASE_URL", "https://api.x.ai/v1"))
    # Default model id is a reasonable current Grok non-reasoning name; override with XAI_MODEL.
    xai_model: str = field(default_factory=lambda: _env("XAI_MODEL", "grok-4-fast-non-reasoning"))
    translate_providers: str = field(default_factory=lambda: _env("APP_TRANSLATE_PROVIDERS", "grok,google,stub"))
    translate_timeout: float = field(default_factory=lambda: _env_float("APP_TRANSLATE_TIMEOUT", 15.0))

    tts_enabled: bool = field(default_factory=lambda: _env_bool("APP_TTS", False))
    # Empty = browser Web Speech API only. "gtts" enables MIT server TTS.
    # "edge" / "piper" / "pyttsx3" require requirements-optional.txt and stay off.
    tts_providers: str = field(default_factory=lambda: _env("APP_TTS_PROVIDERS", ""))
    piper_voices_dir: str = field(default_factory=lambda: _env("PIPER_VOICES_DIR", str(PROJECT_DIR / "voices")))
    tts_rate: str = field(default_factory=lambda: _env("APP_TTS_RATE", "+10%"))

    sample_rate: int = 16000
    block_ms: int = 30
    silence_ms: int = field(default_factory=lambda: _env_int("APP_PAUSE_MS", 800))
    max_segment_s: float = field(default_factory=lambda: _env_float("APP_MAX_SEGMENT_S", 18.0))
    min_segment_s: float = 0.35
    vad_threshold: float = field(default_factory=lambda: _env_float("APP_VAD_THRESHOLD", 0.0))

    buffer_seconds: float = field(default_factory=lambda: _env_float("APP_BUFFER_SECONDS", 180.0))
    data_dir: str = field(default_factory=lambda: _env("APP_DATA_DIR", str(PROJECT_DIR / "data")))

    host: str = field(default_factory=lambda: _env("APP_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _env_int("APP_PORT", 8000))
    direction: str = field(default_factory=lambda: _env("APP_DIRECTION", "auto"))

    app_name: str = field(default_factory=lambda: _env("APP_NAME", "Talking"))
    app_env: str = field(default_factory=lambda: _env("APP_ENV", "development"))
    contact_email: str = field(default_factory=lambda: _env("APP_CONTACT_EMAIL", DEVELOPER_EMAIL))
    cors_origins: str = field(default_factory=lambda: _env("APP_CORS_ORIGINS", ""))
    secret_key: str = field(default_factory=lambda: _env("APP_SECRET_KEY", ""))
    rate_limit_per_min: int = field(default_factory=lambda: _env_int("APP_RATE_LIMIT_PER_MIN", 120))
    ws_connects_per_min: int = field(default_factory=lambda: _env_int("APP_WS_CONNECTS_PER_MIN", 20))
    probe_interval_s: float = field(default_factory=lambda: _env_float("APP_PROBE_INTERVAL", 10.0))

    # Game-embed SDK HTTP API (/api/sdk/v1). Empty keys = endpoints refuse traffic.
    # Format: "app_id:secret,other_app:other_secret" (first ':' or '=' splits). Never commit real secrets.
    sdk_api_keys: str = field(default_factory=lambda: _env("SDK_API_KEYS", ""))
    sdk_rate_limit_per_min: int = field(default_factory=lambda: _env_int("SDK_RATE_LIMIT_PER_MIN", 60))
    sdk_max_text_chars: int = field(default_factory=lambda: _env_int("SDK_MAX_TEXT_CHARS", 4000))
    sdk_max_audio_bytes: int = field(default_factory=lambda: _env_int("SDK_MAX_AUDIO_BYTES", 5 * 1024 * 1024))

    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", ""))
    redis_url: str = field(default_factory=lambda: _env("REDIS_URL", ""))

    # M10 auth. `local` is the single-user home Wi-Fi translator (tests/dev default).
    # `accounts` enables cookies, CSRF, and phone sign-in. No anonymous users.
    auth_mode: str = field(default_factory=lambda: _env("AUTH_MODE", "local"))
    public_origin: str = field(default_factory=lambda: _env("PUBLIC_ORIGIN", ""))
    log_salt: str = field(default_factory=lambda: _env("LOG_SALT", ""))
    db_encryption_key: str = field(default_factory=lambda: _env("DB_ENCRYPTION_KEY", ""))
    # console = local/dev sink only (refused in production). twilio = Verify SMS.
    sms_provider: str = field(default_factory=lambda: _env("SMS_PROVIDER", "console"))
    twilio_account_sid: str = field(default_factory=lambda: _env("TWILIO_ACCOUNT_SID", ""))
    twilio_auth_token: str = field(default_factory=lambda: _env("TWILIO_AUTH_TOKEN", ""))
    twilio_verify_service_sid: str = field(default_factory=lambda: _env("TWILIO_VERIFY_SERVICE_SID", ""))
    sms_country_allowlist: str = field(default_factory=lambda: _env("SMS_COUNTRY_ALLOWLIST", ""))
    sms_default_region: str = field(default_factory=lambda: _env("SMS_DEFAULT_REGION", ""))

    s3_endpoint: str = field(default_factory=lambda: _env("S3_ENDPOINT", ""))
    s3_bucket: str = field(default_factory=lambda: _env("S3_BUCKET", ""))
    s3_region: str = field(default_factory=lambda: _env("S3_REGION", ""))
    s3_access_key: str = field(default_factory=lambda: _env("S3_ACCESS_KEY", ""))
    s3_secret_key: str = field(default_factory=lambda: _env("S3_SECRET_KEY", ""))

    commercial_api_url: str = field(default_factory=lambda: _env("COMMERCIAL_API_URL", ""))
    commercial_api_token: str = field(default_factory=lambda: _env("COMMERCIAL_API_TOKEN", ""))
    commercial_webhook_secret: str = field(default_factory=lambda: _env("COMMERCIAL_WEBHOOK_SECRET", ""))
    # Local/dev stub for premium checks. Never a substitute for the commercial service
    # in production — empty URL without these flags fail-closed (no entitlement).
    commercial_dev_entitlements: str = field(
        default_factory=lambda: _env("COMMERCIAL_DEV_ENTITLEMENTS", "")
    )
    commercial_dev_premium: bool = field(
        default_factory=lambda: _env_bool("COMMERCIAL_DEV_PREMIUM", False)
    )
    commercial_entitlement_cache_ttl: float = field(
        default_factory=lambda: _env_float("COMMERCIAL_ENTITLEMENT_CACHE_TTL", 60.0)
    )
    commercial_api_timeout: float = field(
        default_factory=lambda: _env_float("COMMERCIAL_API_TIMEOUT", 5.0)
    )

    # Chat notes slice (Telegram-style voice_note / video_note). M4/M5 reuse these caps.
    chat_voice_max_bytes: int = field(default_factory=lambda: _env_int("CHAT_VOICE_MAX_BYTES", 8 * 1024 * 1024))
    chat_video_max_bytes: int = field(default_factory=lambda: _env_int("CHAT_VIDEO_MAX_BYTES", 25 * 1024 * 1024))
    chat_quota_bytes: int = field(default_factory=lambda: _env_int("CHAT_QUOTA_BYTES", 500 * 1024 * 1024))
    chat_note_max_ms: int = field(default_factory=lambda: _env_int("CHAT_NOTE_MAX_MS", 60_000))
    chat_msg_per_min: int = field(default_factory=lambda: _env_int("CHAT_MSG_PER_MIN", 10))
    chat_upload_per_min: int = field(default_factory=lambda: _env_int("CHAT_UPLOAD_PER_MIN", 20))
    chat_play_token_ttl: int = field(default_factory=lambda: _env_int("CHAT_PLAY_TOKEN_TTL", 600))
    chat_download_token_ttl: int = field(default_factory=lambda: _env_int("CHAT_DOWNLOAD_TOKEN_TTL", 120))
    chat_transcribe: bool = field(default_factory=lambda: _env_bool("CHAT_TRANSCRIBE", True))
    # PR #3 auto-STT on upload. Default off: transcription is on-demand + premium-gated.
    chat_transcribe_on_upload: bool = field(
        default_factory=lambda: _env_bool("CHAT_TRANSCRIBE_ON_UPLOAD", False)
    )
    # none = 403 + locked button; lock = same, button visible; teaser = locked + hint copy.
    # hidden = no button. Free users never run STT.
    chat_transcribe_free_mode: str = field(
        default_factory=lambda: _env("CHAT_TRANSCRIBE_FREE_MODE", "lock")
    )
    chat_transcribe_per_min: int = field(
        default_factory=lambda: _env_int("CHAT_TRANSCRIBE_PER_MIN", 8)
    )
    chat_transcribe_max_ms: int = field(
        default_factory=lambda: _env_int("CHAT_TRANSCRIBE_MAX_MS", 60_000)
    )
    chat_transcribe_job_ttl: int = field(
        default_factory=lambda: _env_int("CHAT_TRANSCRIBE_JOB_TTL", 120)
    )
    chat_retention_seconds: int = field(
        default_factory=lambda: _env_int("CHAT_RETENTION_SECONDS", 0)
    )
    # M4 nick rename cooldown (owner spec: once per 30 days). Tests may set 0.
    chat_nick_rename_seconds: int = field(
        default_factory=lambda: _env_int("CHAT_NICK_RENAME_SECONDS", 30 * 24 * 3600)
    )
    chat_invite_ttl_seconds: int = field(
        default_factory=lambda: _env_int("CHAT_INVITE_TTL_SECONDS", 7 * 24 * 3600)
    )
    # faster-whisper (default, local) | openai-compatible (off; audio would leave the server)
    chat_stt_provider: str = field(
        default_factory=lambda: _env("CHAT_STT_PROVIDER", "faster-whisper")
    )
    chat_stt_api_url: str = field(default_factory=lambda: _env("CHAT_STT_API_URL", ""))
    chat_stt_api_key: str = field(default_factory=lambda: _env("CHAT_STT_API_KEY", ""))
    chat_stt_api_model: str = field(default_factory=lambda: _env("CHAT_STT_API_MODEL", "whisper-1"))

    # Link previews. UNFURL_STUB=1 skips outbound HTTP (tests / demo).
    unfurl_stub: bool = field(default_factory=lambda: _env_bool("UNFURL_STUB", False))
    unfurl_timeout: float = field(default_factory=lambda: _env_float("UNFURL_TIMEOUT_S", 8.0))
    unfurl_max_bytes: int = field(default_factory=lambda: _env_int("UNFURL_MAX_BYTES", 1_048_576))
    unfurl_max_redirects: int = field(default_factory=lambda: _env_int("UNFURL_MAX_REDIRECTS", 4))
    unfurl_cache_ttl: int = field(default_factory=lambda: _env_int("UNFURL_CACHE_TTL_S", 86_400))
    unfurl_rate_per_min: int = field(default_factory=lambda: _env_int("UNFURL_RATE_PER_MIN", 20))
    chat_msg_rate_per_min: int = field(default_factory=lambda: _env_int("CHAT_MSG_RATE_PER_MIN", 10))
    # Telegram ~48 h from original send. 0 = unlimited.
    chat_edit_window_s: int = field(default_factory=lambda: _env_int("CHAT_EDIT_WINDOW_S", 48 * 3600))
    chat_edit_rate_per_min: int = field(default_factory=lambda: _env_int("CHAT_EDIT_RATE_PER_MIN", 20))

    # Voice translation (press-and-hold live + record). Live is free in core;
    # VOICE_TRANSLATE_LIVE_PREMIUM=1 gates it on the commercial licence hook.
    voice_translate_live_premium: bool = field(
        default_factory=lambda: _env_bool("VOICE_TRANSLATE_LIVE_PREMIUM", False)
    )
    voice_translate_live_max_s: float = field(
        default_factory=lambda: _env_float("VOICE_TRANSLATE_LIVE_MAX_S", 120.0)
    )
    voice_translate_partial_interval_s: float = field(
        default_factory=lambda: _env_float("VOICE_TRANSLATE_PARTIAL_INTERVAL_S", 0.8)
    )
    voice_translate_partial_window_s: float = field(
        default_factory=lambda: _env_float("VOICE_TRANSLATE_PARTIAL_WINDOW_S", 3.0)
    )
    voice_translate_record_max_ms: int = field(
        default_factory=lambda: _env_int("VOICE_TRANSLATE_RECORD_MAX_MS", 60_000)
    )
    voice_translate_live_connects_per_min: int = field(
        default_factory=lambda: _env_int("VOICE_TRANSLATE_LIVE_CONNECTS_PER_MIN", 20)
    )
    voice_translate_record_per_min: int = field(
        default_factory=lambda: _env_int("VOICE_TRANSLATE_RECORD_PER_MIN", 20)
    )
    voice_translate_max_pending_audio_s: float = field(
        default_factory=lambda: _env_float("VOICE_TRANSLATE_MAX_PENDING_AUDIO_S", 4.0)
    )
    voice_translate_max_queue: int = field(
        default_factory=lambda: _env_int("VOICE_TRANSLATE_MAX_QUEUE", 64)
    )

    @property
    def production(self) -> bool:
        return self.app_env.lower() in ("production", "prod")

    def pair(self) -> tuple[str, str]:
        return self.lang_a, self.lang_b

    def normalized_whisper_model(self) -> str:
        m = (self.whisper_model or DEFAULT_WHISPER_MODEL).strip()
        return m if m in WHISPER_MODELS else DEFAULT_WHISPER_MODEL
