import { TONE_TARGETS } from '../config';
import type { Tone } from '../types';

function interp(points: number[], u: number): number {
  const pos = Math.min(1, Math.max(0, u)) * (points.length - 1);
  const lo = Math.floor(pos);
  const hi = Math.min(lo + 1, points.length - 1);
  return points[lo] + (points[hi] - points[lo]) * (pos - lo);
}

/**
 * The tolerance band for a tone at normalized time u (0–1), on the Chao scale.
 * For tone 3 the band covers both accepted shapes (dip-rise and low fall).
 */
export function bandAt(tone: Tone, u: number) {
  const t = TONE_TARGETS[tone];
  const main = interp(t.points, u);
  const alt = t.alt ? interp(t.alt, u) : main;
  return {
    center: main,
    lo: Math.min(main, alt) - t.tolerance,
    hi: Math.max(main, alt) + t.tolerance,
  };
}

export function isInBand(tone: Tone, u: number, level: number): boolean {
  const b = bandAt(tone, u);
  return level >= b.lo && level <= b.hi;
}

/**
 * Uncalibrated results only carry the SHAPE (absolute height is unknown), so
 * shift the contour vertically to sit as close to the target as possible.
 */
export function alignToTarget(tone: Tone, contour: { x: number; level: number }[]) {
  if (!contour.length) return contour;
  const offset =
    contour.reduce((sum, p) => sum + (bandAt(tone, p.x).center - p.level), 0) / contour.length;
  return contour.map((p) => ({ x: p.x, level: p.level + offset }));
}

/**
 * alignToTarget for a word: one vertical shift for every syllable together,
 * so a syllable that really was higher than its neighbour still looks higher.
 * `contours[i]` belongs to `tones[i]`; neutral-tone (5) syllables are left out
 * of the fit but shifted with the rest.
 */
export function alignSequenceToTargets(tones: (Tone | 5)[], contours: { x: number; level: number }[][]) {
  let sum = 0;
  let count = 0;
  contours.forEach((contour, i) => {
    const tone = tones[i];
    if (tone === 5) return;
    for (const p of contour) {
      sum += bandAt(tone, p.x).center - p.level;
      count++;
    }
  });
  const offset = count ? sum / count : 0;
  return contours.map((contour) => contour.map((p) => ({ x: p.x, level: p.level + offset })));
}
