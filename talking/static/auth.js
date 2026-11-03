/* Passkey (WebAuthn) helpers for the static frontend. Sessions stay in httpOnly cookies. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.TalkingAuth = api;
  if (typeof document !== "undefined") {
    const start = function () { try { api.bindPanel(document); } catch (_) {} };
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
    else start();
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  function b64urlToBuf(s) {
    const pad = "=".repeat((4 - (s.length % 4)) % 4);
    const b64 = (s + pad).replace(/-/g, "+").replace(/_/g, "/");
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out.buffer;
  }

  function bufToB64url(buf) {
    const bytes = buf instanceof ArrayBuffer ? new Uint8Array(buf) : new Uint8Array(buf.buffer || buf);
    let bin = "";
    for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
  }

  function revivePublicKey(pk) {
    if (!pk || typeof pk !== "object") return pk;
    const out = Object.assign({}, pk);
    if (typeof out.challenge === "string") out.challenge = b64urlToBuf(out.challenge);
    if (out.user && typeof out.user.id === "string") {
      out.user = Object.assign({}, out.user, { id: b64urlToBuf(out.user.id) });
    }
    const reviveList = (list) => (list || []).map((c) => Object.assign({}, c, {
      id: typeof c.id === "string" ? b64urlToBuf(c.id) : c.id,
    }));
    if (Array.isArray(out.excludeCredentials)) out.excludeCredentials = reviveList(out.excludeCredentials);
    if (Array.isArray(out.allowCredentials)) out.allowCredentials = reviveList(out.allowCredentials);
    return out;
  }

  function serializeCredential(cred) {
    if (!cred) return {};
    if (typeof cred.toJSON === "function") return cred.toJSON();
    const resp = cred.response || {};
    const body = {
      id: cred.id,
      rawId: bufToB64url(cred.rawId),
      type: cred.type || "public-key",
      clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
      response: {
        clientDataJSON: bufToB64url(resp.clientDataJSON),
      },
    };
    if (cred.authenticatorAttachment) body.authenticatorAttachment = cred.authenticatorAttachment;
    if (resp.attestationObject) body.response.attestationObject = bufToB64url(resp.attestationObject);
    if (resp.authenticatorData) body.response.authenticatorData = bufToB64url(resp.authenticatorData);
    if (resp.signature) body.response.signature = bufToB64url(resp.signature);
    if (resp.userHandle) body.response.userHandle = bufToB64url(resp.userHandle);
    if (typeof resp.getTransports === "function") body.response.transports = resp.getTransports();
    return body;
  }

  function passkeySupported() {
    return typeof navigator !== "undefined"
      && navigator.credentials
      && typeof navigator.credentials.create === "function"
      && typeof window !== "undefined"
      && window.PublicKeyCredential;
  }

  function detail(body, fallback) {
    if (!body) return fallback;
    if (typeof body.detail === "string") return body.detail;
    if (body.detail && body.detail.code) return body.detail.code;
    return fallback;
  }

  function createClient(fetchFn, credsApi) {
    let csrf = "";
    const f = fetchFn || (typeof fetch === "function" ? fetch.bind(typeof window !== "undefined" ? window : globalThis) : null);
    const nav = credsApi || (typeof navigator !== "undefined" ? navigator.credentials : null);

    async function request(path, opts) {
      const headers = Object.assign({ Accept: "application/json" }, (opts && opts.headers) || {});
      if (opts && opts.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
      if (csrf && opts && opts.method && opts.method !== "GET") headers["X-CSRF-Token"] = csrf;
      const res = await f(path, Object.assign({ credentials: "include" }, opts, { headers }));
      let body = {};
      try { body = await res.json(); } catch (_) { body = {}; }
      if (body && body.csrf_token) csrf = body.csrf_token;
      if (!res.ok) {
        const err = new Error(detail(body, "http_" + res.status));
        err.status = res.status;
        err.body = body;
        throw err;
      }
      return body;
    }

    async function me() {
      const body = await request("/api/auth/me", { method: "GET" });
      if (body && body.csrf_token) csrf = body.csrf_token;
      return body;
    }

    async function anonymous() {
      return request("/api/auth/anonymous", { method: "POST", body: "{}" });
    }

    async function logout() {
      return request("/api/auth/logout", { method: "POST", body: "{}" });
    }

    async function registerPasskey() {
      if (!nav || typeof nav.create !== "function") throw new Error("passkey_unsupported");
      const opts = await request("/api/auth/passkey/register/options", { method: "POST", body: "{}" });
      const cred = await nav.create({ publicKey: revivePublicKey(opts.publicKey) });
      return request("/api/auth/passkey/register", {
        method: "POST",
        body: JSON.stringify({ challenge_id: opts.challenge_id, credential: serializeCredential(cred) }),
      });
    }

    async function authenticatePasskey() {
      if (!nav || typeof nav.get !== "function") throw new Error("passkey_unsupported");
      const opts = await request("/api/auth/passkey/authenticate/options", { method: "POST", body: "{}" });
      const cred = await nav.get({ publicKey: revivePublicKey(opts.publicKey) });
      return request("/api/auth/passkey/authenticate", {
        method: "POST",
        body: JSON.stringify({ challenge_id: opts.challenge_id, credential: serializeCredential(cred) }),
      });
    }

    async function deleteMethod(id) {
      return request("/api/auth/methods/" + encodeURIComponent(id), { method: "DELETE" });
    }

    return {
      me, anonymous, logout, registerPasskey, authenticatePasskey, deleteMethod,
      get csrf() { return csrf; },
      set csrf(v) { csrf = v || ""; },
    };
  }

  function bindPanel(doc, client) {
    const el = (id) => doc.getElementById(id);
    const panel = el("authPanel");
    if (!panel) return;
    const status = el("authStatus");
    const msg = el("authMsg");
    const methods = el("authMethods");
    const btnAnon = el("authAnon");
    const btnIn = el("authPasskeyIn");
    const btnAdd = el("authPasskeyAdd");
    const btnOut = el("authLogout");
    const api = client || createClient();

    function setMsg(text) {
      if (msg) msg.textContent = text || "";
    }

    function render(meBody) {
      const accounts = meBody && meBody.auth_mode === "accounts";
      const signed = accounts && meBody && meBody.id;
      if (status) {
        if (!accounts) status.textContent = "Локальный режим — passkeys выключены.";
        else if (signed) status.textContent = "Вы вошли. Passkey можно добавить к аккаунту.";
        else status.textContent = "Войдите с passkey или создайте анонимный аккаунт.";
      }
      if (btnAnon) btnAnon.hidden = !accounts || !!signed;
      if (btnIn) btnIn.hidden = !accounts || !!signed;
      if (btnAdd) btnAdd.hidden = !accounts || !signed;
      if (btnOut) btnOut.hidden = !accounts || !signed;
      if (methods) {
        methods.innerHTML = "";
        const rows = (meBody && meBody.methods) || [];
        methods.hidden = !signed || rows.length === 0;
        rows.forEach((m) => {
          const li = doc.createElement("li");
          const label = m.label ? m.provider + " · " + m.label : m.provider;
          li.textContent = label + (m.cloned ? " (клон)" : "");
          const rm = doc.createElement("button");
          rm.type = "button";
          rm.className = "auth-remove";
          rm.textContent = "Удалить";
          rm.addEventListener("click", async () => {
            try {
              await api.deleteMethod(m.id);
              setMsg("");
              await refresh();
            } catch (err) {
              setMsg(err.message === "last_method" ? "Нельзя удалить последний способ входа" : String(err.message || err));
            }
          });
          li.appendChild(rm);
          methods.appendChild(li);
        });
      }
    }

    async function refresh() {
      try {
        const meBody = await api.me();
        render(meBody);
      } catch (_) {
        render({ auth_mode: "accounts" });
      }
    }

    if (btnAnon) btnAnon.addEventListener("click", async () => {
      try {
        await api.anonymous();
        setMsg("Аккаунт создан. Сохраните код восстановления, если он показан.");
        await refresh();
      } catch (err) { setMsg(String(err.message || err)); }
    });
    if (btnIn) btnIn.addEventListener("click", async () => {
      try {
        await api.authenticatePasskey();
        setMsg("Вход с passkey выполнен.");
        await refresh();
      } catch (err) { setMsg(String(err.message || err)); }
    });
    if (btnAdd) btnAdd.addEventListener("click", async () => {
      try {
        await api.registerPasskey();
        setMsg("Passkey добавлен.");
        await refresh();
      } catch (err) { setMsg(String(err.message || err)); }
    });
    if (btnOut) btnOut.addEventListener("click", async () => {
      try {
        await api.logout();
        setMsg("Вы вышли.");
        await refresh();
      } catch (err) { setMsg(String(err.message || err)); }
    });

    refresh();
    return { refresh, render };
  }

  return {
    b64urlToBuf, bufToB64url, revivePublicKey, serializeCredential,
    passkeySupported, createClient, bindPanel,
  };
});
