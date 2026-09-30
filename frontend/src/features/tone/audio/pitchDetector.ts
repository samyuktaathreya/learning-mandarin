import { PitchDetector } from 'pitchy';
import type { PitchFrame } from '../types';

export interface RawWindow {
  window: Float32Array;
  peak: number;
  /** sample index (since the audio graph started) of the window's last sample */
  endSample: number;
}

/**
 * Turns raw windows from the worklet into PitchFrames using McLeod (MPM) via pitchy.
 * ~0.1 ms per window, so running it 100×/s on the main thread is fine.
 */
export function createFrameAnalyzer(windowSize: number, sampleRate: number) {
  const detector = PitchDetector.forFloat32Array(windowSize);
  let originSample: number | null = null;

  return {
    /** start timing from the next window (call when a new take starts) */
    resetClock() {
      originSample = null;
    },

    analyze({ window, peak, endSample }: RawWindow): PitchFrame {
      // time = centre of the window, relative to the first window of this take
      const centre = endSample - windowSize / 2;
      if (originSample === null) originSample = centre;
      const t = ((centre - originSample) / sampleRate) * 1000;

      let sumSq = 0;
      for (let i = 0; i < window.length; i++) sumSq += window[i] * window[i];
      const rms = Math.sqrt(sumSq / window.length);

      const [hz, clarity] = detector.findPitch(window, sampleRate);
      return {
        t,
        hz: hz > 0 && Number.isFinite(hz) ? hz : null,
        clarity,
        rmsDb: 20 * Math.log10(rms + 1e-10),
        peak,
      };
    },
  };
}
