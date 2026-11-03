(() => {
  const $ = (id) => document.getElementById(id);
  const I18N = {
    ru: {
      title: "Голосовой перевод",
      live: "Живой перевод",
      record: "Запись",
      source: "Исходный",
      target: "Перевод на",
      auto: "Авто",
      tts: "Озвучивать финалы",
      chat: "Отправить запись в чат как голосовую заметку",
      hold: "Удерживайте",
      holdLive: "Удерживайте, чтобы переводить. Влево — отмена, вверх — замок.",
      holdRecord: "Удерживайте, чтобы записать. Отпустите — отправить. Влево — отмена.",
      emptyLive: "Удерживайте кнопку и говорите. Текст и перевод появятся здесь.",
      emptyRecord: "Удерживайте, чтобы записать фразу. Отпустите — расшифровка и перевод.",
      hint: "Удерживайте · влево отмена · вверх замок",
      hintCancel: "Отпустите, чтобы отменить",
      hintLock: "Без удержания. Стоп или Отправить.",
      hintLive: "Говорите — перевод появится сразу",
      recLive: "Идёт живой перевод",
      recRecord: "Идёт запись",
      locked: "Запись зафиксирована",
      preview: "Превью. Отправьте или отмените.",
      tooShort: "Слишком коротко",
      stop: "Стоп",
      send: "Отправить",
      trash: "Отмена",
      lock: "Замок",
      slide: "← Отмена",
      chatNav: "Чат",
      back: "Назад",
      net: "подключение…",
      offline: "нет связи",
      speakerA: "A",
      speakerB: "B",
      premium: "Живой перевод недоступен по лицензии.",
      sentChat: "Отправлено в чат",
    },
    en: {
      title: "Voice translation",
      live: "Live translation",
      record: "Record",
      source: "Source",
      target: "Translate to",
      auto: "Auto",
      tts: "Speak final segments",
      chat: "Send the recording to chat as a voice note",
      hold: "Hold",
      holdLive: "Hold to translate. Slide left to cancel, up to lock.",
      holdRecord: "Hold to record. Release to send. Slide left to cancel.",
      emptyLive: "Hold the button and speak. Transcript and translation appear here.",
      emptyRecord: "Hold to record a phrase. Release to transcribe and translate.",
      hint: "Hold · left cancel · up lock",
      hintCancel: "Release to cancel",
      hintLock: "Hands-free. Stop or Send.",
      hintLive: "Speak — translation appears live",
      recLive: "Live translation",
      recRecord: "Recording",
      locked: "Locked",
      preview: "Preview. Send or cancel.",
      tooShort: "Too short",
      stop: "Stop",
      send: "Send",
      trash: "Cancel",
      lock: "Lock",
      slide: "← Cancel",
      chatNav: "Chat",
      back: "Back",
      net: "connecting…",
      offline: "offline",
      speakerA: "A",
      speakerB: "B",
      premium: "Live translation is not included in this licence.",
      sentChat: "Sent to chat",
    },
  };

  const LS = localStorage;
  const MAX_MS = 60000;
  const state = {
    uiLang: (LS.getItem("ui_lang") || document.documentElement.lang || "ru").slice(0, 2) === "en" ? "en" : "ru",
    mode: "live",
    source: LS.getItem("vt_source") || "auto",
    target: LS.getItem("target_lang") || "en",
    partner: LS.getItem("partner_lang") || "ru",
    tts: LS.getItem("vt_tts") !== "0",
    toChat: LS.getItem("vt_chat") === "1",
    dialogId: "local",
    token: "",
    ws: null,
    fsm: new TalkingGestures.RecordGestureMachine(new TalkingGestures.GestureConfig({ enable_toggle: false })),
    ticking: false,
    recGen: 0,
    stream: null,
    recorder: null,
    chunks: [],
    audioCtx: null,
    node: null,
    sourceNode: null,
    analyser: null,
    vad: null,
    peaks: [],
    startedAt: 0,
    blob: null,
    mime: "",
    running: false,
    liveAllowed: true,
    langs: [],
  };

  function t(key) {
    return (I18N[state.uiLang] || I18N.ru)[key] || I18N.en[key] || key;
  }

  function toast(text) {
    const el = $("toast");
    el.textContent = text;
    el.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { el.hidden = true; }, 2800);
  }

  function applyI18n() {
    document.documentElement.lang = state.uiLang;
    $("hdrTitle").textContent = t("title");
    $("modeLive").textContent = t("live");
    $("modeRecord").textContent = t("record");
    $("lblSource").textContent = t("source");
    $("lblTarget").textContent = t("target");
    $("lblTts").textContent = t("tts");
    $("lblChat").textContent = t("chat");
    $("holdLabel").textContent = t("hold");
    $("navChat").textContent = t("chatNav");
    $("ftChat").textContent = t("chatNav");
    $("langSwitch").textContent = state.uiLang === "ru" ? "EN" : "RU";
    $("btnStop").textContent = t("stop");
    $("btnSend").textContent = t("send");
    $("btnTrash").textContent = t("trash");
    $("lockTarget").textContent = t("lock");
    $("slideCancel").textContent = t("slide");
    $("empty").textContent = state.mode === "live" ? t("emptyLive") : t("emptyRecord");
    $("recordBtn").setAttribute("aria-label", state.mode === "live" ? t("holdLive") : t("holdRecord"));
    const src = $("source");
    if (src && src.options.length) {
      const auto = src.querySelector('option[value="auto"]');
      if (auto) auto.textContent = t("auto");
    }
  }

  function nowMs() {
    return performance.now();
  }

  function fmtDur(ms) {
    const s = Math.max(0, Math.round(ms / 1000));
    return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
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
    if (!state.tts || !text || !("speechSynthesis" in window)) return;
    const u = new SpeechSynthesisUtterance(text);
    u.lang = lang || "en";
    u.rate = 1.05;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(u);
  }

  function setMode(mode) {
    if (mode === "live" && !state.liveAllowed) {
      toast(t("premium"));
      return;
    }
    state.mode = mode === "record" ? "record" : "live";
    $("modeLive").classList.toggle("is-on", state.mode === "live");
    $("modeRecord").classList.toggle("is-on", state.mode === "record");
    $("modeLive").setAttribute("aria-selected", state.mode === "live" ? "true" : "false");
    $("modeRecord").setAttribute("aria-selected", state.mode === "record" ? "true" : "false");
    $("chatRow").hidden = state.mode !== "record";
    $("empty").textContent = state.mode === "live" ? t("emptyLive") : t("emptyRecord");
    $("recordBtn").setAttribute("aria-label", state.mode === "live" ? t("holdLive") : t("holdRecord"));
  }

  function upsertCurrent(ev) {
    $("empty").hidden = true;
    $("current").hidden = false;
    $("current").classList.toggle("is-partial", !!ev.partial);
    $("current").classList.toggle("speaker-b", ev.speaker === "b");
    $("curSpeaker").textContent = ev.speaker === "b" ? t("speakerB") : t("speakerA");
    $("curPair").textContent = (ev.src_lang || "") + (ev.tgt_lang ? " → " + ev.tgt_lang : "");
    if (ev.text) $("curOrig").textContent = ev.text;
    if (ev.translation) $("curTgt").textContent = ev.translation;
  }

  function commitTurn(ev) {
    const art = document.createElement("article");
    art.className = "turn" + (ev.speaker === "b" ? " speaker-b" : "");
    art.innerHTML = '<div class="turn-meta"><span class="speaker"></span><span class="pair"></span></div>' +
      '<p class="orig"></p><p class="big"></p>';
    art.querySelector(".speaker").textContent = ev.speaker === "b" ? t("speakerB") : t("speakerA");
    art.querySelector(".pair").textContent = (ev.src_lang || "") + (ev.tgt_lang ? " → " + ev.tgt_lang : "");
    art.querySelector(".orig").textContent = ev.text || "";
    art.querySelector(".big").textContent = ev.translation || ev.text || "";
    $("turns").prepend(art);
    $("current").hidden = true;
    $("curOrig").textContent = "";
    $("curTgt").textContent = "";
    $("empty").hidden = true;
  }

  function onLiveEvent(ev) {
    switch (ev.type) {
      case "config":
        if (ev.target_lang) { state.target = ev.target_lang; $("target").value = ev.target_lang; }
        if (ev.source_lang) { state.source = ev.source_lang; $("source").value = ev.source_lang; }
        break;
      case "partial":
        upsertCurrent(ev);
        break;
      case "final":
        commitTurn(ev);
        break;
      case "speak_browser":
        speak(ev.text, ev.lang);
        break;
      case "notice":
      case "error":
        toast(ev.text || ev.reason || t("premium"));
        break;
      case "limit":
        toast((ev.reason || "limit") + " (" + (ev.max_s || "") + "s)");
        stopLive();
        break;
      case "backpressure":
        break;
      case "started":
        $("net").textContent = t("recLive");
        break;
      case "stopped":
        $("net").textContent = t("title");
        break;
      case "swap":
        if (ev.target_lang) $("target").value = ev.target_lang;
        if (ev.source_lang) $("source").value = ev.source_lang;
        state.target = ev.target_lang || state.target;
        state.source = ev.source_lang || state.source;
        state.partner = ev.partner_lang || state.partner;
        break;
      default:
        break;
    }
  }

  async function connectLive() {
    const tok = await fetch("/api/ws-token").then((r) => r.json());
    state.token = tok.token;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/translate/live?t=${encodeURIComponent(state.token)}`);
    state.ws = ws;
    ws.onmessage = (e) => {
      try { onLiveEvent(JSON.parse(e.data)); } catch (_) { /* ignore */ }
    };
    ws.onopen = () => {
      $("net").textContent = t("title");
      sendJSON({
        type: "config",
        source_lang: state.source,
        target_lang: state.target,
        partner_lang: state.partner,
        tts: state.tts,
        client_vad: true,
      });
    };
    ws.onclose = (ev) => {
      $("net").textContent = t("offline");
      if (ev.code === 1008) toast(t("premium"));
      if (state.liveAllowed) setTimeout(connectLive, 1500);
    };
  }

  function pushConfig() {
    sendJSON({
      type: "config",
      source_lang: state.source,
      target_lang: state.target,
      partner_lang: state.partner,
      tts: state.tts,
      client_vad: true,
    });
  }

  async function startLiveMic() {
    state.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true }, video: false,
    });
    state.audioCtx = new AudioContext({ sampleRate: 48000 });
    await state.audioCtx.audioWorklet.addModule("/static/pcm-worklet.js");
    state.sourceNode = state.audioCtx.createMediaStreamSource(state.stream);
    state.node = new AudioWorkletNode(state.audioCtx, "pcm-downsampler");
    state.vad = await TalkingVAD.createClientVAD({
      pauseMs: 800,
      onSpeech: sendFrame,
      onStart: () => {},
      onEnd: () => sendJSON({ type: "eou" }),
    });
    state.node.port.onmessage = (e) => {
      if (state.vad) state.vad.process(e.data);
      else sendFrame(e.data);
    };
    state.sourceNode.connect(state.node);
    const mute = state.audioCtx.createGain();
    mute.gain.value = 0;
    state.node.connect(mute);
    mute.connect(state.audioCtx.destination);
    try {
      state.analyser = state.audioCtx.createAnalyser();
      state.analyser.fftSize = 256;
      state.sourceNode.connect(state.analyser);
    } catch (_) { /* optional */ }
    sendJSON({ type: "start" });
    state.running = true;
  }

  function stopLive() {
    state.running = false;
    if (state.vad) { try { state.vad.reset(); } catch (_) { /* ignore */ } sendJSON({ type: "eou" }); }
    sendJSON({ type: "stop" });
    if (state.node) { try { state.node.disconnect(); } catch (_) { /* ignore */ } }
    if (state.sourceNode) { try { state.sourceNode.disconnect(); } catch (_) { /* ignore */ } }
    if (state.stream) state.stream.getTracks().forEach((tr) => tr.stop());
    if (state.audioCtx) { try { state.audioCtx.close(); } catch (_) { /* ignore */ } }
    state.node = state.sourceNode = state.stream = state.audioCtx = state.analyser = state.vad = null;
  }

  function pickMime(kinds) {
    if (!window.MediaRecorder) return "";
    for (const k of kinds) {
      if (MediaRecorder.isTypeSupported(k)) return k;
    }
    return "";
  }

  function sampleAnalyser() {
    if (!state.analyser) return;
    const buf = new Uint8Array(state.analyser.fftSize);
    state.analyser.getByteTimeDomainData(buf);
    let peak = 0;
    for (let i = 0; i < buf.length; i++) peak = Math.max(peak, Math.abs(buf[i] - 128));
    state.peaks.push(Math.min(255, peak * 2));
    if (state.peaks.length > 64) state.peaks.shift();
    drawLiveWave();
  }

  function drawLiveWave() {
    const canvas = $("waveLive");
    const ctx = canvas.getContext("2d");
    const w = canvas.width, h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#2ad4c6";
    const bar = Math.max(2, w / 64);
    for (let i = 0; i < 64; i++) {
      const p = state.peaks[state.peaks.length - 64 + i] || 4;
      const bh = Math.max(4, (p / 255) * (h - 8));
      ctx.globalAlpha = i / 64;
      ctx.fillRect(i * bar, (h - bh) / 2, bar - 1, bh);
    }
    ctx.globalAlpha = 1;
  }

  async function beginRecording() {
    const g = ++state.recGen;
    state.chunks = [];
    state.peaks = [];
    state.blob = null;
    $("recordOverlay").hidden = false;
    $("lockBar").hidden = true;
    $("recordBtn").classList.add("is-recording");
    $("recordBtn").setAttribute("aria-pressed", "true");
    try {
      if (state.mode === "live") {
        await startLiveMic();
        setStatus(t("recLive"));
      } else {
        const raw = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true }, video: false,
        });
        if (g !== state.recGen || state.fsm.phase === "idle") {
          raw.getTracks().forEach((tr) => tr.stop());
          return;
        }
        state.stream = raw;
        const mime = pickMime(["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"]);
        state.mime = mime;
        const rec = mime ? new MediaRecorder(raw, { mimeType: mime }) : new MediaRecorder(raw);
        state.recorder = rec;
        rec.ondataavailable = (e) => { if (e.data && e.data.size) state.chunks.push(e.data); };
        rec.start(200);
        try {
          state.audioCtx = new AudioContext();
          const src = state.audioCtx.createMediaStreamSource(raw);
          state.analyser = state.audioCtx.createAnalyser();
          state.analyser.fftSize = 256;
          src.connect(state.analyser);
        } catch (_) { /* optional */ }
        setStatus(t("recRecord"));
      }
      state.startedAt = nowMs();
      const loop = () => {
        sampleAnalyser();
        $("timer").textContent = fmtDur(nowMs() - state.startedAt);
        if (state.fsm.phase !== "idle") requestAnimationFrame(loop);
      };
      requestAnimationFrame(loop);
    } catch (err) {
      toast("Mic: " + err.message);
      applySnap(state.fsm.cancel());
    }
  }

  function stopRecorder() {
    return new Promise((resolve) => {
      const rec = state.recorder;
      if (!rec || rec.state === "inactive") {
        resolve(state.blob);
        return;
      }
      rec.onstop = () => {
        const type = rec.mimeType || state.mime || "application/octet-stream";
        state.blob = new Blob(state.chunks, { type });
        resolve(state.blob);
      };
      try { rec.stop(); } catch (_) { resolve(state.blob); }
    });
  }

  function stopTracks() {
    if (state.mode === "live") stopLive();
    else {
      if (state.stream) state.stream.getTracks().forEach((tr) => tr.stop());
      state.stream = null;
      if (state.audioCtx) { try { state.audioCtx.close(); } catch (_) { /* ignore */ } }
      state.audioCtx = null;
      state.analyser = null;
    }
  }

  async function finish(send) {
    state.recGen += 1;
    const elapsed = Math.max(0, nowMs() - (state.startedAt || nowMs()));
    $("recordOverlay").hidden = true;
    $("recordOverlay").classList.remove("is-cancel", "is-lock-hint", "is-locked");
    $("recordBtn").classList.remove("is-recording");
    $("recordBtn").setAttribute("aria-pressed", "false");
    $("lockBar").hidden = true;
    setStatus("");
    const blob = state.mode === "record" ? await stopRecorder() : null;
    stopTracks();
    state.recorder = null;
    if (!send) return;
    if (state.mode === "live") return;
    if (!blob || blob.size < 64 || elapsed < 200) {
      toast(t("tooShort"));
      return;
    }
    const fd = new FormData();
    const ext = (blob.type || "").includes("mp4") ? "mp4" : "webm";
    fd.append("file", blob, "note." + ext);
    fd.append("source_lang", state.source);
    fd.append("target_lang", state.target);
    fd.append("partner_lang", state.partner);
    fd.append("tts", state.tts ? "1" : "0");
    fd.append("duration_ms", String(Math.min(MAX_MS, Math.max(200, Math.round(elapsed)))));
    fd.append("waveform", JSON.stringify(state.peaks));
    if (state.toChat) fd.append("dialog_id", state.dialogId);
    try {
      const r = await fetch("/api/translate/record", { method: "POST", body: fd });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) {
        toast(body.detail || ("Error " + r.status));
        return;
      }
      commitTurn({
        speaker: body.speaker,
        src_lang: body.src_lang,
        tgt_lang: body.tgt_lang,
        text: body.text,
        translation: body.translation,
      });
      if (state.tts && body.speak) speak(body.speak.text, body.speak.lang);
      else if (state.tts && body.translation) speak(body.translation, body.tgt_lang);
      if (body.message) toast(t("sentChat"));
    } catch (e) {
      toast(e.message);
    }
  }

  function setStatus(text) {
    $("recordStatus").textContent = text;
  }

  function applySnap(snap) {
    if (!snap) return;
    const ov = $("recordOverlay");
    ov.classList.toggle("is-cancel", snap.hint === "cancel");
    ov.classList.toggle("is-lock-hint", snap.hint === "lock" || snap.phase === "locked");
    ov.classList.toggle("is-locked", snap.phase === "locked" || snap.phase === "preview");
    $("lockBar").hidden = !(snap.phase === "locked" || snap.phase === "preview");
    $("hint").textContent = snap.hint === "cancel"
      ? t("hintCancel")
      : snap.phase === "locked"
        ? t("hintLock")
        : (state.mode === "live" ? t("hintLive") : t("hint"));
    if (snap.phase === "recording" || snap.phase === "locked" || snap.phase === "preview") {
      ov.hidden = false;
    }
    if (snap.action === "start") beginRecording();
    if (snap.action === "lock") setStatus(t("locked"));
    if (snap.action === "preview") {
      if (state.mode === "record") stopRecorder();
      else stopLive();
      setStatus(t("preview"));
    }
    if (snap.action === "send") finish(true);
    if (snap.action === "cancel") finish(false);
  }

  function tickLoop() {
    if (!state.ticking) return;
    applySnap(state.fsm.tick(nowMs()));
    if (state.fsm.phase === "idle") {
      state.ticking = false;
      return;
    }
    requestAnimationFrame(tickLoop);
  }

  function startTick() {
    if (state.ticking) return;
    state.ticking = true;
    requestAnimationFrame(tickLoop);
  }

  function bindRecord() {
    const btn = $("recordBtn");
    btn.addEventListener("pointerdown", (e) => {
      if (e.button != null && e.button !== 0) return;
      e.preventDefault();
      try { btn.setPointerCapture(e.pointerId); } catch (_) { /* ignore */ }
      applySnap(state.fsm.pointerDown(e.clientX, e.clientY, nowMs()));
      startTick();
    });
    btn.addEventListener("pointermove", (e) => {
      if (state.fsm.phase === "idle") return;
      applySnap(state.fsm.pointerMove(e.clientX, e.clientY, nowMs()));
    });
    btn.addEventListener("pointerup", (e) => {
      applySnap(state.fsm.pointerUp(e.clientX, e.clientY, nowMs()));
    });
    btn.addEventListener("pointercancel", () => applySnap(state.fsm.cancel()));
    btn.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        applySnap(state.fsm.cancel());
      }
      if (e.key === "ArrowLeft" && state.fsm.phase !== "idle") {
        e.preventDefault();
        applySnap(state.fsm.pointerMove(-200, 0, nowMs()));
      }
      if (e.key === "ArrowUp" && state.fsm.phase !== "idle") {
        e.preventDefault();
        applySnap(state.fsm.pointerMove(0, -200, nowMs()));
      }
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        if (e.repeat) return;
        applySnap(state.fsm.pointerDown(0, 0, nowMs()));
        startTick();
      }
    });
    btn.addEventListener("keyup", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        applySnap(state.fsm.pointerUp(0, 0, nowMs()));
      }
    });
    $("btnSend").addEventListener("click", () => applySnap(state.fsm.lockSend()));
    $("btnStop").addEventListener("click", () => applySnap(state.fsm.lockStop()));
    $("btnTrash").addEventListener("click", () => applySnap(state.fsm.cancel()));
  }

  function fillLangs(langs) {
    state.langs = langs;
    const src = $("source");
    const tgt = $("target");
    src.innerHTML = "";
    tgt.innerHTML = "";
    const auto = document.createElement("option");
    auto.value = "auto";
    auto.textContent = t("auto");
    src.appendChild(auto);
    langs.forEach((l) => {
      const a = document.createElement("option");
      a.value = l.code;
      a.textContent = l.label;
      src.appendChild(a);
      const b = a.cloneNode(true);
      tgt.appendChild(b);
    });
    src.value = state.source;
    tgt.value = state.target;
    if (!tgt.value) tgt.value = "en";
  }

  async function boot() {
    applyI18n();
    const [langs, settings, meta, dialogs] = await Promise.all([
      fetch("/api/languages").then((r) => r.json()),
      fetch("/api/settings").then((r) => r.json()),
      fetch("/api/translate").then((r) => r.json()),
      fetch("/api/chat/dialogs").then((r) => r.json()).catch(() => []),
    ]);
    state.target = LS.getItem("target_lang") || settings.target_lang || "en";
    state.partner = LS.getItem("partner_lang") || settings.partner_lang || "ru";
    fillLangs(langs);
    $("tts").checked = state.tts;
    $("toChat").checked = state.toChat;
    if (dialogs && dialogs[0]) state.dialogId = dialogs[0].id;
    state.liveAllowed = meta.live_allowed !== false;
    $("net").textContent = meta.streaming === "vad_windows" ? t("title") : (meta.live_reason || t("title"));
    if (!state.liveAllowed) {
      $("modeLive").disabled = true;
      setMode("record");
      toast(t("premium"));
    }
    const q = new URLSearchParams(location.search);
    if (q.get("mode") === "record") setMode("record");
    if (q.get("dialog")) state.dialogId = q.get("dialog");
    $("chatRow").hidden = state.mode !== "record";
    bindRecord();
    $("modeLive").addEventListener("click", () => setMode("live"));
    $("modeRecord").addEventListener("click", () => setMode("record"));
    $("source").addEventListener("change", () => {
      state.source = $("source").value;
      LS.setItem("vt_source", state.source);
      pushConfig();
    });
    $("target").addEventListener("change", () => {
      state.target = $("target").value;
      LS.setItem("target_lang", state.target);
      pushConfig();
    });
    $("swap").addEventListener("click", () => {
      if (state.mode === "live") sendJSON({ type: "swap" });
      else {
        if (state.source === "auto") {
          const tmp = state.target;
          state.target = state.partner;
          state.partner = tmp;
        } else {
          const tmp = state.source;
          state.source = state.target;
          state.target = tmp;
        }
        $("source").value = state.source;
        $("target").value = state.target;
        LS.setItem("vt_source", state.source);
        LS.setItem("target_lang", state.target);
        LS.setItem("partner_lang", state.partner);
      }
    });
    $("tts").addEventListener("change", () => {
      state.tts = $("tts").checked;
      LS.setItem("vt_tts", state.tts ? "1" : "0");
      pushConfig();
    });
    $("toChat").addEventListener("change", () => {
      state.toChat = $("toChat").checked;
      LS.setItem("vt_chat", state.toChat ? "1" : "0");
    });
    $("langSwitch").addEventListener("click", () => {
      state.uiLang = state.uiLang === "ru" ? "en" : "ru";
      LS.setItem("ui_lang", state.uiLang);
      applyI18n();
      fillLangs(state.langs);
    });
    if (state.liveAllowed) await connectLive();
  }

  boot().catch((e) => toast(e.message));
})();
