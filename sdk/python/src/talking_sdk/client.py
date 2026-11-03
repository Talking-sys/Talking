"""Sync and async Talking game SDK clients.

Standalone MIT package: depends only on httpx. Does not import the GPL server.
API keys are sent as Authorization: Bearer and are never written to logs or repr().
"""
from __future__ import annotations

import logging
import mimetypes
import time
from collections.abc import AsyncIterator, Iterator, Mapping
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlparse, urlunparse

import httpx

from .errors import (
    ConfigError,
    NetworkError,
    OfflineError,
    TalkingError,
    TalkingTimeoutError,
    error_from_payload,
)
from .models import Language, StreamEvent, Translation

log = logging.getLogger("talking_sdk")

API_PREFIX = "/api/sdk/v1"
DEFAULT_TIMEOUT = 15.0
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF = 0.25
USER_AGENT = "talking-sdk-python/0.1.0"
_RETRY_STATUSES = {408, 429, 502, 503, 504}
_SECRET_HEADERS = {"authorization", "x-talking-api-key"}


def _safe_url(url: str) -> str:
    p = urlparse(url)
    host = p.hostname or ""
    if p.port:
        host = f"{host}:{p.port}"
    return urlunparse((p.scheme, host, p.path, "", "", ""))


def _redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    out = {}
    for k, v in headers.items():
        out[k] = "***" if k.lower() in _SECRET_HEADERS else v
    return out


def _parse_retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _read_audio(audio: bytes | bytearray | memoryview | str | Path | BinaryIO | Iterator[bytes]) -> bytes:
    if isinstance(audio, (bytes, bytearray, memoryview)):
        return bytes(audio)
    if isinstance(audio, (str, Path)):
        return Path(audio).read_bytes()
    read = getattr(audio, "read", None)
    if callable(read):
        data = read()
        if not isinstance(data, (bytes, bytearray)):
            raise ConfigError("audio stream must yield bytes", code="invalid_request")
        return bytes(data)
    if hasattr(audio, "__iter__"):
        chunks = []
        for chunk in audio:  # type: ignore[union-attr]
            if not isinstance(chunk, (bytes, bytearray, memoryview)):
                raise ConfigError("audio iterator must yield bytes", code="invalid_request")
            chunks.append(bytes(chunk))
        return b"".join(chunks)
    raise ConfigError("audio must be bytes, a file path, a binary stream, or an iterator of bytes",
                      code="invalid_request")


def _guess_format(audio: Any, fmt: str | None) -> str:
    if fmt:
        return fmt.lstrip(".").lower()
    if isinstance(audio, (str, Path)):
        suffix = Path(audio).suffix.lstrip(".").lower()
        if suffix:
            return suffix
    return "wav"


class _ClientCore:
    def __init__(
        self,
        server_url: str,
        api_key: str,
        *,
        app_id: str = "",
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_RETRIES,
        retry_backoff: float = DEFAULT_BACKOFF,
        offline: bool = False,
        target_language: str | None = None,
        user_agent: str = USER_AGENT,
    ):
        if not (server_url or "").strip():
            raise ConfigError("server_url is required")
        if not (api_key or "").strip():
            raise ConfigError("api_key is required")
        self.server_url = server_url.rstrip("/")
        self.app_id = app_id or ""
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self.retry_backoff = float(retry_backoff)
        self.offline = bool(offline)
        self.user_agent = user_agent
        self._api_key = api_key
        self._target = (target_language or "").strip().lower() or None
        self._languages: list[Language] | None = None

    def __repr__(self) -> str:
        return (f"{type(self).__name__}(server_url={_safe_url(self.server_url)!r}, "
                f"app_id={self.app_id!r}, offline={self.offline})")

    def set_target_language(self, code: str) -> str:
        c = (code or "").strip().lower()
        if not c:
            raise ConfigError("target language is empty", code="unsupported_language")
        self._target = c
        return c

    def get_target_language(self) -> str | None:
        return self._target

    def _headers(self) -> dict[str, str]:
        h = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }
        if self.app_id:
            h["X-Talking-App-Id"] = self.app_id
        return h

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return self.server_url + API_PREFIX + path

    def _resolve_target(self, target: str | None) -> str:
        t = (target or self._target or "").strip().lower()
        if not t:
            raise ConfigError("target language is required (set_target_language or pass target=)",
                              code="invalid_request")
        return t

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        payload = None
        try:
            payload = response.json()
        except Exception:
            payload = {"error": {"code": "error", "message": response.text[:200]}}
        raise error_from_payload(response.status_code, payload, _parse_retry_after(response))

    def _cache_languages(self, payload: dict | list) -> list[Language]:
        rows = payload["languages"] if isinstance(payload, dict) and "languages" in payload else payload
        if not isinstance(rows, list):
            raise TalkingError("languages response is not a list", code="error", status=200)
        langs = [Language.from_dict(x) for x in rows if isinstance(x, dict)]
        self._languages = langs
        return langs


