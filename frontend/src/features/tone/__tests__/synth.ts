/**
 * Builds synthetic PitchFrame streams so the analysis can be tested without a mic.
 * Shapes are given as Chao-level waypoints inside a speaker range, like a real
 * recording would look after the detector: pre-roll noise, an unvoiced consonant,
 * the voiced vowel, optional creak, then silence.
 */
import type { PitchFrame, SpeakerRange } from '../types';

export const MALE: SpeakerRange = { lowHz: 90, highHz: 160 };
export const FEMALE: SpeakerRange = { lowHz: 170, highHz: 320 };

export interface SynthOptions {
  /** Chao waypoints, evenly spaced across the voiced part */
  shape: number[];
  range?: SpeakerRange;
  voicedMs?: number;
  preRollMs?: number;
  consonantMs?: number;
  noiseDb?: number;
  voiceDb?: number;
  jitterSt?: number;
  /** creaky frames (energy, no trackable pitch) appended after the voiced part */
  creakTailMs?: number;
  /** frame indices within the voiced part that report double/half the pitch */
  octaveGlitches?: { at: number; factor: number }[];
  /** part of the voiced section (as 0–1 fractions) that goes creaky: energy but no pitch */
  creakDropout?: { from: number; to: number };
  peak?: number;
  seed?: number;
  trailingSilenceMs?: number;
}

function rng(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 2 ** 32 - 0.5; // −0.5 … 0.5
  };
}

function levelAt(shape: number[], u: number): number {
  const pos = u * (shape.length - 1);
  const lo = Math.floor(pos);
  const hi = Math.min(lo + 1, shape.length - 1);
  return shape[lo] + (shape[hi] - shape[lo]) * (pos - lo);
}

export function synth(o: SynthOptions): PitchFrame[] {
  const {
    shape,
    range = MALE,
    voicedMs = 450,
    preRollMs = 300,
    consonantMs = 60,
    noiseDb = -65,
    voiceDb = -25,
    jitterSt = 0.15,
    creakTailMs = 0,
    octaveGlitches = [],
    creakDropout,
    peak = 0.5,
    seed = 1,
    trailingSilenceMs = 150,
  } = o;
  const r = rng(seed);
  const hop = 10;
  const frames: PitchFrame[] = [];
  let t = 0;
  const push = (f: Omit<PitchFrame, 't'>) => {
    frames.push({ t, ...f });
    t += hop;
  };
  const silence = () =>
    push({ hz: r() > 0 ? 200 + 300 * r() : null, clarity: 0.3 + 0.3 * r(), rmsDb: noiseDb + 2 * r(), peak: 0.01 });

  const lowSt = 12 * Math.log2(range.lowHz / 55);
  const highSt = 12 * Math.log2(range.highHz / 55);

  for (let i = 0; i < preRollMs / hop; i++) silence();
  for (let i = 0; i < consonantMs / hop; i++)
    push({ hz: 1000 * (0.3 + r()), clarity: 0.3 + 0.2 * r(), rmsDb: voiceDb - 10, peak: 0.1 });

  const n = voicedMs / hop;
  for (let i = 0; i < n; i++) {
    const level = levelAt(shape, i / (n - 1));
    const st = lowSt + ((level - 1) / 4) * (highSt - lowSt) + jitterSt * 2 * r();
    let hz = 55 * 2 ** (st / 12);
    const g = octaveGlitches.find((x) => x.at === i);
    if (g) hz *= g.factor;
    const u = i / (n - 1);
    if (creakDropout && u >= creakDropout.from && u <= creakDropout.to) {
      push({ hz: r() > 0 ? 70 + 400 * Math.abs(r()) : null, clarity: 0.4 + 0.3 * r(), rmsDb: voiceDb - 8, peak: 0.1 });
      continue;
    }
    push({ hz, clarity: 0.92 + 0.06 * r(), rmsDb: voiceDb + 1.5 * r(), peak });
  }
  for (let i = 0; i < creakTailMs / hop; i++)
    push({ hz: r() > 0 ? 70 + 400 * Math.abs(r()) : null, clarity: 0.4 + 0.3 * r(), rmsDb: voiceDb - 8, peak: 0.1 });
  for (let i = 0; i < trailingSilenceMs / hop; i++) silence();

  return frames;
}

