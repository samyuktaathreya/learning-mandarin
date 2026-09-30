import { TONE_CONFIG } from '../config';
import type { SpeakerRange } from '../types';
import { hzToSt, median, percentile, stToHz } from './math';

export interface StRange {
  lowSt: number;
  highSt: number;
}

/**
 * Fix octave errors (the tracker suddenly reporting ×2 or ÷2 the real pitch).
 * Starting from the frame closest to the median, walk outward in both
 * directions; any jump larger than `octaveJumpSt` between neighbours gets
 * shifted by ±12 semitones, whichever lands closer to the previous frame.
 */
export function correctOctaves(st: (number | null)[]): (number | null)[] {
  const out = [...st];
  const known = out.flatMap((v, i) => (v === null ? [] : [i]));
  if (known.length < 2) return out;
  const med = median(known.map((i) => out[i] as number));
  const anchor = known.reduce((a, b) =>
    Math.abs((out[b] as number) - med) < Math.abs((out[a] as number) - med) ? b : a,
  );

  const walk = (order: number[]) => {
    let prev = out[anchor] as number;
    for (const i of order) {
      let v = out[i];
      if (v === null) continue;
      if (Math.abs(v - prev) > TONE_CONFIG.contour.octaveJumpSt) {
        const candidates = [v - 12, v + 12, v - 24, v + 24];
        const best = candidates.reduce((a, b) => (Math.abs(b - prev) < Math.abs(a - prev) ? b : a));
        if (Math.abs(best - prev) < Math.abs(v - prev)) v = best;
        out[i] = v;
      }
      prev = v;
    }
  };
  const forward = known.filter((i) => i > anchor);
  const backward = known.filter((i) => i < anchor).reverse();
  walk(forward);
  walk(backward);
  return out;
}

export function speakerRangeToSt(range: SpeakerRange): StRange {
  let lowSt = hzToSt(range.lowHz);
  let highSt = hzToSt(range.highHz);
  const minSpan = TONE_CONFIG.range.minSpanSt;
  if (highSt - lowSt < minSpan) {
    const mid = (lowSt + highSt) / 2;
    lowSt = mid - minSpan / 2;
    highSt = mid + minSpan / 2;
  }
  return { lowSt, highSt };
}

/** Uncalibrated: assume the attempt's median sits mid-range. Good enough to draw the shape. */
export function fallbackRange(contourSt: number[]): StRange {
  const mid = median(contourSt);
  const half = TONE_CONFIG.range.fallbackSpanSt / 2;
  return { lowSt: mid - half, highSt: mid + half };
}

/** Semitones → Chao level (1 = bottom of the speaker's range, 5 = top). */
export function toChaoLevel(st: number, range: StRange): number {
  return 1 + (4 * (st - range.lowSt)) / (range.highSt - range.lowSt);
}

/**
 * Estimate a speaker's range from pitch values (Hz) collected over past
 * attempts or a calibration round. Uses the 5th/95th percentiles so a few
 * stray frames don't stretch it.
 */
export function estimateSpeakerRange(pitchesHz: number[]): SpeakerRange | null {
  if (pitchesHz.length < 20) return null;
  const st = pitchesHz.map(hzToSt);
  return { lowHz: stToHz(percentile(st, 5)), highHz: stToHz(percentile(st, 95)) };
}
