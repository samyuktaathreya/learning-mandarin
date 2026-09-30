import { TONE_CONFIG } from '../config';
import type { PitchFrame } from '../types';
import { hzToSt, median, percentile } from './math';
import type { StRange } from './normalize';

export interface Segment {
  /** frame indices, inclusive */
  startIdx: number;
  endIdx: number;
  startMs: number;
  endMs: number;
  /** frames with a trustworthy pitch */
  pitchedCount: number;
  /** frames with voice energy but no usable pitch (creak / raspy voice) */
  creakCount: number;
}

/**
 * pitched  — clean, steady pitch the classifier can use
 * creak    — voice energy without usable pitch: dropouts, pulses below 60 Hz,
 *            or erratic readings BELOW the voice (subharmonics) — a raspy voice
 * unstable — erratic readings at normal height: tracker trouble, not creak
 * quiet    — background
 */
export type FrameKind = 'pitched' | 'creak' | 'unstable' | 'quiet';

export function isPitched(f: PitchFrame, floorDb: number): boolean {
  const { minHz, maxHz } = TONE_CONFIG.detector;
  const { minClarity, minLevelAboveFloorDb } = TONE_CONFIG.gate;
  return (
    f.hz !== null &&
    f.hz >= minHz &&
    f.hz <= maxHz &&
    f.clarity >= minClarity &&
    f.rmsDb >= floorDb + minLevelAboveFloorDb
  );
}

export const hasEnergy = (f: PitchFrame, floorDb: number) =>
  f.rmsDb >= floorDb + TONE_CONFIG.gate.creakLevelAboveFloorDb;

/**
 * Label every frame. A frame that passes the pitch gate still counts as creak
 * (or unstable) if it fits neither neighbour NOR the local slope: real voice
 * moves smoothly over 10 ms, even in a fast tone 4 that drops ~2 semitones per
 * frame, while a raspy voice makes the tracker jump around at random.
 */
export function classifyFrames(frames: PitchFrame[], floorDb: number, range: StRange | null = null): FrameKind[] {
  const { irregularSt, maxStepSt } = TONE_CONFIG.segment;
  const st = frames.map((f) => (isPitched(f, floorDb) ? hzToSt(f.hz!) : null));
  const at = (i: number) => (i >= 0 && i < st.length ? st[i] : null);

  const agrees = (i: number, j: number) => {
    const a = at(i);
    const b = at(j);
    if (a === null || b === null) return false;
    // an exact octave jump is a tracker error, not creak; correctOctaves fixes those
    const d = Math.abs(a - b);
    return d <= irregularSt || Math.abs(d - 12) <= irregularSt;
  };

  // on a steady slope: close to the straight line through its neighbours
  // (or, at the edge of a run, the line extended from the next two frames)
  const onSlope = (i: number) => {
    const v = at(i);
    if (v === null) return false;
    const fits = (a: number | null, b: number | null, predicted: (a: number, b: number) => number) =>
      a !== null && b !== null && Math.abs(a - b) <= 2 * maxStepSt && Math.abs(v - predicted(a, b)) <= irregularSt / 2;
    return (
      fits(at(i - 1), at(i + 1), (a, b) => (a + b) / 2) ||
      fits(at(i + 1), at(i + 2), (a, b) => 2 * a - b) ||
      fits(at(i - 1), at(i - 2), (a, b) => 2 * a - b)
    );
  };

  const regular = st.map((v, i) => v !== null && (agrees(i, i - 1) || agrees(i, i + 1) || onSlope(i)));
  const continues = (j: number) => {
    const a = at(j);
    const b = at(j + 1);
    return a !== null && b !== null && (Math.abs(a - b) <= maxStepSt || agrees(j, j + 1));
  };

  // real voice gives long steady runs; 2–3 frames that happen to agree inside a
  // stretch of creak are chance, not clean pitch
  const { minRunFrames } = TONE_CONFIG.segment;
  for (let i = 0; i < regular.length; ) {
    if (!regular[i]) { i++; continue; }
    let j = i;
    while (j + 1 < regular.length && regular[j + 1] && continues(j)) j++;
    if (j - i + 1 < minRunFrames) for (let k = i; k <= j; k++) regular[k] = false;
    i = j + 1;
  }

  // erratic readings only count as creak when they sit below the clean voice
  // (well below the clean voice, or low in absolute terms: creak pulses sit at
  // the very bottom of anyone's voice)
  const regularSt = st.filter((v, i): v is number => regular[i]);
  const absoluteCeilingSt = range
    ? range.lowSt + TONE_CONFIG.creak.maxStAboveLow
    : hzToSt(TONE_CONFIG.creak.maxHzUncalibrated);
  const creakCeilingSt =
    regularSt.length >= 3
      ? Math.max(median(regularSt) - TONE_CONFIG.segment.creakBelowVoiceSt, absoluteCeilingSt)
      : absoluteCeilingSt;

  return frames.map((f, i) => {
    if (regular[i]) return 'pitched';
    if (!hasEnergy(f, floorDb)) return 'quiet';
    if (st[i] !== null && st[i]! > creakCeilingSt) return 'unstable';
    return 'creak';
  });
}

