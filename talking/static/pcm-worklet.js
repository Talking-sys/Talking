// Downsample microphone audio to mono Float32 16 kHz in 20 ms frames.
class PcmDownsampler extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.pos = 0;
    this.out = new Float32Array(320);
    this.n = 0;
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    while (this.pos < ch.length - 1) {
      const i = Math.floor(this.pos), f = this.pos - i;
      this.out[this.n++] = ch[i] * (1 - f) + ch[i + 1] * f;
      if (this.n === this.out.length) {
        this.port.postMessage(this.out.slice(0));
        this.n = 0;
      }
      this.pos += this.ratio;
    }
    this.pos -= ch.length;
    if (this.pos < 0) this.pos = 0;
    return true;
  }
}
registerProcessor("pcm-downsampler", PcmDownsampler);
