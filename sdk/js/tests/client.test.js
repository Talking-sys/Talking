import { test } from "node:test";
import assert from "node:assert/strict";
import {
  TalkingClient,
  ConfigError,
  AuthError,
  RateLimitError,
  OfflineError,
  parseSse,
  redactHeaders,
} from "../src/index.js";

test("constructor requires serverUrl and apiKey", () => {
  assert.throws(() => new TalkingClient({ apiKey: "k" }), ConfigError);
  assert.throws(() => new TalkingClient({ serverUrl: "http://x" }), ConfigError);
});

test("repr / toJSON never includes the api key", () => {
  const c = new TalkingClient({
    serverUrl: "http://127.0.0.1:8000",
    apiKey: "super-secret-key",
    appId: "demo",
    fetch: async () => new Response("{}"),
  });
  const dumped = JSON.stringify(c.toJSON());
  assert.equal(dumped.includes("super-secret-key"), false);
  assert.equal(String(c.toJSON()).includes("super-secret-key"), false);
});

test("redactHeaders strips Authorization", () => {
  const out = redactHeaders({ Authorization: "Bearer super-secret-key", Accept: "application/json" });
  assert.equal(out.Authorization, "***");
  assert.equal(out.Accept, "application/json");
});

test("set/get target language", () => {
  const c = new TalkingClient({
    serverUrl: "http://example.invalid",
    apiKey: "k",
    fetch: async () => new Response("{}"),
  });
  assert.equal(c.getTargetLanguage(), null);
  c.setTargetLanguage("de");
  assert.equal(c.getTargetLanguage(), "de");
});

test("offline listLanguages without cache fails", async () => {
  const c = new TalkingClient({
    serverUrl: "http://example.invalid",
    apiKey: "k",
    offline: true,
    fetch: async () => {
      throw new Error("network should not be used");
    },
  });
  await assert.rejects(() => c.listLanguages(), OfflineError);
});

test("translateText posts JSON and maps the response", async () => {
  /** @type {Request | null} */
  let seen = null;
  const c = new TalkingClient({
    serverUrl: "http://talk.example",
    apiKey: "k-secret",
    appId: "demo",
    fetch: async (url, init) => {
      seen = new Request(url, init);
      return new Response(
        JSON.stringify({
          request_id: "abc",
          source: "en",
          target: "de",
          text: "hi",
          translation: "[en→de] hi",
          provider: "stub",
          elapsed_ms: 1,
        }),
        { headers: { "Content-Type": "application/json" } },
      );
    },
  });
  const r = await c.translateText("hi", { source: "en", target: "de" });
  assert.equal(r.translation, "[en→de] hi");
  assert.ok(seen);
  assert.equal(new URL(seen.url).pathname, "/api/sdk/v1/translate/text");
  assert.equal(seen.headers.get("Authorization"), "Bearer k-secret");
  assert.equal(seen.headers.get("X-Talking-App-Id"), "demo");
  const body = JSON.parse(await seen.text());
  assert.deepEqual(body, { text: "hi", source: "en", target: "de", return_audio: false });
});

test("401 becomes AuthError", async () => {
  const c = new TalkingClient({
    serverUrl: "http://talk.example",
    apiKey: "bad",
    maxRetries: 0,
    fetch: async () =>
      new Response(JSON.stringify({ error: { code: "unauthorized", message: "nope" } }), { status: 401 }),
  });
  await assert.rejects(() => c.listLanguages(), AuthError);
});

test("retries 429 then succeeds", async () => {
  let n = 0;
  const c = new TalkingClient({
    serverUrl: "http://talk.example",
    apiKey: "k",
    retryBackoff: 0,
    fetch: async () => {
      n += 1;
      if (n === 1) {
        return new Response(JSON.stringify({ error: { code: "rate_limited", message: "slow", retry_after: 0 } }), {
          status: 429,
          headers: { "Retry-After": "0" },
        });
      }
      return new Response(JSON.stringify({ languages: [{ code: "en", en: "English" }] }));
    },
  });
  const langs = await c.listLanguages();
  assert.equal(n, 2);
  assert.equal(langs[0].code, "en");
});

test("RateLimitError after retries exhausted", async () => {
  const c = new TalkingClient({
    serverUrl: "http://talk.example",
    apiKey: "k",
    maxRetries: 0,
    fetch: async () =>
      new Response(JSON.stringify({ error: { code: "rate_limited", message: "slow" } }), { status: 429 }),
  });
  await assert.rejects(() => c.info(), RateLimitError);
});

test("parseSse reads translation events", () => {
  const events = parseSse(
    'event: status\ndata: {"stage":"translating"}\n\nevent: translation\ndata: {"translation":"hallo"}\n\n',
  );
  assert.equal(events.length, 2);
  assert.equal(events[0].event, "status");
  assert.equal(events[1].data.translation, "hallo");
});
