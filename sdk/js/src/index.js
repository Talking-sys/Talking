/**
 * Talking game-embed SDK (MIT). Browser and Node 18+.
 * Pass apiKey from env / a secret store — never hard-code it.
 * @module talking-sdk
 */

export const API_PREFIX = "/api/sdk/v1";
export const VERSION = "0.1.0";

const RETRY_STATUSES = new Set([408, 429, 502, 503, 504]);
const SECRET_HEADERS = new Set(["authorization", "x-talking-api-key"]);

export class TalkingError extends Error {
  constructor(message, { code = "error", status = null, retryAfter = null } = {}) {
    super(message);
    this.name = "TalkingError";
    this.code = code;
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

export class ConfigError extends TalkingError {
  constructor(message, extra = {}) {
    super(message, { code: "invalid_request", ...extra });
    this.name = "ConfigError";
  }
}

export class AuthError extends TalkingError {
  constructor(message, extra = {}) {
    super(message, { code: "unauthorized", ...extra });
    this.name = "AuthError";
  }
}

export class RateLimitError extends TalkingError {
  constructor(message, extra = {}) {
    super(message, { code: "rate_limited", ...extra });
    this.name = "RateLimitError";
  }
}

export class OfflineError extends TalkingError {
  constructor(message, extra = {}) {
    super(message, { code: "offline", ...extra });
    this.name = "OfflineError";
  }
}

const CODE_MAP = {
  unauthorized: AuthError,
  forbidden: AuthError,
  rate_limited: RateLimitError,
  invalid_request: TalkingError,
  unsupported_language: TalkingError,
  unsupported_format: TalkingError,
  payload_too_large: TalkingError,
  no_speech: TalkingError,
  translation_unavailable: TalkingError,
  stt_unavailable: TalkingError,
  sdk_disabled: TalkingError,
  timeout: TalkingError,
  offline: OfflineError,
};

function errorFromPayload(status, payload, retryAfter) {
  const err = payload && typeof payload.error === "object" ? payload.error : payload || {};
  const code = err.code || "error";
  const message = err.message || `HTTP ${status}`;
  const ra = err.retry_after != null ? Number(err.retry_after) : retryAfter;
  const Cls = CODE_MAP[code] || TalkingError;
  return new Cls(message, { code, status, retryAfter: Number.isFinite(ra) ? ra : retryAfter });
}

function safeUrl(url) {
  try {
    const u = new URL(url, "http://localhost");
    return `${u.protocol}//${u.host}${u.pathname}`;
  } catch {
    return "<url>";
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function parseRetryAfter(res) {
  const raw = res.headers && (res.headers.get ? res.headers.get("retry-after") : res.headers["retry-after"]);
  if (raw == null) return null;
  const n = Number(raw);
  return Number.isFinite(n) ? Math.max(0, n) : null;
}

/**
 * @typedef {object} TalkingClientOptions
 * @property {string} serverUrl
 * @property {string} apiKey
 * @property {string} [appId]
 * @property {number} [timeout=15000] milliseconds
 * @property {number} [maxRetries=3]
 * @property {number} [retryBackoff=250] milliseconds
 * @property {boolean} [offline=false]
 * @property {string} [targetLanguage]
 * @property {typeof fetch} [fetch]
 */

export class TalkingClient {
  /**
   * @param {TalkingClientOptions} opts
   */
  constructor(opts = {}) {
    if (!opts.serverUrl) throw new ConfigError("serverUrl is required");
    if (!opts.apiKey) throw new ConfigError("apiKey is required");
    this.serverUrl = String(opts.serverUrl).replace(/\/$/, "");
    this.appId = opts.appId || "";
    this.timeout = opts.timeout ?? 15000;
    this.maxRetries = opts.maxRetries ?? 3;
    this.retryBackoff = opts.retryBackoff ?? 250;
    this.offline = !!opts.offline;
    this._target = opts.targetLanguage ? String(opts.targetLanguage).toLowerCase() : null;
    this._languages = null;
    this._apiKey = opts.apiKey;
    this._fetch = opts.fetch || (typeof fetch === "function" ? fetch.bind(globalThis) : null);
    if (!this._fetch) throw new ConfigError("fetch is not available; pass opts.fetch");
  }

  toJSON() {
    return { serverUrl: this.serverUrl, appId: this.appId, offline: this.offline };
  }

  setTargetLanguage(code) {
    const c = String(code || "").trim().toLowerCase();
    if (!c) throw new ConfigError("target language is empty");
    this._target = c;
    return c;
  }

  getTargetLanguage() {
    return this._target;
  }

  _headers(extra) {
    const h = {
      Authorization: `Bearer ${this._apiKey}`,
      Accept: "application/json",
      "User-Agent": `talking-sdk-js/${VERSION}`,
    };
    if (this.appId) h["X-Talking-App-Id"] = this.appId;
    return { ...h, ...extra };
  }

  _url(path) {
    if (!path.startsWith("/")) path = `/${path}`;
    return `${this.serverUrl}${API_PREFIX}${path}`;
  }

  _resolveTarget(target) {
    const t = String(target || this._target || "").trim().toLowerCase();
    if (!t) {
      throw new ConfigError("target language is required (setTargetLanguage or pass target)");
    }
    return t;
  }

  async _send(method, path, { json, form, headers, signal } = {}) {
    if (this.offline) throw new OfflineError("client is in offline mode");
    const url = this._url(path);
    const hdrs = this._headers(headers);
    let body;
    if (json !== undefined) {
      hdrs["Content-Type"] = "application/json";
      body = JSON.stringify(json);
    } else if (form) {
      body = form;
    }
    let last;
    for (let attempt = 0; attempt <= this.maxRetries; attempt++) {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), this.timeout);
      const onAbort = () => ctrl.abort();
      if (signal) {
        if (signal.aborted) ctrl.abort();
        else signal.addEventListener("abort", onAbort, { once: true });
      }
      try {
        const res = await this._fetch(url, { method, headers: hdrs, body, signal: ctrl.signal });
        if (RETRY_STATUSES.has(res.status) && attempt < this.maxRetries) {
          const ra = parseRetryAfter(res);
          let payload = null;
          try {
            payload = await res.json();
          } catch {
            payload = null;
          }
          last = errorFromPayload(res.status, payload, ra);
          await sleep((ra != null ? ra * 1000 : Math.min(8000, this.retryBackoff * 2 ** attempt)));
          continue;
        }
        if (res.status >= 400) {
          let payload = null;
          try {
            payload = await res.json();
          } catch {
            payload = { error: { code: "error", message: `HTTP ${res.status}` } };
          }
          throw errorFromPayload(res.status, payload, parseRetryAfter(res));
        }
        return res;
      } catch (err) {
        if (err instanceof TalkingError) throw err;
        last = new TalkingError(err && err.name === "AbortError" ? "request timed out" : "network error", {
          code: err && err.name === "AbortError" ? "timeout" : "network",
        });
        if (attempt >= this.maxRetries) throw last;
        await sleep(Math.min(8000, this.retryBackoff * 2 ** attempt));
      } finally {
        clearTimeout(timer);
        if (signal) signal.removeEventListener("abort", onAbort);
      }
    }
    throw last || new TalkingError("request failed", { code: "network" });
  }

  async info(opts = {}) {
    const res = await this._send("GET", "/", { signal: opts.signal });
    return res.json();
  }

  async listLanguages({ refresh = false, signal } = {}) {
    if (this.offline) {
      if (this._languages && !refresh) return this._languages.slice();
      throw new OfflineError("no cached language list");
    }
    if (this._languages && !refresh) return this._languages.slice();
    const res = await this._send("GET", "/languages", { signal });
    const payload = await res.json();
    const rows = Array.isArray(payload.languages) ? payload.languages : payload;
    this._languages = rows;
    return rows.slice();
  }

  async translateText(text, { source = "auto", target, returnAudio = false, signal } = {}) {
    const tgt = this._resolveTarget(target);
    const res = await this._send("POST", "/translate/text", {
      json: { text, source: source || "auto", target: tgt, return_audio: !!returnAudio },
      signal,
    });
    return res.json();
  }

  /**
   * Streaming-ready: v1 parses SSE (status, translation, done).
   * @returns {Promise<Array<{event: string, data: object}>>}
   */
  async translateTextStream(text, { source = "auto", target, returnAudio = false, signal } = {}) {
    const tgt = this._resolveTarget(target);
    const res = await this._send("POST", "/translate/text/stream", {
      json: { text, source: source || "auto", target: tgt, return_audio: !!returnAudio },
      headers: { Accept: "text/event-stream" },
      signal,
    });
    const body = await res.text();
    return parseSse(body);
  }

  /**
   * @param {Blob|ArrayBuffer|Uint8Array|ArrayBufferView} audio
   */
  async translateAudio(audio, { format = "wav", target, source = "auto", returnAudio = false, sampleRate = 16000, signal } = {}) {
    const tgt = this._resolveTarget(target);
    const blob = toBlob(audio, format);
    const form = new FormData();
    form.append("audio", blob, `clip.${format}`);
    form.append("format", format);
    form.append("source", source || "auto");
    form.append("target", tgt);
    form.append("return_audio", returnAudio ? "true" : "false");
    form.append("sample_rate", String(sampleRate));
    const res = await this._send("POST", "/translate/audio", { form, signal });
    return res.json();
  }
}

function toBlob(audio, format) {
  if (typeof Blob !== "undefined" && audio instanceof Blob) return audio;
  const mime = format === "wav" ? "audio/wav" : "application/octet-stream";
  if (audio instanceof ArrayBuffer) return new Blob([audio], { type: mime });
  if (ArrayBuffer.isView(audio)) {
    return new Blob([audio.buffer.slice(audio.byteOffset, audio.byteOffset + audio.byteLength)], { type: mime });
  }
  throw new ConfigError("audio must be a Blob, ArrayBuffer, or typed array");
}

export function parseSse(text) {
  const events = [];
  let event = "message";
  let dataLines = [];
  const lines = String(text).split(/\r?\n/);
  const flush = () => {
    if (!dataLines.length) return;
    let data;
    const raw = dataLines.join("\n");
    try {
      data = JSON.parse(raw);
    } catch {
      data = { raw };
    }
    events.push({ event, data });
    event = "message";
    dataLines = [];
  };
  for (const line of lines) {
    if (line === "") {
      flush();
      continue;
    }
    if (line.startsWith("event:")) event = line.slice(6).trim() || "message";
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
  }
  flush();
  return events;
}

export function redactHeaders(headers) {
  const out = {};
  for (const [k, v] of Object.entries(headers || {})) {
    out[k] = SECRET_HEADERS.has(k.toLowerCase()) ? "***" : v;
  }
  return out;
}

export { safeUrl };
