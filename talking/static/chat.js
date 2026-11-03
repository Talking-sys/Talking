(() => {
  const $ = (id) => document.getElementById(id);
  const RING_C = 2 * Math.PI * 54;
  const MAX_MS = 60000;
  const SIZE = 480;

  const I18N = {
    ru: {
      targetLang: "Язык перевода заметок",
      empty: "Короткое нажатие справа переключает голос и кружочек. Удерживайте, чтобы записать; влево — отмена; вверх — замок.",
      transcribe: "Расшифровать",
      transcribeShort: "→А",
      transcribing: "Расшифровка…",
      copy: "Копировать",
      copied: "Скопировано",
      showOrig: "Показать оригинал",
      hideOrig: "Скрыть оригинал",
      premiumHint: "Расшифровка голосовых — функция Talking Premium.",
      teaser: "Полный текст доступен в Premium.",
      failed: "Не удалось расшифровать",
      voice: "голос",
      circle: "кружочек",
      text: "текст",
      ariaVoice: "Голосовая заметка",
      ariaCircle: "Кружочек",
      ariaTranscribe: "Расшифровать голосовую заметку",
      ariaLocked: "Расшифровка недоступна без Premium",
    },
    en: {
      targetLang: "Note translation language",
      empty: "Short tap on the right toggles voice and round video. Hold to record; slide left to cancel; slide up to lock.",
      transcribe: "Transcribe",
      transcribeShort: "→A",
      transcribing: "Transcribing…",
      copy: "Copy",
      copied: "Copied",
      showOrig: "Show original",
      hideOrig: "Hide original",
      premiumHint: "Voice transcription is a Talking Premium feature.",
      teaser: "Full text is available with Premium.",
      failed: "Could not transcribe",
      voice: "voice",
      circle: "round video",
      text: "text",
      ariaVoice: "Voice note",
      ariaCircle: "Round video note",
      ariaTranscribe: "Transcribe voice note",
      ariaLocked: "Transcription requires Premium",
    },
  };

  const state = {
    dialogId: "local",
    target: localStorage.getItem("target_lang") || "en",
    uiLang: localStorage.getItem("ui_lang") || "ru",
    token: "",
    ws: null,
    fsm: new TalkingGestures.RecordGestureMachine(),
    ticking: false,
    recGen: 0,
    stream: null,
    recorder: null,
    chunks: [],
    audioCtx: null,
    analyser: null,
    peaks: [],
    startedAt: 0,
    blob: null,
    mime: "",
    cropTimer: 0,
    drawTimer: 0,
    autoplay: true,
    entitled: false,
    freeMode: "lock",
    pollers: {},
  };

  function t(key) {
    const pack = I18N[state.uiLang] || I18N.ru;
    return pack[key] || I18N.ru[key] || key;
  }

  function applyUiLang() {
    const lang = state.uiLang === "en" ? "en" : "ru";
    document.documentElement.lang = lang;
    const ru = $("uiLangRu");
    const en = $("uiLangEn");
    if (ru) ru.setAttribute("aria-pressed", lang === "ru" ? "true" : "false");
    if (en) en.setAttribute("aria-pressed", lang === "en" ? "true" : "false");
    document.querySelectorAll("[data-i18n]").forEach((el) => {
      const key = el.getAttribute("data-i18n");
      if (key && I18N.ru[key]) el.textContent = t(key);
    });
    const sel = $("target");
    if (sel) sel.setAttribute("aria-label", t("targetLang"));
  }

  function toast(text) {
    const el = $("toast");
    el.textContent = text;
    el.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { el.hidden = true; }, 2800);
  }

  function fmtDur(ms) {
    const s = Math.max(0, Math.round(ms / 1000));
    return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
  }

  function nowMs() {
    return performance.now();
  }

  function pickMime(kinds) {
    if (!window.MediaRecorder) return "";
    for (const k of kinds) {
      if (MediaRecorder.isTypeSupported(k)) return k;
    }
    return "";
  }

  function setModeUI(mode) {
    const btn = $("recordBtn");
    btn.dataset.mode = mode;
    btn.setAttribute(
      "aria-label",
      mode === "video"
        ? "Кружочек. Короткое нажатие — голосовая заметка, удержание — запись. Влево отмена, вверх замок."
        : "Голосовая заметка. Короткое нажатие — кружочек, удержание — запись. Влево отмена, вверх замок."
    );
  }

  function setRing(progress) {
    const fg = $("ringFg");
    if (!fg) return;
    fg.style.strokeDasharray = String(RING_C);
    fg.style.strokeDashoffset = String(RING_C * (1 - Math.max(0, Math.min(1, progress))));
  }

  function setStatus(text) {
    $("recordStatus").textContent = text;
  }

  function drawLiveWave() {
    const canvas = $("waveLive");
    const ctx = canvas.getContext("2d");
    const w = canvas.width, h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#2ad4c6";
    const peaks = state.peaks;
    const bar = Math.max(2, w / 64);
    for (let i = 0; i < 64; i++) {
      const p = peaks[peaks.length - 64 + i] || 4;
      const bh = Math.max(4, (p / 255) * (h - 8));
      ctx.globalAlpha = i / 64;
      ctx.fillRect(i * bar, (h - bh) / 2, bar - 1, bh);
    }
    ctx.globalAlpha = 1;
  }

  function sampleAnalyser() {
    if (!state.analyser) return;
    const buf = new Uint8Array(state.analyser.fftSize);
    state.analyser.getByteTimeDomainData(buf);
    let peak = 0;
    for (let i = 0; i < buf.length; i++) peak = Math.max(peak, Math.abs(buf[i] - 128));
    state.peaks.push(Math.min(255, peak * 2));
    if (state.peaks.length > 64) state.peaks.shift();
    if (!$("waveLive").hidden) drawLiveWave();
  }

  function stopTracks() {
    if (state.cropTimer) { cancelAnimationFrame(state.cropTimer); state.cropTimer = 0; }
    if (state.drawTimer) { cancelAnimationFrame(state.drawTimer); state.drawTimer = 0; }
    if (state.stream) state.stream.getTracks().forEach((t) => t.stop());
    state.stream = null;
    if (state.audioCtx) { try { state.audioCtx.close(); } catch (_) { /* ignore */ } }
    state.audioCtx = null;
    state.analyser = null;
    $("preview").srcObject = null;
  }

  function squareStream(raw) {
    const videoTrack = raw.getVideoTracks()[0];
    if (!videoTrack || !HTMLCanvasElement.prototype.captureStream) return raw;
    const canvas = document.createElement("canvas");
    canvas.width = SIZE;
    canvas.height = SIZE;
    const ctx = canvas.getContext("2d");
    const v = document.createElement("video");
    v.srcObject = raw;
    v.muted = true;
    v.playsInline = true;
    v.play().catch(() => {});
    const draw = () => {
      const vw = v.videoWidth || SIZE, vh = v.videoHeight || SIZE;
      const side = Math.min(vw, vh) || SIZE;
      const sx = (vw - side) / 2, sy = (vh - side) / 2;
      try { ctx.drawImage(v, sx, sy, side, side, 0, 0, SIZE, SIZE); } catch (_) { /* ignore */ }
      if (state.stream) state.cropTimer = requestAnimationFrame(draw);
    };
    state.cropTimer = requestAnimationFrame(draw);
    const out = canvas.captureStream(24);
    raw.getAudioTracks().forEach((t) => out.addTrack(t));
    out._raw = raw;
    return out;
  }

  async function beginRecording() {
    const g = ++state.recGen;
    const mode = state.fsm.mode;
    state.chunks = [];
    state.peaks = [];
    state.blob = null;
    try {
      const constraints = mode === "video"
        ? { audio: true, video: { facingMode: "user", width: { ideal: 480 }, height: { ideal: 480 } } }
        : { audio: { echoCancellation: true, noiseSuppression: true }, video: false };
      let raw = await navigator.mediaDevices.getUserMedia(constraints);
      if (g !== state.recGen || state.fsm.phase === "idle") {
        raw.getTracks().forEach((t) => t.stop());
        return;
      }
      let recStream = raw;
      if (mode === "video") recStream = squareStream(raw);
      state.stream = recStream;
      const preview = $("preview");
      $("recordOverlay").hidden = false;
      $("circleWrap").hidden = mode !== "video";
      $("waveLive").hidden = mode === "video";
      $("lockBar").hidden = true;
      $("recordBtn").classList.add("is-recording");
      if (mode === "video") {
        preview.srcObject = recStream._raw || recStream;
        preview.play().catch(() => {});
      }
      const mime = mode === "video"
        ? pickMime(["video/webm;codecs=vp9,opus", "video/webm;codecs=vp8,opus", "video/webm", "video/mp4"])
        : pickMime(["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"]);
      state.mime = mime;
      const rec = mime ? new MediaRecorder(recStream, { mimeType: mime }) : new MediaRecorder(recStream);
      state.recorder = rec;
      rec.ondataavailable = (e) => { if (e.data && e.data.size) state.chunks.push(e.data); };
      rec.start(200);
      state.startedAt = nowMs();
      try {
        state.audioCtx = new AudioContext();
        const src = state.audioCtx.createMediaStreamSource(raw);
        state.analyser = state.audioCtx.createAnalyser();
        state.analyser.fftSize = 256;
        src.connect(state.analyser);
      } catch (_) { /* analyser optional */ }
      const loop = () => {
        sampleAnalyser();
        const elapsed = nowMs() - state.startedAt;
        $("timer").textContent = fmtDur(elapsed);
        setRing(elapsed / MAX_MS);
        if (state.fsm.phase !== "idle") state.drawTimer = requestAnimationFrame(loop);
      };
      state.drawTimer = requestAnimationFrame(loop);
      setStatus(mode === "video" ? "Идёт запись кружочка" : "Идёт запись голосовой заметки");
    } catch (err) {
      toast("Запись: " + err.message + ". На телефоне нужен HTTPS.");
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

  async function finish(send) {
    state.recGen += 1;
    const mode = state.fsm.mode;
    const elapsed = Math.max(0, nowMs() - (state.startedAt || nowMs()));
    $("recordOverlay").hidden = true;
    $("recordOverlay").classList.remove("is-cancel", "is-lock-hint", "is-locked");
    $("recordBtn").classList.remove("is-recording");
    $("lockBar").hidden = true;
    setRing(0);
    setStatus("");
    const blob = await stopRecorder();
    stopTracks();
    state.recorder = null;
    if (!send) return;
    if (!blob || blob.size < 64 || elapsed < 200) {
      toast("Слишком коротко");
      return;
    }
    const fd = new FormData();
    const ext = (blob.type || "").includes("mp4") ? "mp4" : "webm";
    fd.append("file", blob, mode === "video" ? "note." + ext : "note." + ext);
    fd.append("kind", mode === "video" ? "video_note" : "voice_note");
    fd.append("duration_ms", String(Math.min(MAX_MS, Math.max(200, Math.round(elapsed)))));
    fd.append("waveform", JSON.stringify(state.peaks));
    fd.append("target_lang", state.target);
    try {
      const r = await fetch("/api/chat/dialogs/" + encodeURIComponent(state.dialogId) + "/notes", {
        method: "POST", body: fd,
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) {
        toast(body.detail || ("Ошибка " + r.status));
        return;
      }
      upsertMessage(body);
    } catch (e) {
      toast("Сеть: " + e.message);
    }
  }

  function applySnap(snap) {
    if (!snap) return;
    setModeUI(snap.mode);
    const ov = $("recordOverlay");
    ov.classList.toggle("is-cancel", snap.hint === "cancel");
    ov.classList.toggle("is-lock-hint", snap.hint === "lock" || snap.phase === "locked");
    ov.classList.toggle("is-locked", snap.phase === "locked" || snap.phase === "preview");
    $("lockBar").hidden = !(snap.phase === "locked" || snap.phase === "preview");
    $("hint").textContent = snap.hint === "cancel"
      ? "Отпустите, чтобы отменить"
      : snap.phase === "locked"
        ? "Запись без удержания. Стоп или Отправить."
        : "Удерживайте · влево отмена · вверх замок";
    if (snap.phase === "recording" || snap.phase === "locked" || snap.phase === "preview") {
      ov.hidden = false;
    }
    if (snap.action === "start") beginRecording();
    if (snap.action === "toggle") {
      toast(snap.mode === "video" ? "Режим: кружочек" : "Режим: голос");
    }
    if (snap.action === "lock") setStatus("Запись зафиксирована");
    if (snap.action === "preview") {
      stopRecorder();
      setStatus("Превью. Отправьте или отмените.");
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
    btn.addEventListener("pointercancel", () => {
      applySnap(state.fsm.cancel());
    });
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

  function drawWaveform(canvas, peaks, progress) {
    const ctx = canvas.getContext("2d");
    const w = canvas.width, h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    const n = Math.max(peaks.length, 8);
    const bar = w / n;
    for (let i = 0; i < n; i++) {
      const p = peaks[i] || 8;
      const bh = Math.max(4, (p / 255) * (h - 6));
      ctx.fillStyle = i / n <= progress ? "#2ad4c6" : "#2a3654";
      ctx.fillRect(i * bar, (h - bh) / 2, Math.max(1, bar - 1), bh);
    }
  }

  const io = new IntersectionObserver((entries) => {
    if (!state.autoplay) return;
    for (const e of entries) {
      const v = e.target;
      if (!(v instanceof HTMLVideoElement)) continue;
      if (e.isIntersecting && e.intersectionRatio >= 0.45) {
        v.muted = true;
        v.play().catch(() => {});
      } else {
        v.pause();
      }
    }
  }, { threshold: [0, 0.45, 1] });

  function isNote(msg) {
    return msg && (msg.type === "voice_note" || msg.type === "video_note");
  }

  function isDone(status) {
    return status === "done" || status === "ok";
  }

  function isPending(status) {
    return status === "pending";
  }

  function isFailed(status) {
    return status === "failed" || status === "error" || status === "skipped";
  }

  function escapeAttr(s) {
    return String(s || "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  function bindTranscribe(row, msg) {
    const btn = row.querySelector(".transcribe-btn");
    if (!btn) return;
    btn.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      if (!state.entitled) {
        toast(t("premiumHint"));
        return;
      }
      requestTranscribe(msg.id);
    });
    const copy = row.querySelector(".copy-transcript");
    if (copy) {
      copy.addEventListener("click", async () => {
        const text = msg.translation || msg.transcript || "";
        try {
          await navigator.clipboard.writeText(text);
          copy.textContent = t("copied");
          setTimeout(() => { copy.textContent = t("copy"); }, 1600);
        } catch (_) {
          toast(t("copy"));
        }
      });
    }
    const show = row.querySelector(".show-orig");
    if (show) {
      show.addEventListener("click", () => {
        const p = row.querySelector(".orig-wrap");
        const on = p.hidden;
        p.hidden = !on;
        show.textContent = on ? t("hideOrig") : t("showOrig");
      });
    }
  }

  async function requestTranscribe(id) {
    try {
      const r = await fetch("/api/chat/messages/" + encodeURIComponent(id) + "/transcribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_lang: state.target }),
      });
      const body = await r.json().catch(() => ({}));
      if (r.status === 403) {
        toast((body.detail && body.detail.hint && body.detail.hint[state.uiLang]) || t("premiumHint"));
        state.entitled = false;
        return;
      }
      if (!r.ok) {
        toast(typeof body.detail === "string" ? body.detail : (t("failed") + " " + r.status));
        return;
      }
      if (body.message) upsertMessage(body.message);
      else if (body.status) {
        const row = document.getElementById("m-" + id);
        if (row && row._msg) upsertMessage(Object.assign({}, row._msg, {
          transcript_status: body.status,
          transcript: body.transcript,
          translation: body.translation,
          src_lang: body.language,
          transcript_reason: body.reason,
        }));
      }
      if (body.status === "pending") pollTranscript(id);
    } catch (e) {
      toast("Сеть: " + e.message);
    }
  }

  function pollTranscript(id) {
    if (state.pollers[id]) return;
    let n = 0;
    const tick = async () => {
      n += 1;
      try {
        const r = await fetch("/api/chat/messages/" + encodeURIComponent(id) + "/transcript");
        const body = await r.json().catch(() => ({}));
        if (body.message) upsertMessage(body.message);
        if (body.status && body.status !== "pending") {
          clearInterval(state.pollers[id]);
          delete state.pollers[id];
          return;
        }
      } catch (_) { /* ignore */ }
      if (n > 80) {
        clearInterval(state.pollers[id]);
        delete state.pollers[id];
      }
    };
    state.pollers[id] = setInterval(tick, 400);
    tick();
  }

  function noteToolsHtml(msg) {
    if (!isNote(msg)) return "";
    const status = msg.transcript_status;
    const done = isDone(status) && msg.transcript;
    const pending = isPending(status);
    const failed = isFailed(status);
    const hideBtn = !state.entitled && state.freeMode === "hidden";
    let tools = "";
    if (!hideBtn && !done) {
      const locked = !state.entitled;
      const cls = "transcribe-btn" + (locked ? " is-locked" : "") + (pending ? " is-loading" : "");
      const label = pending ? t("transcribing") : t("transcribe");
      const aria = locked ? t("ariaLocked") : t("ariaTranscribe");
      tools += '<div class="note-tools"><button type="button" class="' + cls + '" aria-label="' +
        escapeAttr(aria) + '" aria-busy="' + (pending ? "true" : "false") + '"' +
        (locked ? ' aria-disabled="true"' : "") + ">" +
        '<span class="glyph" aria-hidden="true">' + t("transcribeShort") + "</span>" +
        '<span class="label">' + escapeAttr(label) + "</span></button></div>";
    }
    if (!state.entitled && state.freeMode === "teaser" && !done) {
      tools += '<p class="upsell-hint">' + escapeAttr(t("teaser")) + "</p>";
    }
    if (pending) {
      tools += '<p class="transcript-status" aria-live="polite">' + escapeAttr(t("transcribing")) + "</p>";
    }
    if (failed && msg.transcript_reason) {
      tools += '<p class="transcript-status">' + escapeAttr(t("failed") + " — " + msg.transcript_reason) + "</p>";
    }
    const hasTr = !!(msg.translation && msg.transcript && msg.translation !== msg.transcript);
    if (done) {
      tools += '<div class="transcript-panel">' +
        (msg.translation ? '<p class="translation"></p>' : "") +
        (hasTr ? '<p class="transcript orig-wrap" hidden></p>' : '<p class="transcript-body"></p>') +
        (!hasTr && !msg.translation ? "" : "") +
        '<div class="transcript-actions">' +
        '<button type="button" class="copy-transcript">' + escapeAttr(t("copy")) + "</button>" +
        (hasTr ? '<button type="button" class="show-orig">' + escapeAttr(t("showOrig")) + "</button>" : "") +
        "</div></div>";
    }
    return tools;
  }

  function upsertMessage(msg) {
    if (!msg || !msg.id) return;
    $("empty").hidden = true;
    let row = document.getElementById("m-" + msg.id);
    if (!row) {
      row = document.createElement("article");
      row.id = "m-" + msg.id;
      row.className = "msg";
      $("feed").appendChild(row);
    }
    row._msg = msg;
    const clock = new Date((msg.created_at || 0) * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const dur = fmtDur(msg.duration_ms || 0);
    let media = "";
    if (msg.type === "video_note") {
      media = '<button type="button" class="circle-note" aria-label="' + escapeAttr(t("ariaCircle") + ", " + dur) + '">' +
        '<video playsinline loop preload="metadata" src="' + escapeAttr(msg.play_url || "") + '"></video>' +
        '<svg class="ring" viewBox="0 0 120 120" aria-hidden="true"><circle class="ring-bg" cx="60" cy="60" r="54"></circle>' +
        '<circle class="ring-fg" cx="60" cy="60" r="54"></circle></svg>' +
        '<span class="play-glyph">▶</span><span class="dur">' + dur + "</span></button>";
    } else if (msg.type === "voice_note") {
      media = '<button type="button" class="wave-note" aria-label="' + escapeAttr(t("ariaVoice") + ", " + dur) + '">' +
        '<canvas width="180" height="40"></canvas><span class="dur">' + dur + "</span></button>";
    } else {
      media = '<div class="body"></div>';
    }
    const kindLabel = msg.type === "video_note" ? t("circle") : msg.type === "voice_note" ? t("voice") : t("text");
    const hasTr = !!(msg.translation && msg.transcript && msg.translation !== msg.transcript);
    row.innerHTML = '<div class="meta"><span>' + clock + "</span><span>" + kindLabel +
      "</span></div>" + media + noteToolsHtml(msg);
    if (msg.type === "text") row.querySelector(".body").textContent = msg.translation || msg.text || "";
    const trEl = row.querySelector(".translation");
    if (trEl && msg.translation) trEl.textContent = msg.translation;
    const orig = row.querySelector(".orig-wrap");
    if (orig && msg.transcript) orig.textContent = msg.transcript;
    const bodyEl = row.querySelector(".transcript-body");
    if (bodyEl && msg.transcript && !hasTr) bodyEl.textContent = msg.transcript;
    bindTranscribe(row, msg);
    if (msg.type === "voice_note") {
      const canvas = row.querySelector("canvas");
      const peaks = msg.waveform || [];
      drawWaveform(canvas, peaks, 0);
      const btn = row.querySelector(".wave-note");
      const audio = new Audio(msg.play_url);
      audio.preload = "metadata";
      btn.addEventListener("click", () => {
        if (audio.paused) audio.play(); else audio.pause();
      });
      audio.addEventListener("timeupdate", () => {
        const p = audio.duration ? audio.currentTime / audio.duration : 0;
        drawWaveform(canvas, peaks, p);
      });
    }
    if (msg.type === "video_note") {
      const btn = row.querySelector(".circle-note");
      const v = btn.querySelector("video");
      const fg = btn.querySelector(".ring-fg");
      io.observe(v);
      btn.addEventListener("click", () => {
        v.muted = false;
        if (v.paused) { v.play(); btn.classList.add("is-playing"); }
        else { v.pause(); btn.classList.remove("is-playing"); }
      });
      v.addEventListener("timeupdate", () => {
        const p = v.duration ? v.currentTime / v.duration : 0;
        fg.style.strokeDasharray = String(RING_C);
        fg.style.strokeDashoffset = String(RING_C * (1 - p));
      });
    }
    row.scrollIntoView({ block: "end" });
  }

  async function sendText(ev) {
    if (ev) ev.preventDefault();
    const text = $("composer").value.trim();
    if (!text) return;
    $("composer").value = "";
    $("sendText").hidden = true;
    $("recordBtn").hidden = false;
    try {
      const r = await fetch("/api/chat/dialogs/" + encodeURIComponent(state.dialogId) + "/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ type: "text", text, target_lang: state.target, src_lang: "ru" }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) { toast(body.detail || ("Ошибка " + r.status)); return; }
      upsertMessage(body);
    } catch (e) {
      toast("Сеть: " + e.message);
    }
  }

  async function connect() {
    const tok = await fetch("/api/ws-token").then((r) => r.json());
    state.token = tok.token;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/chat?t=${encodeURIComponent(state.token)}`);
    state.ws = ws;
    ws.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data);
        if (ev.type === "chat.message" && ev.message) upsertMessage(ev.message);
        if (ev.type === "chat.transcript" && ev.message_id) {
          if (ev.transcript && ev.transcript.status && ev.transcript.status !== "pending") {
            if (state.pollers[ev.message_id]) {
              clearInterval(state.pollers[ev.message_id]);
              delete state.pollers[ev.message_id];
            }
          }
        }
        if (ev.type === "chat.deleted" && ev.message_id) {
          const row = document.getElementById("m-" + ev.message_id);
          if (row) row.remove();
        }
      } catch (_) { /* ignore */ }
    };
    ws.onclose = () => setTimeout(connect, 2000);
  }

  async function boot() {
    const [langs, dialogs, settings, chat] = await Promise.all([
      fetch("/api/languages").then((r) => r.json()),
      fetch("/api/chat/dialogs").then((r) => r.json()),
      fetch("/api/settings").then((r) => r.json()),
      fetch("/api/chat").then((r) => r.json()).catch(() => ({})),
    ]);
    state.uiLang = localStorage.getItem("ui_lang") || document.documentElement.lang || "ru";
    if (state.uiLang !== "en") state.uiLang = "ru";
    applyUiLang();
    $("uiLangRu").addEventListener("click", () => {
      state.uiLang = "ru";
      localStorage.setItem("ui_lang", "ru");
      applyUiLang();
      document.querySelectorAll(".msg").forEach((row) => { if (row._msg) upsertMessage(row._msg); });
    });
    $("uiLangEn").addEventListener("click", () => {
      state.uiLang = "en";
      localStorage.setItem("ui_lang", "en");
      applyUiLang();
      document.querySelectorAll(".msg").forEach((row) => { if (row._msg) upsertMessage(row._msg); });
    });
    const tr = (chat && chat.transcription) || {};
    state.entitled = !!tr.entitled;
    state.freeMode = tr.free_mode || "lock";
    const sel = $("target");
    langs.forEach((l) => {
      const o = document.createElement("option");
      o.value = l.code;
      o.textContent = l.label;
      sel.appendChild(o);
    });
    state.target = localStorage.getItem("target_lang") || settings.target_lang || "en";
    sel.value = state.target;
    if (dialogs && dialogs[0]) {
      state.dialogId = dialogs[0].id;
      $("dialogTitle").textContent = dialogs[0].title || "Чат";
    }
    const msgs = await fetch("/api/chat/dialogs/" + encodeURIComponent(state.dialogId) + "/messages").then((r) => r.json());
    msgs.forEach(upsertMessage);
    sel.addEventListener("change", () => {
      state.target = sel.value;
      localStorage.setItem("target_lang", state.target);
    });
    $("composer").addEventListener("input", () => {
      const has = !!$("composer").value.trim();
      $("sendText").hidden = !has;
      $("recordBtn").hidden = has;
      $("composer").style.height = "auto";
      $("composer").style.height = Math.min(120, $("composer").scrollHeight) + "px";
    });
    $("composerForm").addEventListener("submit", sendText);
    $("composer").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) sendText(e);
    });
    bindRecord();
    setModeUI(state.fsm.mode);
    await connect();
  }

  boot().catch((e) => toast("Ошибка загрузки: " + e.message));
})();