export function frameHopMs(frames: PitchFrame[]): number {
  if (frames.length < 2) return TONE_CONFIG.detector.hopMs;
  return median(frames.slice(1).map((f, i) => f.t - frames[i].t));
}

/**
 * Find the syllable: the longest run of pitched frames, where
 *  - short gaps (< bridgeGapMs) are always bridged, and
 *  - longer gaps are bridged if they are creak following low pitch,
 *    which is what a raspy tone 3 looks like to a pitch tracker.
 * Creak right after a low ending is kept as a creak tail.
 */
export function findVoicedSegment(
  frames: PitchFrame[],
  floorDb: number,
  kinds: FrameKind[] = classifyFrames(frames, floorDb),
): Segment | null {
  const cfg = TONE_CONFIG.segment;
  const hop = frameHopMs(frames);
  const pitched = kinds.map((k) => k === 'pitched');
  const allSt = frames.flatMap((f, i) => (pitched[i] ? [hzToSt(f.hz!)] : []));
  if (!allSt.length) return null;
  const lowSt = percentile(allSt, cfg.lowPitchPercentile);

  // 1. runs of consecutive pitched frames
  const runs: { s: number; e: number; creak: number }[] = [];
  for (let i = 0; i < frames.length; i++) {
    if (!pitched[i]) continue;
    const last = runs[runs.length - 1];
    if (last && last.e === i - 1) last.e = i;
    else runs.push({ s: i, e: i, creak: 0 });
  }

  // 2. merge runs across bridgeable gaps
  const merged = [runs[0]];
  for (const run of runs.slice(1)) {
    const prev = merged[merged.length - 1];
    const gapFrames = run.s - prev.e - 1;
    const gapMs = gapFrames * hop;
    const gapKinds = kinds.slice(prev.e + 1, run.s);
    const prevLow = hzToSt(frames[prev.e].hz!) <= lowSt;
    const nextLow = hzToSt(frames[run.s].hz!) <= lowSt;
    // creak after a low stretch (raspy tone 3 ending) or before one (falling into the dip)
    const isCreak = gapKinds.every((k) => k === 'creak') && (prevLow || nextLow);

    if (gapMs < cfg.bridgeGapMs || (isCreak && gapMs <= cfg.creakGapMs)) {
      prev.e = run.e;
      prev.creak += gapKinds.filter((k) => k === 'creak').length + run.creak;
    } else {
      merged.push({ ...run });
    }
  }

  // 3. keep the longest
  const best = merged.reduce((a, b) => (b.e - b.s > a.e - a.s ? b : a));

  // 4. creak tail after a low ending
  let end = best.e;
  let creak = best.creak;
  if (hzToSt(frames[best.e].hz!) <= lowSt) {
    const maxTail = Math.round(cfg.creakTailMaxMs / hop);
    for (let i = best.e + 1; i < frames.length && i - best.e <= maxTail; i++) {
      if (kinds[i] !== 'creak') break;
      end = i;
      creak++;
    }
  }

  let pitchedCount = 0;
  for (let i = best.s; i <= end; i++) if (pitched[i]) pitchedCount++;

  return {
    startIdx: best.s,
    endIdx: end,
    startMs: frames[best.s].t,
    endMs: frames[end].t + hop,
    pitchedCount,
    creakCount: creak,
  };
}

export interface VoiceRun {
  startIdx: number;
  endIdx: number;
  durationMs: number;
  pitchedCount: number;
  creakCount: number;
}

/**
 * Longest stretch of voice of ANY kind (pitched or creak), bridging short
 * quiet gaps. Used when the pitch-based segment fails, to recognize a take
 * that was mostly or entirely raspy.
 */
export function findVoiceRun(kinds: FrameKind[], hopMs: number, fromIdx = 0): VoiceRun | null {
  const maxGap = Math.round(TONE_CONFIG.segment.bridgeGapMs / hopMs);
  let best: VoiceRun | null = null;
  let start = -1;
  let lastVoice = -1;
  const close = () => {
    if (start < 0) return;
    const slice = kinds.slice(start, lastVoice + 1);
    const run: VoiceRun = {
      startIdx: start,
      endIdx: lastVoice,
      durationMs: (lastVoice - start + 1) * hopMs,
      pitchedCount: slice.filter((k) => k === 'pitched').length,
      creakCount: slice.filter((k) => k === 'creak').length,
    };
    if (!best || run.durationMs > best.durationMs) best = run;
  };
  for (let i = fromIdx; i < kinds.length; i++) {
    if (kinds[i] === 'quiet') {
      if (start >= 0 && i - lastVoice > maxGap) {
        close();
        start = -1;
      }
      continue;
    }
    if (start < 0) start = i;
    lastVoice = i;
  }
  close();
  return best;
}