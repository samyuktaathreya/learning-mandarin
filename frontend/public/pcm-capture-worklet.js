/*
 * public/pcm-capture-worklet.js
 *
 * Runs on the browser's audio thread (loaded with audioWorklet.addModule, so
 * it lives in public/ and is served as-is, not bundled). Turns live mic audio
 * (Float32 at the audio device's rate, usually 48 kHz) into 16 kHz mono
 * 16-bit PCM, the format Azure's speech input stream expects.
 *
 * Idle:      keeps the last 200 ms in a ring buffer, so a word started just
 *            before the Record tap isn't clipped.
 * Recording: posts { type: 'chunk', id, pcm: ArrayBuffer } about every 100 ms.
 * On stop:   posts whatever is left, then { type: 'end', id } so the main
 *            thread knows every sample has arrived.
 * `id` identifies the take, so a late message from an old take is ignored.
 */
const TARGET_RATE = 16000;
const CHUNK_SAMPLES = 1600;   // 100 ms
const PREROLL_SAMPLES = 3200; // 200 ms

const toInt16 = (s) => {
    const c = Math.max(-1, Math.min(1, s));
    return c < 0 ? c * 0x8000 : c * 0x7fff;
};

class PcmCaptureProcessor extends AudioWorkletProcessor {
    constructor() {
        super();
        // `sampleRate` is a global in the worklet scope: the AudioContext's rate.
        this.step = sampleRate / TARGET_RATE; // input samples per output sample
        this.phase = 0;
        this.sum = 0;
        this.count = 0;

        this.takeId = null; // set while recording
        this.ring = new Int16Array(PREROLL_SAMPLES);
        this.ringPos = 0;
        this.ringFull = false;
        this.out = new Int16Array(CHUNK_SAMPLES);
        this.outLen = 0;

        this.port.onmessage = (e) => {
            if (e.data.type === 'start') this.start(e.data.id);
            else if (e.data.type === 'stop') this.stop(e.data.id);
        };
    }

    post(pcm) {
        this.port.postMessage({ type: 'chunk', id: this.takeId, pcm: pcm.buffer }, [pcm.buffer]);
    }

    start(id) {
        this.takeId = id;
        this.outLen = 0;
        // Pre-roll first, oldest sample first.
        const n = this.ringFull ? PREROLL_SAMPLES : this.ringPos;
        if (n > 0) {
            const preroll = new Int16Array(n);
            if (this.ringFull) {
                preroll.set(this.ring.subarray(this.ringPos));
                preroll.set(this.ring.subarray(0, this.ringPos), PREROLL_SAMPLES - this.ringPos);
            } else {
                preroll.set(this.ring.subarray(0, n));
            }
            this.post(preroll);
        }
        this.ringPos = 0;
        this.ringFull = false;
    }

    stop(id) {
        if (this.takeId === id && this.outLen > 0) this.post(this.out.slice(0, this.outLen));
        this.outLen = 0;
        this.takeId = null;
        // Always answer, even for an unknown take, so the main thread never waits.
        this.port.postMessage({ type: 'end', id });
    }

    pushSample(v) {
        if (this.takeId !== null) {
            this.out[this.outLen++] = v;
            if (this.outLen === CHUNK_SAMPLES) {
                this.post(this.out); // transfers the buffer, so start a new one
                this.out = new Int16Array(CHUNK_SAMPLES);
                this.outLen = 0;
            }
        } else {
            this.ring[this.ringPos++] = v;
            if (this.ringPos === PREROLL_SAMPLES) {
                this.ringPos = 0;
                this.ringFull = true;
            }
        }
    }

    process(inputs) {
        const channel = inputs[0]?.[0];
        if (channel) {
            // Downsample by averaging the input samples that fall in each output
            // sample's window (also acts as a simple low-pass filter).
            for (let i = 0; i < channel.length; i++) {
                this.sum += channel[i];
                this.count++;
                this.phase += 1;
                if (this.phase >= this.step) {
                    this.pushSample(toInt16(this.sum / this.count));
                    this.phase -= this.step;
                    this.sum = 0;
                    this.count = 0;
                }
            }
        }
        return true; // keep running while the mic is open
    }
}

registerProcessor('pcm-capture', PcmCaptureProcessor);