export interface WordOptions {
  /** one Chao-waypoint shape per syllable */
  shapes: number[][];
  /**
   * how syllables join:
   *  consonant — an unvoiced consonant between them (the "c" of "cǎoméi"): no pitch
   *  nasal     — a voiced consonant ("māma"): pitch carries on, only the loudness dips
   *  pause     — silence (a learner saying the syllables one at a time)
   */
  join?: 'consonant' | 'nasal' | 'pause';
  joinMs?: number;
  /** voiced length of each syllable (syllables in a word are shorter than alone) */
  voicedMs?: number | number[];
  /** creak appended to a syllable's end, e.g. { 0: 80 } for a creaky first-syllable tone 3 */
  creakTailMs?: Record<number, number>;
  range?: SpeakerRange;
  preRollMs?: number;
  noiseDb?: number;
  voiceDb?: number;
  jitterSt?: number;
  seed?: number;
  trailingSilenceMs?: number;
}

/** Several syllables in one take, built like synth() builds one. */
export function synthWord(o: WordOptions): PitchFrame[] {
  const {
    shapes,
    join = 'consonant',
    joinMs = join === 'pause' ? 250 : 70,
    voicedMs = 280,
    creakTailMs = {},
    range = MALE,
    preRollMs = 300,
    noiseDb = -65,
    voiceDb = -25,
    jitterSt = 0.15,
    seed = 1,
    trailingSilenceMs = 150,
  } = o;
  const r = rng(seed);
  const hop = 10;
  const frames: PitchFrame[] = [];
  let t = 0;
  const push = (f: Omit<PitchFrame, 't'>) => {
    frames.push({ t, ...f });
    t += hop;
  };
  const silence = () =>
    push({ hz: r() > 0 ? 200 + 300 * r() : null, clarity: 0.3 + 0.3 * r(), rmsDb: noiseDb + 2 * r(), peak: 0.01 });
  const unvoiced = () => push({ hz: 1000 * (0.3 + r()), clarity: 0.3 + 0.2 * r(), rmsDb: voiceDb - 10, peak: 0.1 });
  const creak = () =>
    push({ hz: r() > 0 ? 70 + 400 * Math.abs(r()) : null, clarity: 0.4 + 0.3 * r(), rmsDb: voiceDb - 8, peak: 0.1 });

  const lowSt = 12 * Math.log2(range.lowHz / 55);
  const highSt = 12 * Math.log2(range.highHz / 55);
  const hzAt = (level: number) => 55 * 2 ** ((lowSt + ((level - 1) / 4) * (highSt - lowSt) + jitterSt * 2 * r()) / 12);

  for (let i = 0; i < preRollMs / hop; i++) silence();
  for (let i = 0; i < 60 / hop; i++) unvoiced(); // first syllable's consonant

  shapes.forEach((shape, s) => {
    const n = (Array.isArray(voicedMs) ? voicedMs[s] : voicedMs) / hop;
    for (let i = 0; i < n; i++)
      push({ hz: hzAt(levelAt(shape, i / (n - 1))), clarity: 0.92 + 0.06 * r(), rmsDb: voiceDb + 1.5 * r(), peak: 0.5 });
    for (let i = 0; i < (creakTailMs[s] ?? 0) / hop; i++) creak();

    if (s === shapes.length - 1) return;
    const next = shapes[s + 1];
    const m = joinMs / hop;
    for (let i = 0; i < m; i++) {
      if (join === 'pause') silence();
      else if (join === 'consonant') unvoiced();
      else {
        // nasal: pitch glides from this syllable's end to the next one's start, quieter
        const level = shape.at(-1)! + ((next[0] - shape.at(-1)!) * (i + 1)) / (m + 1);
        push({ hz: hzAt(level), clarity: 0.9 + 0.05 * r(), rmsDb: voiceDb - 9 + r(), peak: 0.2 });
      }
    }
  });
  for (let i = 0; i < trailingSilenceMs / hop; i++) silence();
  return frames;
}