class TalkingClient(_ClientCore):
    """Synchronous client. Safe to use from a game loop or worker thread."""

    def __init__(self, server_url: str, api_key: str, **kw: Any):
        http_client = kw.pop("http_client", None)
        super().__init__(server_url, api_key, **kw)
        self._owns_http = http_client is None
        self._http = http_client or httpx.Client(timeout=self.timeout, headers={"User-Agent": self.user_agent})

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> "TalkingClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if self.offline:
            raise OfflineError("client is in offline mode", code="offline")
        url = self._url(path)
        headers = {**self._headers(), **kwargs.pop("headers", {})}
        last: TalkingError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                log.debug("sdk %s %s headers=%s", method, _safe_url(url), _redact_headers(headers))
                response = self._http.request(method, url, headers=headers, timeout=self.timeout, **kwargs)
            except httpx.TimeoutException as e:
                last = TalkingTimeoutError("request timed out", code="timeout")
                if attempt >= self.max_retries:
                    raise last from e
                self._sleep(min(8.0, self.retry_backoff * (2 ** attempt)))
                continue
            except httpx.TransportError as e:
                last = NetworkError(f"network error: {type(e).__name__}", code="network")
                if attempt >= self.max_retries:
                    raise last from e
                self._sleep(min(8.0, self.retry_backoff * (2 ** attempt)))
                continue
            if response.status_code in _RETRY_STATUSES and attempt < self.max_retries:
                ra = _parse_retry_after(response)
                last = error_from_payload(response.status_code, _try_json(response), ra)
                self._sleep(ra if ra is not None else min(8.0, self.retry_backoff * (2 ** attempt)))
                continue
            self._raise_for_status(response)
            return response
        raise last or NetworkError("request failed", code="network")

    def info(self) -> dict:
        return self._send("GET", "").json()

    def list_languages(self, *, refresh: bool = False) -> list[Language]:
        if self.offline:
            if self._languages and not refresh:
                return list(self._languages)
            raise OfflineError("no cached language list (call list_languages while online first)",
                               code="offline")
        if self._languages is not None and not refresh:
            return list(self._languages)
        return self._cache_languages(self._send("GET", "/languages").json())

    def translate_text(self, text: str, source: str = "auto", target: str | None = None,
                       return_audio: bool = False) -> Translation:
        tgt = self._resolve_target(target)
        body = {"text": text, "source": source or "auto", "target": tgt, "return_audio": bool(return_audio)}
        return Translation.from_dict(self._send("POST", "/translate/text", json=body).json())

    def translate_text_stream(self, text: str, source: str = "auto", target: str | None = None,
                              return_audio: bool = False) -> Iterator[StreamEvent]:
        """Yield SSE events. v1 sends status, one translation, then done."""
        tgt = self._resolve_target(target)
        body = {"text": text, "source": source or "auto", "target": tgt, "return_audio": bool(return_audio)}
        response = self._send("POST", "/translate/text/stream", json=body)
        yield from _iter_sse(response)

    def translate_audio(
        self,
        audio: bytes | bytearray | memoryview | str | Path | BinaryIO | Iterator[bytes],
        format: str | None = None,
        target: str | None = None,
        source: str = "auto",
        return_audio: bool = False,
        sample_rate: int = 16000,
    ) -> Translation:
        tgt = self._resolve_target(target)
        data = _read_audio(audio)
        fmt = _guess_format(audio, format)
        mime = mimetypes.types_map.get("." + fmt, "application/octet-stream")
        files = {"audio": (f"clip.{fmt}", data, mime)}
        form = {
            "format": fmt,
            "source": source or "auto",
            "target": tgt,
            "return_audio": "true" if return_audio else "false",
            "sample_rate": str(int(sample_rate)),
        }
        return Translation.from_dict(self._send("POST", "/translate/audio", files=files, data=form).json())


