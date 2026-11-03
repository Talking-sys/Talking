"use strict";
const assert = require("assert");
const path = require("path");
const auth = require(path.join(__dirname, "../../talking/static/auth.js"));

function testI18n() {
  assert.strictEqual(auth.t("ru", "anonymous"), "Продолжить анонимно");
  assert.strictEqual(auth.t("en", "anonymous"), "Continue anonymously");
  assert.strictEqual(auth.t("en", "resendIn", { n: 12 }), "Send again in 12s");
  assert.strictEqual(auth.t("ru", "resendIn", { n: 5 }), "Ещё раз через 5 с");
  assert.strictEqual(auth.detectLang({ documentElement: { getAttribute: () => "en" } }), "en");
  assert.strictEqual(auth.detectLang({ documentElement: { getAttribute: () => "ru" } }), "ru");
}

function testPhoneE164() {
  assert.strictEqual(auth.toE164("1", "2015550123"), "+12015550123");
  assert.strictEqual(auth.toE164("7", "+7 999 123-45-67"), "+79991234567");
  assert.strictEqual(auth.toE164("44", ""), "");
  assert.strictEqual(auth.nationalDigits("+1 (201) 555-0123"), "12015550123");
}

function testCountries() {
  const only = auth.filterCountries(auth.COUNTRIES, ["DE", "US"]);
  assert.deepStrictEqual(only.map((c) => c.iso).sort(), ["DE", "US"]);
  assert.strictEqual(auth.pickDefaultCountry(only, "DE", "ru"), "DE");
  assert.strictEqual(auth.pickDefaultCountry(only, "", "ru"), "US");
  const ru = auth.filterCountries(auth.COUNTRIES, ["RU"]);
  assert.strictEqual(auth.pickDefaultCountry(ru, "", "ru"), "RU");
}

function testRecovery() {
  assert.strictEqual(auth.normalizeRecovery("abcd-ef01-2345-6789-abcd"), "abcd-ef01-2345-6789-abcd");
  assert.strictEqual(auth.normalizeRecovery("ABCDef0123456789ABCD"), "abcd-ef01-2345-6789-abcd");
}

function testErrors() {
  assert.strictEqual(auth.errorMessage("en", 429, "rate limit"), "Too many attempts. Wait and try again.");
  assert.strictEqual(auth.errorMessage("ru", 429, "resend_cooldown").length > 10, true);
  assert.strictEqual(auth.errorMessage("en", 429, "attempts_exhausted").includes("wrong"), true);
  assert.strictEqual(auth.errorMessage("en", 400, "last_method").includes("last"), true);
  assert.strictEqual(auth.errorMessage("en", 409, "phone_taken").includes("another"), true);
  assert.ok(auth.ERROR_KEYS.resend_cooldown);
  assert.ok(auth.ERROR_KEYS["rate limit"]);
}

function testUnlink() {
  assert.strictEqual(auth.canUnlink([]), false);
  assert.strictEqual(auth.canUnlink([{ id: "a" }]), false);
  assert.strictEqual(auth.canUnlink([{ id: "a" }, { id: "b" }]), true);
}

function testNoTokensInStorage() {
  const store = {
    0: "ui_lang",
    1: "csrf_token",
    2: "refresh",
    length: 3,
    key(i) { return this[i]; },
  };
  const bad = auth.assertNoAuthTokensInStorage(store);
  assert.deepStrictEqual(bad.sort(), ["csrf_token", "refresh"]);
  const ok = { 0: "ui_lang", 1: "target_lang", length: 2, key(i) { return this[i]; } };
  assert.deepStrictEqual(auth.assertNoAuthTokensInStorage(ok), []);
}

function testProviderLabel() {
  assert.strictEqual(auth.providerLabel("en", "phone"), "Phone");
  assert.strictEqual(auth.providerLabel("ru", "anonymous"), "Анонимный вход");
}

function testDeviceLabel() {
  const ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";
  assert.strictEqual(auth.formatDevice(ua, "en"), "Chrome 120 · Linux");
  const truncated = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessC";
  assert.strictEqual(auth.formatDevice(truncated, "en"), "Chrome · Linux");
  assert.strictEqual(auth.formatDevice("", "en"), "Unknown device");
}

testI18n();
testPhoneE164();
testCountries();
testRecovery();
testErrors();
testUnlink();
testNoTokensInStorage();
testProviderLabel();
testDeviceLabel();
console.log("ok");
