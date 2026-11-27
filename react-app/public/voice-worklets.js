/*
 * Audio-thread processors for Vasco's voice mode (they keep running smoothly even when the page is busy).
 *
 * pcm-player : plays Vasco's 24 kHz speech as ONE continuous stream. Chunks go into a ring buffer and
 *              are resampled continuously to the device rate (no clicks between chunks), with a small
 *              pre-buffer so network jitter never tears the voice.
 * mic-tap    : collects microphone samples in blocks of 2048 and posts them to the page.
 */
const SRC_RATE = 24000;
const START_BUFFER = 0.32 * SRC_RATE; // pre-buffer before speaking starts
const REBUFFER = 0.2 * SRC_RATE; // pre-buffer after the network ran dry mid-answer
const MAX_EXTRA = 0.35 * SRC_RATE; // adaptive extra buffer on bad networks (grows on each dropout)
const FADE = 96; // samples of fade-in/out at start/stop, avoids pops

class PcmPlayer extends AudioWorkletProcessor {
  constructor() {
    super();
    this.size = SRC_RATE * 120;
    this.buf = new Float32Array(this.size);
    this.w = 0; // samples written (absolute)
    this.r = 0; // read position (absolute, fractional)
    this.step = SRC_RATE / sampleRate;
    this.playing = false;
    this.extra = 0;
    this.pre = START_BUFFER;
    this.gain = 0;
    this.port.onmessage = (e) => this.handle(e.data);
  }

  handle(d) {
    if (d && d.port) {
      // A direct line from the network worker: audio no longer goes through the page.
      d.port.onmessage = (ev) => this.handle(ev.data);
      return;
    }
    if (d === 'clear') {
      this.r = this.w;
      this.pre = START_BUFFER + this.extra;
      if (this.playing) {
        this.playing = false;
        this.port.postMessage('idle');
      }
      return;
    }
    if (d === 'flush') {
      this.pre = 0; // answer complete: play whatever is left, even if short
      return;
    }
    if (!d || !d.length) return;
    if (this.w - this.r + d.length >= this.size) this.r = this.w + d.length - this.size + 1; // overflow guard
    let i = this.w % this.size;
    for (let k = 0; k < d.length; k++) {
      this.buf[i] = d[k];
      i = i + 1 === this.size ? 0 : i + 1;
    }
    this.w += d.length;
  }

  process(_inputs, outputs) {
    const out = outputs[0][0];
    if (!out) return true;
    if (!this.playing) {
      const avail = this.w - this.r;
      if (avail > 1 && avail >= this.pre) {
        this.playing = true;
        this.gain = 0;
        this.port.postMessage('playing');
      } else {
        out.fill(0);
        return true;
      }
    }
    const size = this.size;
    for (let n = 0; n < out.length; n++) {
      const left = this.w - this.r;
      if (left < 2) {
        // Ran dry: fade already reached the last sample; wait for more data. If this happened in the
        // middle of an answer (no 'flush' yet), the network is jittery: keep a bigger cushion from now on.
        out.fill(0, n);
        this.playing = false;
        if (this.pre !== 0) this.extra = Math.min(MAX_EXTRA, this.extra + 0.1 * SRC_RATE);
        this.pre = REBUFFER + this.extra;
        this.port.postMessage('idle');
        break;
      }
      const i0 = Math.floor(this.r);
      const f = this.r - i0;
      const a = this.buf[i0 % size];
      const b = this.buf[(i0 + 1) % size];
      // Fade in at start; fade out when the buffer is about to run dry.
      if (this.gain < 1) this.gain = Math.min(1, this.gain + 1 / FADE);
      const tail = left < FADE ? left / FADE : 1;
      out[n] = (a + (b - a) * f) * this.gain * tail;
      this.r += this.step;
    }
    for (let c = 1; c < outputs[0].length; c++) outputs[0][c].set(out);
    return true;
  }
}

class MicTap extends AudioWorkletProcessor {
  constructor() {
    super();
    this.block = new Float32Array(2048);
    this.n = 0;
  }

  process(inputs, outputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) {
      for (let i = 0; i < ch.length; i++) {
        this.block[this.n++] = ch[i];
        if (this.n === this.block.length) {
          this.port.postMessage(this.block, [this.block.buffer]);
          this.block = new Float32Array(2048);
          this.n = 0;
        }
      }
    }
    if (outputs[0] && outputs[0][0]) outputs[0][0].fill(0);
    return true;
  }
}

registerProcessor('pcm-player', PcmPlayer);
registerProcessor('mic-tap', MicTap);
