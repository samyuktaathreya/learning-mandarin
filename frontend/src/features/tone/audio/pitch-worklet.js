/**
 * AudioWorklet processor. Runs on the audio thread, so it stays deliberately dumb:
 * it keeps a rolling window of the band-passed mic signal and, every hop
 * (10 ms), posts a copy of that window plus the raw signal's peak.
 * Pitch detection happens on the main thread (worklets can't import npm packages).
 *
 * Inputs:  0 = band-passed signal (for pitch)   1 = raw signal (for clipping)
 * Messages in: { type: 'active', value: boolean }  — only post windows while recording
 *              { type: 'dispose' }                   — let the processor be garbage-collected
 */
class TonePitchWindowProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const { windowSize, hopSize } = options.processorOptions;
    this.windowSize = windowSize;
    this.hopSize = hopSize;
    this.ring = new Float32Array(windowSize);
    this.writeIdx = 0;
    this.filled = 0;
    this.sinceHop = 0;
    this.hopPeak = 0;
    this.totalSamples = 0;
    this.posting = false;
    this.alive = true;
    this.port.onmessage = (e) => {
      if (e.data.type === 'active') this.posting = e.data.value;
      if (e.data.type === 'dispose') this.alive = false;
    };
  }

  process(inputs) {
    const filtered = inputs[0] && inputs[0][0];
    const raw = inputs[1] && inputs[1][0];
    if (!filtered) return this.alive;

    for (let i = 0; i < filtered.length; i++) {
      this.ring[this.writeIdx] = filtered[i];
      this.writeIdx = (this.writeIdx + 1) % this.windowSize;
      if (this.filled < this.windowSize) this.filled++;

      const a = Math.abs(raw ? raw[i] : filtered[i]);
      if (a > this.hopPeak) this.hopPeak = a;
      this.totalSamples++;

      if (++this.sinceHop >= this.hopSize) {
        this.sinceHop = 0;
        if (this.posting && this.filled === this.windowSize) {
          // unroll the ring buffer into a fresh, transferable array (oldest sample first)
          const win = new Float32Array(this.windowSize);
          win.set(this.ring.subarray(this.writeIdx), 0);
          win.set(this.ring.subarray(0, this.writeIdx), this.windowSize - this.writeIdx);
          this.port.postMessage(
            { window: win, peak: this.hopPeak, endSample: this.totalSamples },
            [win.buffer],
          );
        }
        this.hopPeak = 0;
      }
    }
    return this.alive;
  }
}

registerProcessor('tone-pitch-window', TonePitchWindowProcessor);
