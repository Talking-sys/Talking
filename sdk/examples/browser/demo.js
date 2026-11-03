/**
 * Browser snippet: translate a line of game dialogue.
 * Paste your server URL and API key into the form — they are not stored in this file.
 *
 * Serve this folder over HTTP (or open from the Talking origin) and set
 * APP_CORS_ORIGINS on the server if the page is cross-origin.
 */
import { TalkingClient } from "../../js/src/index.js";

const $ = (id) => document.getElementById(id);

$("run").addEventListener("click", async () => {
  const out = $("out");
  out.textContent = "…";
  const serverUrl = $("server").value.trim();
  const apiKey = $("key").value.trim();
  const appId = $("app").value.trim();
  const target = $("target").value.trim() || "ru";
  const text = $("text").value;
  if (!serverUrl || !apiKey) {
    out.textContent = "Enter server URL and API key (from your environment / dashboard).";
    return;
  }
  const client = new TalkingClient({ serverUrl, apiKey, appId, targetLanguage: target });
  try {
    const langs = await client.listLanguages();
    const result = await client.translateText(text, { source: "auto", target });
    out.textContent = JSON.stringify(
      {
        languages: langs.length,
        source: result.source,
        target: result.target,
        translation: result.translation,
        provider: result.provider,
      },
      null,
      2,
    );
  } catch (err) {
    out.textContent = `${err.name || "Error"}: ${err.message} (${err.code || "?"})`;
  }
});
