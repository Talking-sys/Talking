/* Sign-in and account security. Cookies + X-CSRF-Token only — never localStorage for tokens. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  root.TalkingAuth = api;
  if (typeof document !== "undefined") {
    document.addEventListener("DOMContentLoaded", function () {
      if (document.body && document.body.getAttribute("data-page") && root.TalkingAuth) {
        root.TalkingAuth.boot();
      }
    });
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const STRINGS = {
    ru: {
      signinTitle: "Вход",
      accountTitle: "Аккаунт",
      accountSub: "безопасность",
      back: "Назад",
      phoneTitle: "Вход по телефону",
      phoneHint: "Код придёт по SMS. Звонок — только если вы сами нажмёте «позвоните мне».",
      country: "Страна",
      phone: "Номер",
      sendSms: "Получить SMS-код",
      codeLabel: "Код из SMS",
      verify: "Войти",
      resend: "Отправить ещё раз",
      resendIn: "Ещё раз через {n} с",
      callMe: "Позвоните мне",
      changeNumber: "Другой номер",
      codeSentSms: "Код отправлен по SMS на {phone}.",
      codeSentCall: "Мы звоним на {phone}. Введите код из звонка.",
      otherTitle: "Другие способы",
      or: "или",
      anonymous: "Продолжить анонимно",
      recoverySummary: "Есть код восстановления",
      recoveryLabel: "Код восстановления",
      recover: "Войти по коду",
      google: "Google",
      apple: "Apple",
      email: "Почта",
      passkey: "Passkey",
      savedTitle: "Сохраните код восстановления",
      savedHint: "Он показывается один раз. Без него анонимный аккаунт не вернуть.",
      copy: "Скопировать",
      copied: "Скопировано",
      savedLabel: "Я сохранил код в надёжном месте",
      continue: "Продолжить",
      sessionsTitle: "Сессии и устройства",
      sessionsHint: "Выйдите с чужого устройства или со всех сразу.",
      methodsTitle: "Способы входа",
      methodsHint: "Последний способ удалить нельзя.",
      logoutAll: "Выйти везде",
      logoutOne: "Выйти",
      currentDevice: "Это устройство",
      unknownDevice: "Неизвестное устройство",
      addPhone: "Добавить телефон",
      link: "Привязать",
      cancel: "Отмена",
      remove: "Удалить",
      lastMethod: "Нельзя удалить последний способ входа",
      lastMethodHint: "Добавьте другой способ, чтобы удалить этот.",
      localMode: "Этот экземпляр — локальный переводчик. Многопользовательский вход выключен.",
      notImplemented: "Этот способ входа ещё не включён на сервере.",
      rateLimit: "Слишком много попыток. Подождите и попробуйте снова.",
      resendCooldown: "Повторная отправка пока недоступна. Подождите минуту.",
      attemptsExhausted: "Слишком много неверных кодов. Запросите новый.",
      invalidCode: "Неверный код.",
      invalidPhone: "Проверьте номер телефона.",
      phoneRequired: "Введите номер телефона.",
      countryBlocked: "Эта страна не поддерживается.",
      phoneTaken: "Этот номер уже привязан к другому аккаунту.",
      freshSession: "Чтобы добавить телефон, войдите заново.",
      smsUnconfigured: "SMS сейчас недоступен.",
      sessionExpired: "Сессия истекла. Войдите снова.",
      notSignedIn: "Нужно войти.",
      codeExpired: "Код устарел. Запросите новый.",
      codeRequired: "Введите код.",
      providerError: "Не удалось отправить код. Попробуйте позже.",
      accountsOnly: "Доступно только в режиме аккаунтов.",
      forbidden: "Запрос отклонён.",
      serverError: "Ошибка сервера. Попробуйте позже.",
      error: "Ошибка",
      logoutAllConfirm: "Выйти на всех устройствах?",
      removeConfirm: "Удалить этот способ входа?",
      signedOut: "Вы вышли.",
      linked: "Телефон привязан.",
      home: "Переводчик",
      chat: "Чат",
      voice: "Голос",
      seen: "активна {when}",
      created: "с {when}",
      providerAnonymous: "Анонимный вход",
      providerPhone: "Телефон",
      providerGoogle: "Google",
      providerApple: "Apple",
      providerEmail: "Почта",
      providerPasskey: "Passkey",
      emptySessions: "Нет активных сессий.",
      emptyMethods: "Нет привязанных способов входа.",
    },
    en: {
      signinTitle: "Sign in",
      accountTitle: "Account",
      accountSub: "security",
      back: "Back",
      phoneTitle: "Sign in with phone",
      phoneHint: "The code arrives by SMS. A voice call is only used if you tap “call me instead”.",
      country: "Country",
      phone: "Number",
      sendSms: "Send SMS code",
      codeLabel: "SMS code",
      verify: "Sign in",
      resend: "Send again",
      resendIn: "Send again in {n}s",
      callMe: "Call me instead",
      changeNumber: "Use another number",
      codeSentSms: "SMS code sent to {phone}.",
      codeSentCall: "Calling {phone}. Enter the code from the call.",
      otherTitle: "Other ways",
      or: "or",
      anonymous: "Continue anonymously",
      recoverySummary: "I have a recovery code",
      recoveryLabel: "Recovery code",
      recover: "Sign in with code",
      google: "Google",
      apple: "Apple",
      email: "Email",
      passkey: "Passkey",
      savedTitle: "Save your recovery code",
      savedHint: "It is shown once. Without it this anonymous account cannot be recovered.",
      copy: "Copy",
      copied: "Copied",
      savedLabel: "I saved this code somewhere safe",
      continue: "Continue",
      sessionsTitle: "Sessions and devices",
      sessionsHint: "Sign out a device you do not recognise, or sign out everywhere.",
      methodsTitle: "Sign-in methods",
      methodsHint: "The last remaining method cannot be removed.",
      logoutAll: "Sign out everywhere",
      logoutOne: "Sign out",
      currentDevice: "This device",
      unknownDevice: "Unknown device",
      addPhone: "Add phone",
      link: "Link",
      cancel: "Cancel",
      remove: "Remove",
      lastMethod: "You cannot remove the last sign-in method",
      lastMethodHint: "Add another method before removing this one.",
      localMode: "This instance is a local translator. Multi-user sign-in is off.",
      notImplemented: "This sign-in method is not available on the server yet.",
      rateLimit: "Too many attempts. Wait and try again.",
      resendCooldown: "Please wait a minute before sending another code.",
      attemptsExhausted: "Too many wrong codes. Request a new one.",
      invalidCode: "That code is incorrect.",
      invalidPhone: "Check the phone number.",
      phoneRequired: "Enter a phone number.",
      countryBlocked: "This country is not supported.",
      phoneTaken: "This number already belongs to another account.",
      freshSession: "Sign in again to add a phone number.",
      smsUnconfigured: "SMS is not available right now.",
      sessionExpired: "Your session expired. Sign in again.",
      notSignedIn: "Please sign in.",
      codeExpired: "That code expired. Request a new one.",
      codeRequired: "Enter the code.",
      providerError: "Could not send the code. Try again later.",
      accountsOnly: "Available only in accounts mode.",
      forbidden: "The request was blocked.",
      serverError: "Server error. Try again later.",
      error: "Error",
      logoutAllConfirm: "Sign out on every device?",
      removeConfirm: "Remove this sign-in method?",
      signedOut: "Signed out.",
      linked: "Phone number linked.",
      home: "Translator",
      chat: "Chat",
      voice: "Voice",
      seen: "active {when}",
      created: "since {when}",
      providerAnonymous: "Anonymous",
      providerPhone: "Phone",
      providerGoogle: "Google",
      providerApple: "Apple",
      providerEmail: "Email",
      providerPasskey: "Passkey",
      emptySessions: "No active sessions.",
      emptyMethods: "No linked sign-in methods.",
    },
  };

  const COUNTRIES = [
    { iso: "RU", dial: "7", en: "Russia", ru: "Россия" },
    { iso: "US", dial: "1", en: "United States", ru: "США" },
    { iso: "GB", dial: "44", en: "United Kingdom", ru: "Великобритания" },
    { iso: "DE", dial: "49", en: "Germany", ru: "Германия" },
    { iso: "FR", dial: "33", en: "France", ru: "Франция" },
    { iso: "ES", dial: "34", en: "Spain", ru: "Испания" },
    { iso: "IT", dial: "39", en: "Italy", ru: "Италия" },
    { iso: "UA", dial: "380", en: "Ukraine", ru: "Украина" },
    { iso: "BY", dial: "375", en: "Belarus", ru: "Беларусь" },
    { iso: "KZ", dial: "7", en: "Kazakhstan", ru: "Казахстан" },
    { iso: "UZ", dial: "998", en: "Uzbekistan", ru: "Узбекистан" },
    { iso: "AM", dial: "374", en: "Armenia", ru: "Армения" },
    { iso: "GE", dial: "995", en: "Georgia", ru: "Грузия" },
    { iso: "AZ", dial: "994", en: "Azerbaijan", ru: "Азербайджан" },
    { iso: "TR", dial: "90", en: "Turkey", ru: "Турция" },
    { iso: "PL", dial: "48", en: "Poland", ru: "Польша" },
    { iso: "NL", dial: "31", en: "Netherlands", ru: "Нидерланды" },
    { iso: "BE", dial: "32", en: "Belgium", ru: "Бельгия" },
    { iso: "AT", dial: "43", en: "Austria", ru: "Австрия" },
    { iso: "CH", dial: "41", en: "Switzerland", ru: "Швейцария" },
    { iso: "SE", dial: "46", en: "Sweden", ru: "Швеция" },
    { iso: "NO", dial: "47", en: "Norway", ru: "Норвегия" },
    { iso: "FI", dial: "358", en: "Finland", ru: "Финляндия" },
    { iso: "DK", dial: "45", en: "Denmark", ru: "Дания" },
    { iso: "CZ", dial: "420", en: "Czechia", ru: "Чехия" },
    { iso: "IE", dial: "353", en: "Ireland", ru: "Ирландия" },
    { iso: "IL", dial: "972", en: "Israel", ru: "Израиль" },
    { iso: "AE", dial: "971", en: "United Arab Emirates", ru: "ОАЭ" },
    { iso: "IN", dial: "91", en: "India", ru: "Индия" },
    { iso: "CN", dial: "86", en: "China", ru: "Китай" },
    { iso: "JP", dial: "81", en: "Japan", ru: "Япония" },
    { iso: "KR", dial: "82", en: "South Korea", ru: "Южная Корея" },
    { iso: "BR", dial: "55", en: "Brazil", ru: "Бразилия" },
    { iso: "MX", dial: "52", en: "Mexico", ru: "Мексика" },
    { iso: "CA", dial: "1", en: "Canada", ru: "Канада" },
    { iso: "AU", dial: "61", en: "Australia", ru: "Австралия" },
    { iso: "NZ", dial: "64", en: "New Zealand", ru: "Новая Зеландия" },
    { iso: "ZA", dial: "27", en: "South Africa", ru: "ЮАР" },
    { iso: "EG", dial: "20", en: "Egypt", ru: "Египет" },
    { iso: "AR", dial: "54", en: "Argentina", ru: "Аргентина" },
    { iso: "ID", dial: "62", en: "Indonesia", ru: "Индонезия" },
    { iso: "TH", dial: "66", en: "Thailand", ru: "Таиланд" },
    { iso: "VN", dial: "84", en: "Vietnam", ru: "Вьетнам" },
    { iso: "PH", dial: "63", en: "Philippines", ru: "Филиппины" },
    { iso: "SG", dial: "65", en: "Singapore", ru: "Сингапур" },
    { iso: "PK", dial: "92", en: "Pakistan", ru: "Пакистан" },
    { iso: "PT", dial: "351", en: "Portugal", ru: "Португалия" },
    { iso: "GR", dial: "30", en: "Greece", ru: "Греция" },
    { iso: "RO", dial: "40", en: "Romania", ru: "Румыния" },
    { iso: "HU", dial: "36", en: "Hungary", ru: "Венгрия" },
  ];

  const ERROR_KEYS = {
    "rate limit": "rateLimit",
    resend_cooldown: "resendCooldown",
    attempts_exhausted: "attemptsExhausted",
    invalid_code: "invalidCode",
    invalid_phone: "invalidPhone",
    phone_required: "phoneRequired",
    country_not_allowed: "countryBlocked",
    phone_taken: "phoneTaken",
    last_method: "lastMethod",
    fresh_session_required: "freshSession",
    sms_unconfigured: "smsUnconfigured",
    not_implemented: "notImplemented",
    invalid_refresh: "sessionExpired",
    refresh_reuse: "sessionExpired",
    "not signed in": "notSignedIn",
    code_expired: "codeExpired",
    code_required: "codeRequired",
    invalid_channel: "error",
    provider_error: "providerError",
    vonage_not_configured: "smsUnconfigured",
    accounts_only: "accountsOnly",
    "csrf token": "forbidden",
    "csrf origin": "forbidden",
    "csrf site": "forbidden",
  };

  const TOKEN_STORAGE_KEYS = /^(access|refresh|csrf|token|session|recovery)/i;

  function detectLang(doc, nav) {
    const html = String((doc && doc.documentElement && doc.documentElement.getAttribute("lang")) || "").toLowerCase();
    if (html.startsWith("en")) return "en";
    if (html.startsWith("ru")) return "ru";
    const loc = String((nav && (nav.language || nav.userLanguage)) || "").toLowerCase();
    if (loc.startsWith("en")) return "en";
    return "ru";
  }

  function t(lang, key, vars) {
    const pack = STRINGS[lang] || STRINGS.ru;
    let out = pack[key] || STRINGS.ru[key] || STRINGS.en[key] || key;
    if (vars) {
      Object.keys(vars).forEach((k) => {
        out = out.split("{" + k + "}").join(String(vars[k]));
      });
    }
    return out;
  }

  function readUiLang(storage, doc, nav) {
    try {
      const stored = storage && storage.getItem && storage.getItem("ui_lang");
      if (stored === "en" || stored === "ru") return stored;
    } catch (e) { /* ignore quota / private mode */ }
    return detectLang(doc, nav);
  }

  function writeUiLang(storage, lang) {
    if (!storage || !storage.setItem) return;
    try { storage.setItem("ui_lang", lang === "en" ? "en" : "ru"); } catch (e) { /* ignore */ }
  }

  function assertNoAuthTokensInStorage(storage) {
    if (!storage) return [];
    const bad = [];
    for (let i = 0; i < storage.length; i++) {
      const key = storage.key(i);
      if (key && TOKEN_STORAGE_KEYS.test(key)) bad.push(key);
    }
    return bad;
  }

  function toE164(dial, national) {
    const raw = String(national || "").trim();
    if (!raw) return "";
    if (raw.startsWith("+")) return "+" + raw.replace(/\D/g, "");
    const n = raw.replace(/\D/g, "");
    const d = String(dial || "").replace(/\D/g, "");
    if (!n) return "";
    return "+" + d + n;
  }

  function nationalDigits(value) {
    return String(value || "").replace(/\D/g, "");
  }

  function normalizeRecovery(raw) {
    const hex = String(raw || "").toLowerCase().replace(/[^0-9a-f]/g, "");
    if (hex.length === 20) {
      return [hex.slice(0, 4), hex.slice(4, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20)].join("-");
    }
    return String(raw || "").trim();
  }

  function filterCountries(all, allowlist) {
    const src = all || COUNTRIES;
    if (!allowlist || !allowlist.length) return src.slice();
    const set = new Set(allowlist.map((x) => String(x).toUpperCase()));
    return src.filter((c) => set.has(c.iso));
  }

  function pickDefaultCountry(countries, defaultRegion, lang) {
    const list = countries || [];
    const want = String(defaultRegion || "").toUpperCase();
    if (want) {
      const hit = list.find((c) => c.iso === want);
      if (hit) return hit.iso;
    }
    if (lang === "ru") {
      const ru = list.find((c) => c.iso === "RU");
      if (ru) return ru.iso;
    }
    const us = list.find((c) => c.iso === "US");
    return (us || list[0] || {}).iso || "US";
  }

  function detailOf(body) {
    if (!body) return "";
    const d = body.detail;
    if (typeof d === "string") return d;
    if (d && typeof d === "object") return d.error || d.code || "";
    return "";
  }

  function errorMessage(lang, status, detail) {
    const code = String(detail || "");
    const key = ERROR_KEYS[code];
    if (key) return t(lang, key);
    if (status === 429) return t(lang, "rateLimit");
    if (status === 403) return t(lang, "forbidden");
    if (status === 401) return t(lang, "notSignedIn");
    if (status >= 500) return t(lang, "serverError");
    return t(lang, "error");
  }

  function canUnlink(methods) {
    return Array.isArray(methods) && methods.length > 1;
  }

  function providerLabel(lang, provider) {
    const key = "provider" + String(provider || "").charAt(0).toUpperCase() + String(provider || "").slice(1);
    if ((STRINGS[lang] || STRINGS.ru)[key] || STRINGS.ru[key]) return t(lang, key);
    return provider || "";
  }

  function formatDevice(label, lang) {
    const raw = String(label || "").trim();
    if (!raw) return t(lang, "unknownDevice");
    const chrome = /(?:HeadlessChrome|Chrome|CriOS)\/(\d+)/.exec(raw);
    const firefox = /Firefox\/(\d+)/.exec(raw);
    const safari = /Safari\//.test(raw) && !/Chrome\/|HeadlessChrome|CriOS/.test(raw);
    let browser = "";
    if (chrome) browser = "Chrome " + chrome[1];
    else if (/AppleWebKit/.test(raw) && /Gecko/.test(raw)) browser = "Chrome";
    else if (firefox) browser = "Firefox " + firefox[1];
    else if (safari) browser = "Safari";
    const os = /Android/.test(raw) ? "Android"
      : /iPhone|iPad/.test(raw) ? "iOS"
      : /Mac OS X/.test(raw) ? "macOS"
      : /Windows NT/.test(raw) ? "Windows"
      : /Linux/.test(raw) ? "Linux" : "";
    if (browser && os) return browser + " · " + os;
    if (browser) return browser;
    return raw.length > 56 ? raw.slice(0, 53) + "…" : raw;
  }

  function formatWhen(iso, lang) {
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    try {
      return new Intl.DateTimeFormat(lang === "en" ? "en" : "ru", {
        dateStyle: "medium", timeStyle: "short",
      }).format(d);
    } catch (e) {
      return d.toISOString();
    }
  }

  function createClient() {
    const mem = { csrf: null };

    async function request(method, url, body) {
      const headers = {};
      if (body !== undefined) headers["Content-Type"] = "application/json";
      if (mem.csrf && method !== "GET" && method !== "HEAD") {
        headers["X-CSRF-Token"] = mem.csrf;
      }
      const res = await fetch(url, {
        method,
        headers,
        credentials: "same-origin",
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
      let data = {};
      const text = await res.text();
      if (text) {
        try { data = JSON.parse(text); } catch (e) { data = { detail: text }; }
      }
      if (data && typeof data.csrf_token === "string" && data.csrf_token) {
        mem.csrf = data.csrf_token;
      }
      return { ok: res.ok, status: res.status, data: data || {} };
    }

    return {
      get csrf() { return mem.csrf; },
      setCsrf(value) { mem.csrf = value || null; },
      request,
      config: () => request("GET", "/api/auth/config"),
      me: () => request("GET", "/api/auth/me"),
      refresh: () => request("POST", "/api/auth/refresh"),
      anonymous: () => request("POST", "/api/auth/anonymous", {}),
      recover: (code) => request("POST", "/api/auth/recover", { code }),
      phoneStart: (phone, channel) => request("POST", "/api/auth/phone/start", { phone, channel: channel || "sms" }),
      phoneVerify: (phone, code) => request("POST", "/api/auth/phone/verify", { phone, code }),
      phoneLink: (phone, code) => request("POST", "/api/auth/phone/link", { phone, code }),
      sessions: () => request("GET", "/api/auth/sessions"),
      methods: () => request("GET", "/api/auth/methods"),
      deleteSession: (id) => request("DELETE", "/api/auth/sessions/" + encodeURIComponent(id)),
      deleteMethod: (id) => request("DELETE", "/api/auth/methods/" + encodeURIComponent(id)),
      logout: () => request("POST", "/api/auth/logout", {}),
      logoutAll: () => request("POST", "/api/auth/logout-all", {}),
    };
  }

  function $(id) {
    return document.getElementById(id);
  }

  function show(el, on) {
    if (!el) return;
    el.hidden = !on;
  }

  function setText(id, value) {
    const el = $(id);
    if (el) el.textContent = value;
  }

  function boot() {
    const page = document.body.getAttribute("data-page");
    const storage = window.localStorage;
    const client = createClient();
    const ui = {
      lang: readUiLang(storage, document, navigator),
      config: null,
      phone: "",
      channel: "sms",
      cooldownTimer: 0,
      cooldownUntil: 0,
    };

    function banner(kind, text) {
      const err = $("errorBanner");
      const ok = $("okBanner");
      if (kind === "error") {
        if (err) { err.textContent = text; err.hidden = false; }
        if (ok) ok.hidden = true;
      } else if (kind === "ok") {
        if (ok) { ok.textContent = text; ok.hidden = false; }
        if (err) err.hidden = true;
      } else {
        if (err) err.hidden = true;
        if (ok) ok.hidden = true;
      }
    }

    function fail(res) {
      banner("error", errorMessage(ui.lang, res.status, detailOf(res.data)));
    }

    function applyI18n() {
      document.documentElement.lang = ui.lang;
      writeUiLang(storage, ui.lang);
      const ru = $("uiLangRu");
      const en = $("uiLangEn");
      if (ru) ru.setAttribute("aria-pressed", ui.lang === "ru" ? "true" : "false");
      if (en) en.setAttribute("aria-pressed", ui.lang === "en" ? "true" : "false");
      const back = $("navBack");
      if (back) back.setAttribute("aria-label", t(ui.lang, "back"));
      setText("hdrSub", page === "account" ? t(ui.lang, "accountSub") : t(ui.lang, "signinTitle"));
      setText("hdrTitle", t(ui.lang, page === "account" ? "accountTitle" : "signinTitle"));
      setText("phoneTitle", t(ui.lang, "phoneTitle"));
      setText("phoneHint", t(ui.lang, "phoneHint"));
      setText("lblCountry", t(ui.lang, "country"));
      setText("lblPhone", t(ui.lang, "phone"));
      setText("btnSendCode", t(ui.lang, "sendSms"));
      setText("lblCode", t(ui.lang, "codeLabel"));
      setText("btnVerify", t(ui.lang, page === "account" ? "link" : "verify"));
      setText("btnCallMe", t(ui.lang, "callMe"));
      setText("btnChangeNumber", t(ui.lang, "changeNumber"));
      setText("otherTitle", t(ui.lang, "otherTitle"));
      setText("orDivider", t(ui.lang, "or"));
      setText("btnAnonymous", t(ui.lang, "anonymous"));
      setText("recoverySummary", t(ui.lang, "recoverySummary"));
      setText("lblRecovery", t(ui.lang, "recoveryLabel"));
      setText("btnRecover", t(ui.lang, "recover"));
      setText("oauthGoogle", t(ui.lang, "google"));
      setText("oauthApple", t(ui.lang, "apple"));
      setText("oauthEmail", t(ui.lang, "email"));
      setText("oauthPasskey", t(ui.lang, "passkey"));
      setText("savedTitle", t(ui.lang, "savedTitle"));
      setText("savedHint", t(ui.lang, "savedHint"));
      setText("btnCopyRecovery", t(ui.lang, "copy"));
      setText("savedLabel", t(ui.lang, "savedLabel"));
      setText("btnSavedContinue", t(ui.lang, "continue"));
      setText("sessionsTitle", t(ui.lang, "sessionsTitle"));
      setText("sessionsHint", t(ui.lang, "sessionsHint"));
      setText("methodsTitle", t(ui.lang, "methodsTitle"));
      setText("methodsHint", t(ui.lang, "methodsHint"));
      setText("btnLogoutAll", t(ui.lang, "logoutAll"));
      setText("btnAddPhone", t(ui.lang, "addPhone"));
      setText("btnCancelLink", t(ui.lang, "cancel"));
      setText("ftHome", t(ui.lang, "home"));
      setText("ftChat", t(ui.lang, "chat"));
      setText("ftVoice", t(ui.lang, "voice"));
      setText("localBanner", t(ui.lang, "localMode"));
      fillCountries();
      updateResendLabel();
      if (page === "account") {
        renderAccount();
      }
    }

    function enabledMethods() {
      return (ui.config && ui.config.methods) || {};
    }

    function fillCountries() {
      const sel = $("phoneCountry");
      if (!sel) return;
      const allow = (ui.config && ui.config.country_allowlist) || [];
      const rows = filterCountries(COUNTRIES, allow);
      const prev = sel.value;
      const lang = ui.lang;
      sel.innerHTML = "";
      rows.forEach((c) => {
        const opt = document.createElement("option");
        opt.value = c.iso;
        opt.textContent = (lang === "en" ? c.en : c.ru) + " +" + c.dial;
        opt.dataset.dial = c.dial;
        sel.appendChild(opt);
      });
      const pick = prev && rows.some((c) => c.iso === prev)
        ? prev
        : pickDefaultCountry(rows, ui.config && ui.config.default_region, ui.lang);
      if (pick) sel.value = pick;
    }

    function currentDial() {
      const sel = $("phoneCountry");
      if (!sel || !sel.selectedOptions.length) return "1";
      return sel.selectedOptions[0].dataset.dial || "1";
    }

    function currentPhone() {
      return toE164(currentDial(), ($("phoneNational") && $("phoneNational").value) || "");
    }

    function updateResendLabel() {
      const btn = $("btnResend");
      if (!btn) return;
      const left = Math.max(0, Math.ceil((ui.cooldownUntil - Date.now()) / 1000));
      if (left > 0) {
        btn.disabled = true;
        btn.textContent = t(ui.lang, "resendIn", { n: left });
      } else {
        btn.disabled = false;
        btn.textContent = t(ui.lang, "resend");
      }
    }

    function startCooldown(seconds) {
      const sec = Number(seconds) > 0 ? Number(seconds) : 60;
      ui.cooldownUntil = Date.now() + sec * 1000;
      updateResendLabel();
      if (ui.cooldownTimer) clearInterval(ui.cooldownTimer);
      ui.cooldownTimer = setInterval(() => {
        updateResendLabel();
        if (Date.now() >= ui.cooldownUntil && ui.cooldownTimer) {
          clearInterval(ui.cooldownTimer);
          ui.cooldownTimer = 0;
        }
      }, 500);
    }

    function applyOAuth() {
      const methods = enabledMethods();
      const g = $("oauthGoogle");
      const a = $("oauthApple");
      const e = $("oauthEmail");
      const p = $("oauthPasskey");
      const box = $("oauthBox");
      const row = $("oauthRow");
      show(g, !!methods.google);
      show(a, !!methods.apple);
      show(e, !!methods.email);
      show(p, !!methods.passkey);
      const any = !!(methods.google || methods.apple || methods.email || methods.passkey);
      show(box, any);
      show($("orDivider"), any);
      if (row) row.classList.toggle("has-both", !!(methods.google && methods.apple));
    }

    function accountsMode() {
      return ui.config && ui.config.auth_mode === "accounts";
    }

    function localLock() {
      const local = !accountsMode();
      show($("localBanner"), local);
      ["phoneForm", "codeForm", "btnAnonymous", "recoveryForm", "btnAddPhone"].forEach((id) => {
        const el = $(id);
        if (!el) return;
        if (el.tagName === "FORM") {
          Array.from(el.elements).forEach((n) => { n.disabled = local; });
        } else {
          el.disabled = local;
        }
      });
      return local;
    }

    async function loadMe() {
      let res = await client.me();
      if (!res.ok && res.status === 401) {
        const ref = await client.refresh();
        if (ref.ok) res = await client.me();
      }
      if (res.ok && res.data && res.data.csrf_token) client.setCsrf(res.data.csrf_token);
      return res;
    }

    async function startPhone(channel) {
      banner();
      const phone = currentPhone();
      if (!phone) {
        banner("error", t(ui.lang, "phoneRequired"));
        return;
      }
      ui.phone = phone;
      ui.channel = channel === "call" ? "call" : "sms";
      const res = await client.phoneStart(phone, ui.channel);
      if (!res.ok) {
        fail(res);
        if (res.status === 429) {
          const wait = (res.data && res.data.resend_after) || 60;
          startCooldown(wait);
        }
        return;
      }
      startCooldown(res.data.resend_after || 60);
      show($("phoneForm"), false);
      show($("codeForm"), true);
      show($("linkPhoneForm"), false);
      show($("linkCodeForm"), true);
      const hint = $("codeHint");
      if (hint) {
        hint.textContent = t(ui.lang, ui.channel === "call" ? "codeSentCall" : "codeSentSms", { phone });
      }
      const input = $("codeInput");
      if (input) input.focus();
    }

    async function verifyPhone(link) {
      banner();
      const code = (($("codeInput") && $("codeInput").value) || "").replace(/\s/g, "");
      const phone = ui.phone || currentPhone();
      const res = link ? await client.phoneLink(phone, code) : await client.phoneVerify(phone, code);
      if (!res.ok) {
        fail(res);
        return;
      }
      if (link) {
        banner("ok", t(ui.lang, "linked"));
        show($("linkCodeForm"), false);
        show($("linkPhoneForm"), false);
        show($("btnAddPhone"), true);
        await renderAccount();
        return;
      }
      window.location.href = "/account";
    }

    function showRecoveryOnce(code) {
      show($("signinGrid"), false);
      show($("recoveryOnce"), true);
      const pre = $("recoveryCode");
      if (pre) pre.textContent = code;
      const check = $("savedCheck");
      const cont = $("btnSavedContinue");
      if (check) check.checked = false;
      if (cont) cont.disabled = true;
    }

    async function renderAccount() {
      if (page !== "account") return;
      const sessBox = $("sessionList");
      const methBox = $("methodList");
      if (!accountsMode()) {
        if (sessBox) sessBox.innerHTML = '<p class="empty-row">' + t(ui.lang, "emptySessions") + "</p>";
        if (methBox) methBox.innerHTML = '<p class="empty-row">' + t(ui.lang, "emptyMethods") + "</p>";
        const all = $("btnLogoutAll");
        if (all) all.disabled = true;
        const add = $("btnAddPhone");
        if (add) add.disabled = true;
        return;
      }
      const [sess, meth] = await Promise.all([client.sessions(), client.methods()]);
      if (sess.status === 401 || meth.status === 401) {
        window.location.href = "/signin";
        return;
      }
      if (!sess.ok) { fail(sess); return; }
      if (!meth.ok) { fail(meth); return; }
      const sessions = (sess.data && sess.data.sessions) || [];
      const methods = (meth.data && meth.data.methods) || [];
      if (sessBox) {
        sessBox.innerHTML = "";
        if (!sessions.length) {
          sessBox.innerHTML = '<p class="empty-row">' + t(ui.lang, "emptySessions") + "</p>";
        }
        sessions.forEach((row) => {
          const item = document.createElement("article");
          item.className = "list-item";
          item.dataset.sessionId = row.id;
          const title = document.createElement("div");
          title.className = "title";
          title.textContent = formatDevice(row.device_label, ui.lang);
          const meta = document.createElement("div");
          meta.className = "meta";
          const seen = document.createElement("span");
          seen.textContent = t(ui.lang, "seen", { when: formatWhen(row.last_seen_at, ui.lang) });
          meta.appendChild(seen);
          if (row.current) {
            const badge = document.createElement("span");
            badge.className = "badge";
            badge.textContent = t(ui.lang, "currentDevice");
            meta.appendChild(badge);
          }
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "btn";
          btn.dataset.action = "logout-one";
          btn.dataset.id = row.id;
          btn.textContent = t(ui.lang, "logoutOne");
          item.appendChild(title);
          item.appendChild(meta);
          item.appendChild(btn);
          sessBox.appendChild(item);
        });
      }
      if (methBox) {
        methBox.innerHTML = "";
        if (!methods.length) {
          methBox.innerHTML = '<p class="empty-row">' + t(ui.lang, "emptyMethods") + "</p>";
        }
        const allowRemove = canUnlink(methods);
        methods.forEach((row) => {
          const item = document.createElement("article");
          item.className = "list-item";
          item.dataset.methodId = row.id;
          const title = document.createElement("div");
          title.className = "title";
          title.textContent = providerLabel(ui.lang, row.provider);
          const meta = document.createElement("div");
          meta.className = "meta";
          meta.textContent = t(ui.lang, "created", { when: formatWhen(row.created_at, ui.lang) });
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "btn";
          btn.dataset.action = "remove-method";
          btn.dataset.id = row.id;
          btn.textContent = t(ui.lang, "remove");
          if (!allowRemove) {
            btn.disabled = true;
            btn.title = t(ui.lang, "lastMethodHint");
          }
          item.appendChild(title);
          item.appendChild(meta);
          item.appendChild(btn);
          methBox.appendChild(item);
        });
      }
    }

    function bindLang() {
      const ru = $("uiLangRu");
      const en = $("uiLangEn");
      if (ru) ru.addEventListener("click", () => { ui.lang = "ru"; applyI18n(); });
      if (en) en.addEventListener("click", () => { ui.lang = "en"; applyI18n(); });
    }

    function bindOAuthClicks() {
      ["oauthGoogle", "oauthApple", "oauthEmail", "oauthPasskey"].forEach((id) => {
        const el = $(id);
        if (!el) return;
        el.addEventListener("click", () => banner("error", t(ui.lang, "notImplemented")));
      });
    }

    async function initSignin() {
      const cfg = await client.config();
      ui.config = cfg.data || {};
      applyI18n();
      applyOAuth();
      if (localLock()) return;
      const me = await loadMe();
      if (me.ok && me.data && me.data.auth_mode === "accounts") {
        window.location.replace("/account");
        return;
      }
      $("phoneForm").addEventListener("submit", (ev) => {
        ev.preventDefault();
        startPhone("sms");
      });
      $("codeForm").addEventListener("submit", (ev) => {
        ev.preventDefault();
        verifyPhone(false);
      });
      $("btnResend").addEventListener("click", () => startPhone("sms"));
      $("btnCallMe").addEventListener("click", () => startPhone("call"));
      $("btnChangeNumber").addEventListener("click", () => {
        show($("codeForm"), false);
        show($("phoneForm"), true);
        banner();
      });
      $("btnAnonymous").addEventListener("click", async () => {
        banner();
        const res = await client.anonymous();
        if (!res.ok) { fail(res); return; }
        showRecoveryOnce(res.data.recovery_code || "");
      });
      $("recoveryForm").addEventListener("submit", async (ev) => {
        ev.preventDefault();
        banner();
        const code = normalizeRecovery($("recoveryInput").value);
        const res = await client.recover(code);
        if (!res.ok) { fail(res); return; }
        window.location.href = "/account";
      });
      $("btnCopyRecovery").addEventListener("click", async () => {
        const text = ($("recoveryCode") && $("recoveryCode").textContent) || "";
        try {
          if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(text);
          }
          banner("ok", t(ui.lang, "copied"));
        } catch (e) {
          banner("ok", t(ui.lang, "copied"));
        }
      });
      $("savedCheck").addEventListener("change", () => {
        $("btnSavedContinue").disabled = !$("savedCheck").checked;
      });
      $("btnSavedContinue").addEventListener("click", () => {
        window.location.href = "/account";
      });
      bindOAuthClicks();
    }

    async function initAccount() {
      const cfg = await client.config();
      ui.config = cfg.data || {};
      applyI18n();
      if (localLock()) {
        applyI18n();
        return;
      }
      const me = await loadMe();
      if (!me.ok) {
        window.location.replace("/signin");
        return;
      }
      await renderAccount();
      $("btnLogoutAll").addEventListener("click", async () => {
        if (!window.confirm(t(ui.lang, "logoutAllConfirm"))) return;
        const res = await client.logoutAll();
        if (!res.ok) { fail(res); return; }
        window.location.href = "/signin";
      });
      $("sessionList").addEventListener("click", async (ev) => {
        const btn = ev.target.closest("[data-action=logout-one]");
        if (!btn) return;
        const res = await client.deleteSession(btn.dataset.id);
        if (!res.ok) { fail(res); return; }
        const me2 = await client.me();
        if (!me2.ok) {
          window.location.href = "/signin";
          return;
        }
        banner("ok", t(ui.lang, "signedOut"));
        await renderAccount();
      });
      $("methodList").addEventListener("click", async (ev) => {
        const btn = ev.target.closest("[data-action=remove-method]");
        if (!btn || btn.disabled) return;
        if (!window.confirm(t(ui.lang, "removeConfirm"))) return;
        const res = await client.deleteMethod(btn.dataset.id);
        if (!res.ok) { fail(res); return; }
        await renderAccount();
      });
      $("btnAddPhone").addEventListener("click", () => {
        show($("btnAddPhone"), false);
        show($("linkPhoneForm"), true);
        fillCountries();
      });
      $("linkPhoneForm").addEventListener("submit", (ev) => {
        ev.preventDefault();
        startPhone("sms");
      });
      $("linkCodeForm").addEventListener("submit", (ev) => {
        ev.preventDefault();
        verifyPhone(true);
      });
      $("btnResend").addEventListener("click", () => startPhone("sms"));
      $("btnCallMe").addEventListener("click", () => startPhone("call"));
      $("btnCancelLink").addEventListener("click", () => {
        show($("linkCodeForm"), false);
        show($("linkPhoneForm"), false);
        show($("btnAddPhone"), true);
        banner();
      });
    }

    bindLang();
    if (page === "signin") initSignin();
    else if (page === "account") initAccount();
  }

  return {
    STRINGS,
    COUNTRIES,
    ERROR_KEYS,
    detectLang,
    t,
    readUiLang,
    writeUiLang,
    assertNoAuthTokensInStorage,
    toE164,
    nationalDigits,
    normalizeRecovery,
    filterCountries,
    pickDefaultCountry,
    detailOf,
    errorMessage,
    canUnlink,
    providerLabel,
    formatWhen,
    formatDevice,
    createClient,
    boot,
  };
});
