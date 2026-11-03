/** Talking game-embed SDK. Types for the ESM client in index.js. */

export const API_PREFIX: "/api/sdk/v1";
export const VERSION: string;

export class TalkingError extends Error {
  code: string;
  status: number | null;
  retryAfter: number | null;
  constructor(
    message: string,
    extra?: { code?: string; status?: number | null; retryAfter?: number | null },
  );
}

export class ConfigError extends TalkingError {}
export class AuthError extends TalkingError {}
export class RateLimitError extends TalkingError {}
export class OfflineError extends TalkingError {}

export interface Language {
  code: string;
  en: string;
  ru?: string;
  label?: string;
}

export interface TranslationResult {
  request_id: string;
  source: string;
  target: string;
  text: string;
  translation: string;
  provider: string;
  elapsed_ms: number;
  stt_ms?: number | null;
  tts_ms?: number | null;
  audio?: string | null;
  audio_mime?: string | null;
  audio_error?: string | null;
  lang_prob?: number | null;
}

export interface StreamEvent {
  event: string;
  data: Record<string, unknown>;
}

export interface SdkInfo {
  api: string;
  version: string;
  server_version: string;
  stt_ready: boolean;
  tts_enabled: boolean;
  max_text_chars: number;
  max_audio_bytes: number;
  audio_formats: string[];
  stream?: Record<string, string>;
}

export interface TalkingClientOptions {
  serverUrl: string;
  apiKey: string;
  appId?: string;
  timeout?: number;
  maxRetries?: number;
  retryBackoff?: number;
  offline?: boolean;
  targetLanguage?: string;
  fetch?: typeof fetch;
}

export interface TranslateOptions {
  source?: string;
  target?: string;
  returnAudio?: boolean;
  signal?: AbortSignal;
}

export interface TranslateAudioOptions extends TranslateOptions {
  format?: string;
  sampleRate?: number;
}

export class TalkingClient {
  readonly serverUrl: string;
  readonly appId: string;
  timeout: number;
  maxRetries: number;
  retryBackoff: number;
  offline: boolean;
  constructor(opts: TalkingClientOptions);
  toJSON(): { serverUrl: string; appId: string; offline: boolean };
  setTargetLanguage(code: string): string;
  getTargetLanguage(): string | null;
  info(opts?: { signal?: AbortSignal }): Promise<SdkInfo>;
  listLanguages(opts?: { refresh?: boolean; signal?: AbortSignal }): Promise<Language[]>;
  translateText(text: string, opts?: TranslateOptions): Promise<TranslationResult>;
  translateTextStream(text: string, opts?: TranslateOptions): Promise<StreamEvent[]>;
  translateAudio(
    audio: Blob | ArrayBuffer | ArrayBufferView,
    opts?: TranslateAudioOptions,
  ): Promise<TranslationResult>;
}

export function parseSse(text: string): StreamEvent[];
export function redactHeaders(headers: Record<string, string>): Record<string, string>;
export function safeUrl(url: string): string;
