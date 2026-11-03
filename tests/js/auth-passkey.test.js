"use strict";
const assert = require("assert");
const path = require("path");
const auth = require(path.join(__dirname, "../../talking/static/auth.js"));

function testB64RoundTrip() {
  const buf = new Uint8Array([1, 2, 250, 255, 0]).buffer;
  const s = auth.bufToB64url(buf);
  const back = new Uint8Array(auth.b64urlToBuf(s));
  assert.deepStrictEqual(Array.from(back), [1, 2, 250, 255, 0]);
  assert.strictEqual(s.includes("+"), false);
  assert.strictEqual(s.includes("/"), false);
}

function testRevivePublicKey() {
  const pk = auth.revivePublicKey({
    challenge: auth.bufToB64url(new Uint8Array([9, 8, 7]).buffer),
    rp: { id: "testserver", name: "Talking" },
    user: { id: auth.bufToB64url(new Uint8Array([1, 2, 3]).buffer), name: "u" },
    excludeCredentials: [{ type: "public-key", id: auth.bufToB64url(new Uint8Array([4]).buffer) }],
    allowCredentials: [{ type: "public-key", id: auth.bufToB64url(new Uint8Array([5]).buffer) }],
  });
  assert.ok(pk.challenge instanceof ArrayBuffer);
  assert.deepStrictEqual(Array.from(new Uint8Array(pk.challenge)), [9, 8, 7]);
  assert.ok(pk.user.id instanceof ArrayBuffer);
  assert.deepStrictEqual(Array.from(new Uint8Array(pk.excludeCredentials[0].id)), [4]);
  assert.deepStrictEqual(Array.from(new Uint8Array(pk.allowCredentials[0].id)), [5]);
}

function testSerializeCredential() {
  const raw = new Uint8Array([10, 11, 12]).buffer;
  const json = auth.serializeCredential({
    id: "abc",
    rawId: raw,
    type: "public-key",
    response: {
      clientDataJSON: new Uint8Array([1]).buffer,
      attestationObject: new Uint8Array([2]).buffer,
    },
    getClientExtensionResults() { return {}; },
  });
  assert.strictEqual(json.id, "abc");
  assert.strictEqual(json.rawId, auth.bufToB64url(raw));
  assert.ok(json.response.clientDataJSON);
  assert.ok(json.response.attestationObject);
}

function testCreateClientSetsCsrfAndSerializes() {
  const calls = [];
  const fakeCred = {
    id: "cid",
    rawId: new Uint8Array([1, 2]).buffer,
    type: "public-key",
    toJSON() {
      return { id: "cid", rawId: "AQI", type: "public-key", response: { clientDataJSON: "e30" } };
    },
  };
  const fetchFn = async (url, opts) => {
    calls.push({ url, opts });
    if (url.endsWith("/authenticate/options")) {
      return {
        ok: true,
        json: async () => ({
          challenge_id: "ch1",
          publicKey: { challenge: auth.bufToB64url(new Uint8Array([9]).buffer), rpId: "testserver" },
        }),
      };
    }
    return {
      ok: true,
      json: async () => ({ ok: true, csrf_token: "csrf-from-server", user: { id: "u1" } }),
    };
  };
  const credsApi = {
    async get(opts) {
      assert.ok(opts.publicKey.challenge instanceof ArrayBuffer);
      return fakeCred;
    },
  };
  const client = auth.createClient(fetchFn, credsApi);
  return client.authenticatePasskey().then((body) => {
    assert.strictEqual(body.ok, true);
    assert.strictEqual(client.csrf, "csrf-from-server");
    const finish = calls.find((c) => c.url.endsWith("/authenticate"));
    const payload = JSON.parse(finish.opts.body);
    assert.strictEqual(payload.challenge_id, "ch1");
    assert.strictEqual(payload.credential.id, "cid");
  });
}

testB64RoundTrip();
testRevivePublicKey();
testSerializeCredential();
testCreateClientSetsCsrfAndSerializes().then(() => {
  console.log("ok");
}).catch((err) => {
  console.error(err);
  process.exit(1);
});
