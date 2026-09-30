import { TONE_CONFIG } from '../config';
import type { Tone, ToneFeatures } from '../types';
import { mean, sigmoid } from './math';
import { toChaoLevel, type StRange } from './normalize';

const indexOfMin = (xs: number[]) => xs.reduce((best, v, i) => (v < xs[best] ? i : best), 0);

/**
 * @param contour resampled, smoothed contour in semitones (evenly spaced in time)
 * @param creakRatio share of the syllable that was creaky / untrackable
 * @param range the speaker's range, or null if uncalibrated
 */
export function extractFeatures(
  contour: number[],
  creakRatio: number,
  range: StRange | null,
): ToneFeatures {
  const n = contour.length;
  const start = mean(contour.slice(0, 3));
  const end = mean(contour.slice(-3));
  const midVal = contour[Math.floor((n - 1) / 2)];
  const minIdx = indexOfMin(contour);
  const min = contour[minIdx];
  const max = Math.max(...contour);

  return {
    start,
    end,
    min,
    max,
    minPos: minIdx / (n - 1),
    span: max - min,
    rise: end - start,
    fall: start - end,
    dip: start - min,
    recover: end - min,
    deceleration: start - midVal - (midVal - end),
    creakRatio,
    startLevel: range ? toChaoLevel(start, range) : null,
    meanLevel: range ? toChaoLevel(mean(contour), range) : null,
    minLevel: range ? toChaoLevel(min, range) : null,
  };
}

/**
 * How plausible each tone is, 0–1, judged mainly on SHAPE (direction, slope,
 * where the low point falls) so it works for any voice. Height on the Chao
 * scale is used only as a secondary cue when the speaker is calibrated.
 */
export function scoreTones(f: ToneFeatures): Record<Tone, number> {
  const c = TONE_CONFIG.classify;
  const calibrated = f.startLevel !== null && f.meanLevel !== null;
  const creakCue = sigmoid(f.creakRatio, c.t3.creak);

  // Tone 1: level. High, if we know the speaker's range.
  let t1 = 1 - sigmoid(f.span, c.t1.flatSpan);
  if (calibrated) t1 *= 0.5 + 0.5 * sigmoid(f.meanLevel!, c.t1.highLevel);
  t1 *= 1 - creakCue;

  // Tone 2: rising. A small early dip is normal; a deep, late one is tone 3.
  const t2 =
    sigmoid(f.rise, c.t2.rise) *
    (1 - sigmoid(f.minPos, c.t2.lateMin) * sigmoid(f.dip, c.t2.deepDip));

  // Tone 3, variant A: dip then rise (214). Low point in the middle.
  const midMin =
    sigmoid(f.minPos, c.t3.midMinStart) * (1 - sigmoid(f.minPos, c.t3.midMinEnd));
  const t3Dip = midMin * sigmoid(f.dip, c.t3.dip) * sigmoid(f.recover, c.t3.recover);

  // Tone 3, variant B: low fall (21). A modest fall that levels off,
  // goes creaky, or starts low in the speaker's range.
  const lowStartCue = calibrated ? 1 - sigmoid(f.startLevel!, c.t3.highStart) : 0;
  const shapeCue =
    0.5 + 0.5 * Math.max(sigmoid(f.deceleration, c.t3.deceleration), creakCue, lowStartCue);
  let t3Fall =
    sigmoid(f.fall, c.t3.fall) *
    (1 - sigmoid(f.fall, c.t3.modestFall)) *
    (1 - sigmoid(f.recover, c.t4.lateRecover)) *
    shapeCue;
  if (calibrated) t3Fall *= 1 - sigmoid(f.startLevel!, c.t3.highStart);

  // Tone 3, variant C: held low (calibrated only).
  const t3Low = calibrated
    ? (1 - sigmoid(f.span, c.t1.flatSpan)) * (1 - sigmoid(f.meanLevel!, c.t3.lowLevel))
    : 0;

  // Tone 3, variant D: raspy / creaky. Creak is itself the tone-3 cue, whatever
  // the exact contour, unless the pitch clearly rose a lot (tone 2) or fell
  // from high (tone 4, which often goes creaky at the very end).
  let t3Creak =
    creakCue * (1 - sigmoid(f.rise, c.t3.creakRise)) * (1 - sigmoid(f.fall, c.t3.creakFall));
  if (calibrated) t3Creak *= 1 - sigmoid(f.minLevel!, c.t3.creakLowMin);

  const t3 = Math.max(t3Dip, t3Fall, t3Low, t3Creak);

  // Tone 4: falling, without a rise at the end. Starts high if calibrated.
  let t4 = sigmoid(f.fall, c.t4.fall) * (1 - sigmoid(f.recover, c.t4.lateRecover));
  if (calibrated) t4 *= 0.4 + 0.6 * sigmoid(f.startLevel!, c.t4.highStart);

  // Tone 4, creaky variant: starts high, then breaks into creak. The fall
  // happened where the tracker couldn't follow it.
  const t4Creak = calibrated
    ? creakCue * (1 - sigmoid(f.rise, c.t3.creakRise)) * sigmoid(f.startLevel!, c.t4.highStart)
    : 0;

  return { 1: t1, 2: t2, 3: t3, 4: Math.max(t4, t4Creak) };
}

export function topTone(scores: Record<Tone, number>): Tone {
  return ([1, 2, 3, 4] as Tone[]).reduce((a, b) => (scores[b] > scores[a] ? b : a));
}