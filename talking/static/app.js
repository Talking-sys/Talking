(() => {
  const $ = (id) => document.getElementById(id);
  const LS = localStorage;
  const state = {
    running: false,
    pauseMs: +(LS.getItem("pause_ms") || 800),
    target: LS.getItem("target_lang") || "en",
    model: LS.getItem("whisper_model") || "large-v3",
    clientVad: LS.getItem("client_vad") !== "0",
    ws: null,
    token: "",
    audioCtx: null,
    source: null,
    node: null,
    stream: null,
    vad: null,
    items: new Map(),
  };

  function toast(text) {
    const el = $("toast");
    el.textContent = text;
    el.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { el.hidden = true; }, 2800);
  }

  function floatToPcm16(f32) {
    const out = new Int16Array(f32.length);
    for (let i = 0; i < f32.length; i++) {
      const s = Math.max(-1, Math.min(1, f32[i]));
      out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return out.buffer;
  }

  function sendJSON(obj) {
    if (state.ws && state.ws.readyState === 1) state.ws.send(JSON.stringify(obj));
  }

  function sendFrame(f32) {
    if (state.ws && state.ws.readyState === 1 && state.running) state.ws.send(floatToPcm16(f32));
  }

  function speak(text, lang) {
    if (!text || !("speechSynthesis" in window)) return;
    const u = new SpeechSynthesisUtterance(text);
    u.lang = lang || "en";
    u.rate = 1.05;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(u);
  }

  function upsertItem(ev) {
    const id = ev.segment_id || ev.id;
    if (!id) return;
    let row = state.items.get(id);
    if (!row) {
      row = document.createElement("article");
      row.className = "item";
      row.innerHTML = '<div class="meta"><span class="clock"></span><span class="langs"></span></div>' +
        '<div class="src"></div><div class="tgt"></div>';
      $("feed").prepend(row);
      state.items.set(id, row);
      $("empty").hidden = true;
    }
    if (ev.clock) row.querySelector(".clock").textContent = ev.clock;
    if (ev.src_lang || ev.tgt_lang) {
      row.querySelector(".langs").textContent = (ev.src_lang || "") + (ev.tgt_lang ? " → " + ev.tgt_lang : "");
    }
    if (ev.text) row.querySelector(".src").textContent = ev.text;
    if (ev.translation) {
      row.querySelector(".tgt").textContent = ev.translation;
      row.classList.remove("queued");
    }
    if (ev.type === "queued") {
      row.classList.add("queued");
      row.querySelector(".tgt").textContent = ev.text || "в очереди…";
    }
  }

  function onEvent(ev) {
    switch (ev.type) {
      case "status":
        $("net").textContent = ev.label || ev.net;
        break;
      case "config":
        if (ev.target_lang) { state.target = ev.target_lang; $("target").value = ev.target_lang; }
        if (ev.pause_ms) setPause(ev.pause_ms, false);
        if (ev.whisper_model) { state.model = ev.whisper_model; $("model").value = ev.whisper_model; }
        break;
      case "detected":
        $("detected").textContent = "язык: " + (ev.name || ev.lang);
        break;
      case "source":
      case "translation":
      case "queued":
        upsertItem(ev);
        break;
      case "command":
        toast(ev.toast || ("язык: " + ev.target_lang));
        if (ev.target_lang) $("target").value = ev.target_lang;
        break;
      case "speak_browser":
        speak(ev.text, ev.lang);
        break;
      case "notice":
        toast(ev.text);
        break;
      case "started":
        $("mic").setAttribute("aria-pressed", "true");
        $("mic").querySelector(".mic-label").textContent = "Стоп";
        break;
      case "stopped":
        $("mic").setAttribute("aria-pressed", "false");
        $("mic").querySelector(".mic-label").textContent = "Микрофон";
        break;
      default:
        break;
    }
  }

  async function connect() {
    const tok = await fetch("/api/ws-token").then((r) => r.json());
    state.token = tok.token;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws?t=${encodeURIComponent(state.token)}`);
    state.ws = ws;
    ws.onmessage = (e) => {
      try { onEvent(JSON.parse(e.data)); } catch (_) { /* ignore */ }
    };
    ws.onopen = () => {
      sendJSON({
        type: "config",
        target_lang: state.target,
        pause_ms: state.pauseMs,
        whisper_model: state.model,
        client_vad: state.clientVad,
        tts_mode: "browser",
      });
    };
    ws.onclose = () => { $("net").textContent = "нет связи"; setTimeout(connect, 1500); };
  }

  async function startMic() {
    state.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true }, video: false });
    state.audioCtx = new AudioContext({ sampleRate: 48000 });
    await state.audioCtx.audioWorklet.addModule("/static/pcm-worklet.js");
    state.source = state.audioCtx.createMediaStreamSource(state.stream);
    state.node = new AudioWorkletNode(state.audioCtx, "pcm-downsampler");
    state.vad = await TalkingVAD.createClientVAD({
      pauseMs: state.pauseMs,
      onSpeech: sendFrame,
      onStart: () => sendJSON({ type: "config", client_vad: true }),
      onEnd: (forced) => sendJSON({ type: "eou", forced: !!forced }),
    });
    state.vad.setPause(state.pauseMs);
    state.node.port.onmessage = (e) => {
      const f32 = e.data;
      if (state.clientVad && state.vad) state.vad.process(f32);
      else sendFrame(f32);
    };
    state.source.connect(state.node);
    const mute = state.audioCtx.createGain();
    mute.gain.value = 0;
    state.node.connect(mute);
    mute.connect(state.audioCtx.destination);
    sendJSON({ type: "start" });
    state.running = true;
  }

  function stopMic() {
    state.running = false;
    if (state.vad) { state.vad.reset(); sendJSON({ type: "eou" }); }
    sendJSON({ type: "stop" });
    if (state.node) { try { state.node.disconnect(); } catch (_) { /* ignore */ } }
    if (state.source) { try { state.source.disconnect(); } catch (_) { /* ignore */ } }
    if (state.stream) state.stream.getTracks().forEach((t) => t.stop());
    if (state.audioCtx) state.audioCtx.close();
    state.node = state.source = state.stream = state.audioCtx = null;
  }

  function setPause(v, send) {
    v = Math.max(300, Math.min(3000, +v || 800));
    state.pauseMs = v;
    $("pause").value = v;
    $("pauseVal").textContent = v;
    LS.setItem("pause_ms", v);
    if (state.vad) state.vad.setPause(v);
    if (send) sendJSON({ type: "config", pause_ms: v });
  }

  async function boot() {
    const [langs, settings] = await Promise.all([
      fetch("/api/languages").then((r) => r.json()),
      fetch("/api/settings").then((r) => r.json()),
    ]);
    const sel = $("target");
    langs.forEach((l) => {
      const o = document.createElement("option");
      o.value = l.code;
      o.textContent = l.label;
      sel.appendChild(o);
    });
    sel.value = LS.getItem("target_lang") || settings.target_lang || "en";
    $("model").value = LS.getItem("whisper_model") || settings.whisper_model || "large-v3";
    setPause(LS.getItem("pause_ms") || settings.pause_ms || 800, false);
    $("clientVad").checked = state.clientVad;
    await connect();
  }

  $("pause").addEventListener("input", (e) => setPause(e.target.value, true));
  $("target").addEventListener("change", (e) => {
    state.target = e.target.value;
    LS.setItem("target_lang", state.target);
    sendJSON({ type: "config", target_lang: state.target });
  });
  $("model").addEventListener("change", (e) => {
    state.model = e.target.value;
    LS.setItem("whisper_model", state.model);
    sendJSON({ type: "config", whisper_model: state.model });
    toast("Модель: " + state.model + (state.model === "large-v3" ? " — нужна память" : ""));
  });
  $("clientVad").addEventListener("change", (e) => {
    state.clientVad = e.target.checked;
    LS.setItem("client_vad", e.target.checked ? "1" : "0");
    sendJSON({ type: "config", client_vad: state.clientVad });
  });
  $("mic").addEventListener("click", async () => {
    try {
      if (state.running) stopMic();
      else await startMic();
    } catch (err) {
      toast("Микрофон: " + err.message + ". На телефоне нужен HTTPS.");
    }
  });

  boot().catch((e) => toast("Ошибка загрузки: " + e.message));
})();
