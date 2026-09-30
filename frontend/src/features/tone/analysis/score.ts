import { TONE_CONFIG } from '../config';
import type { Tone, ToneFeatures } from '../types';
import { topTone } from './classify';

export function scoreAttempt(expected: Tone, scores: Record<Tone, number>) {
  const { passThreshold, leniencyMargin, leniencyMinScore } = TONE_CONFIG.scoring;
  const detected = topTone(scores);
  const mine = scores[expected];
  const best = scores[detected];

  const isCorrect =
    (detected === expected && mine >= passThreshold) ||
    (mine >= leniencyMinScore && best - mine < leniencyMargin);

  return {
    detectedTone: isCorrect ? expected : detected,
    isCorrect,
    score: Math.round(100 * mine),
  };
}

/** Hints for a raspy take when the target wasn't tone 3. */
export const CREAK_HINTS: Record<Tone, string | null> = {
  1: 'Your voice dropped into a low creak — keep tone 1 high and clear.',
  2: 'Your voice went low and creaky — tone 2 should rise clearly from the middle.',
  3: null,
  4: 'Your voice went creaky — start high and fall clearly.',
};

/** One actionable sentence about what to change. Null when correct. */
export function buildHint(
  expected: Tone,
  detected: Tone,
  isCorrect: boolean,
  f: ToneFeatures,
): string | null {
  if (isCorrect) return null;
  const rose = f.rise > 1.5;
  const fell = f.fall > 1.5;

  switch (expected) {
    case 1:
      if (rose) return 'Your pitch rose — keep tone 1 high and level.';
      if (fell) return 'Your pitch dropped — hold it steady and high.';
      return 'Keep your pitch level and a little higher.';
    case 2:
      if (fell) return "Your pitch fell — tone 2 should rise, like asking 'huh?'";
      if (detected === 3) return 'You dipped too long — start rising sooner.';
      return 'Rise more — start mid and finish high.';
    case 3:
      if (detected === 4) return 'Start lower — tone 3 lives at the bottom of your voice.';
      if (detected === 2) return 'Go lower first — dip down before coming back up.';
      return 'Drop your pitch down low.';
    case 4:
      if (rose) return "Your pitch rose — tone 4 should fall, like a firm 'No!'";
      if (detected === 3) return 'Start higher and drop all the way down.';
      return 'Fall more sharply — start high and drop.';
  }
}