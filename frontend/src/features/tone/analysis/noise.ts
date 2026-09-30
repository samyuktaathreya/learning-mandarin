import { TONE_CONFIG } from '../config';
import type { PitchFrame } from '../types';
import { median, percentile } from './math';

/**
 * Background level in dBFS. Prefers the pre-roll (recorded before the user
 * was prompted); falls back to the quietest 10% of the whole recording.
 */
export function estimateNoiseFloor(frames: PitchFrame[], preRollMs: number): number {
  const preRoll = frames.filter((f) => f.t < preRollMs).map((f) => f.rmsDb);
  if (preRoll.length >= 5) return median(preRoll);
  return percentile(
    frames.map((f) => f.rmsDb),
    10,
  );
}

/** Level of the loudest sustained part of the recording (90th percentile). */
export function estimateSpeechLevel(frames: PitchFrame[]): number {
  return percentile(
    frames.map((f) => f.rmsDb),
    90,
  );
}

export function isClipping(frames: PitchFrame[]): boolean {
  const { clipPeak, clipMinFrames } = TONE_CONFIG.quality;
  return frames.filter((f) => f.peak >= clipPeak).length >= clipMinFrames;
}
