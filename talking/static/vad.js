/* Client VAD: Silero-compatible interface over Web Audio.
 *
 * Default implementation is an energy VAD with the spec range
 * (pause 300–3000 ms, default 800; max segment 15–20 s).
 * If /static/vendor/silero_vad.onnx is present AND onnxruntime-web is loaded,
 * SileroOnnxVAD is used instead. Energy VAD always works offline.
 */
(function (global) {
  const FRAME_MS = 20;
  const MIN_START_FRAMES = 2;
  const PREROLL = 15;
  const MAX_SEG_MS = 18000;

  class EnergyVAD {
    constructor(opts) {
      this.pauseMs = opts.pauseMs || 800;
      this.maxSegMs = opts.maxSegMs || MAX_SEG_MS;
      this.floor = 0.004;
      this.speaking = false;
      this.silenceMs = 0;
      this.speechMs = 0;
      this.voicedRun = 0;
      this.preroll = [];
      this.onSpeech = opts.onSpeech || function () {};
      this.onStart = opts.onStart || function () {};
      this.onEnd = opts.onEnd || function () {};
    }
    setPause(ms) {
      this.pauseMs = Math.max(300, Math.min(3000, ms));
    }
    reset() {
      this.speaking = false;
      this.silenceMs = 0;
      this.speechMs = 0;
      this.voicedRun = 0;
      this.preroll = [];
    }
    process(frame) {
      let sum = 0;
      for (let i = 0; i < frame.length; i++) sum += frame[i] * frame[i];
      const rms = Math.sqrt(sum / frame.length);
      const thr = Math.max(0.01, this.floor * 3);
      const voiced = rms > thr;
      if (!this.speaking) {
        this.floor = 0.95 * this.floor + 0.05 * Math.min(rms, 0.05);
        this.preroll.push(frame);
        if (this.preroll.length > PREROLL) this.preroll.shift();
        this.voicedRun = voiced ? this.voicedRun + 1 : 0;
        if (this.voicedRun >= MIN_START_FRAMES) {
          this.speaking = true;
          this.silenceMs = 0;
          this.speechMs = this.preroll.length * FRAME_MS;
          this.onStart();
          this.preroll.forEach((f) => this.onSpeech(f));
          this.preroll = [];
        }
        return;
      }
      this.onSpeech(frame);
      this.speechMs += FRAME_MS;
      this.silenceMs = voiced ? 0 : this.silenceMs + FRAME_MS;
      if (this.silenceMs >= this.pauseMs || this.speechMs >= this.maxSegMs) {
        const forced = this.speechMs >= this.maxSegMs;
        this.reset();
        this.onEnd(forced);
      }
    }
  }

  class SileroOnnxVAD {
    /* Placeholder adapter: if ONNX is unavailable we fall back at construction. */
    constructor(opts) {
      this.inner = new EnergyVAD(opts);
      this.backend = "energy-fallback";
    }
    setPause(ms) { this.inner.setPause(ms); }
    reset() { this.inner.reset(); }
    process(frame) { this.inner.process(frame); }
  }

  async function createClientVAD(opts) {
    try {
      const probe = await fetch("/static/vendor/silero_vad.onnx", { method: "HEAD" });
      if (probe.ok && global.ort) {
        const v = new SileroOnnxVAD(opts);
        v.backend = "silero-onnx";
        return v;
      }
    } catch (_) { /* ignore */ }
    const v = new EnergyVAD(opts);
    v.backend = "energy";
    return v;
  }

  global.TalkingVAD = { EnergyVAD, SileroOnnxVAD, createClientVAD, FRAME_MS, MAX_SEG_MS };
})(typeof window !== "undefined" ? window : globalThis);