class AsyncTalkingClient(_ClientCore):
    """Asyncio counterpart of TalkingClient. Method names match; all are awaitable."""

    def __init__(self, server_url: str, api_key: str, **kw: Any):
        http_client = kw.pop("http_client", None)
        super().__init__(server_url, api_key, **kw)
        self._owns_http = http_client is None
        self._http = http_client or httpx.AsyncClient(timeout=self.timeout, headers={"User-Agent": self.user_agent})

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    close = aclose

    async def __aenter__(self) -> "AsyncTalkingClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _sleep(self, seconds: float) -> None:
        if seconds > 0:
            import asyncio
            await asyncio.sleep(seconds)

    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if self.offline:
            raise OfflineError("client is in offline mode", code="offline")
        url = self._url(path)
        headers = {**self._headers(), **kwargs.pop("headers", {})}
        last: TalkingError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                log.debug("sdk %s %s headers=%s", method, _safe_url(url), _redact_headers(headers))
                response = await self._http.request(method, url, headers=headers, timeout=self.timeout, **kwargs)
            except httpx.TimeoutException as e:
                last = TalkingTimeoutError("request timed out", code="timeout")
                if attempt >= self.max_retries:
                    raise last from e
                await self._sleep(min(8.0, self.retry_backoff * (2 ** attempt)))
                continue
            except httpx.TransportError as e:
                last = NetworkError(f"network error: {type(e).__name__}", code="network")
                if attempt >= self.max_retries:
                    raise last from e
                await self._sleep(min(8.0, self.retry_backoff * (2 ** attempt)))
                continue
            if response.status_code in _RETRY_STATUSES and attempt < self.max_retries:
                ra = _parse_retry_after(response)
                last = error_from_payload(response.status_code, _try_json(response), ra)
                await self._sleep(ra if ra is not None else min(8.0, self.retry_backoff * (2 ** attempt)))
                continue
            self._raise_for_status(response)
            return response
        raise last or NetworkError("request failed", code="network")

    async def info(self) -> dict:
        return (await self._send("GET", "")).json()

    async def list_languages(self, *, refresh: bool = False) -> list[Language]:
        if self.offline:
            if self._languages and not refresh:
                return list(self._languages)
            raise OfflineError("no cached language list (call list_languages while online first)",
                               code="offline")
        if self._languages is not None and not refresh:
            return list(self._languages)
        return self._cache_languages((await self._send("GET", "/languages")).json())

    async def translate_text(self, text: str, source: str = "auto", target: str | None = None,
                             return_audio: bool = False) -> Translation:
        tgt = self._resolve_target(target)
        body = {"text": text, "source": source or "auto", "target": tgt, "return_audio": bool(return_audio)}
        return Translation.from_dict((await self._send("POST", "/translate/text", json=body)).json())

    async def translate_text_stream(self, text: str, source: str = "auto", target: str | None = None,
                                    return_audio: bool = False) -> AsyncIterator[StreamEvent]:
        tgt = self._resolve_target(target)
        body = {"text": text, "source": source or "auto", "target": tgt, "return_audio": bool(return_audio)}
        response = await self._send("POST", "/translate/text/stream", json=body)
        for ev in _iter_sse(response):
            yield ev

    async def translate_audio(
        self,
        audio: bytes | bytearray | memoryview | str | Path | BinaryIO | Iterator[bytes],
        format: str | None = None,
        target: str | None = None,
        source: str = "auto",
        return_audio: bool = False,
        sample_rate: int = 16000,
    ) -> Translation:
        tgt = self._resolve_target(target)
        data = _read_audio(audio)
        fmt = _guess_format(audio, format)
        mime = mimetypes.types_map.get("." + fmt, "application/octet-stream")
        files = {"audio": (f"clip.{fmt}", data, mime)}
        form = {
            "format": fmt,
            "source": source or "auto",
            "target": tgt,
            "return_audio": "true" if return_audio else "false",
            "sample_rate": str(int(sample_rate)),
        }
        return Translation.from_dict((await self._send("POST", "/translate/audio", files=files, data=form)).json())


def _try_json(response: httpx.Response) -> dict | None:
    try:
        data = response.json()
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _iter_sse(response: httpx.Response) -> Iterator[StreamEvent]:
    event = "message"
    data_lines: list[str] = []
    text = response.text
    for line in text.splitlines():
        if line == "":
            if data_lines:
                import json
                raw = "\n".join(data_lines)
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = {"raw": raw}
                yield StreamEvent(event=event, data=payload if isinstance(payload, dict) else {"raw": payload})
            event = "message"
            data_lines = []
            continue
        if line.startswith("event:"):
            event = line[6:].strip() or "message"
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        import json
        raw = "\n".join(data_lines)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"raw": raw}
        yield StreamEvent(event=event, data=payload if isinstance(payload, dict) else {"raw": payload})
