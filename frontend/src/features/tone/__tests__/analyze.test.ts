import { describe, expect, it } from 'vitest';
import { analyzeTone } from '../analysis/analyze';
import type { SpeakerRange, Tone, ToneSuccess } from '../types';
import { FEMALE, MALE, synth, type SynthOptions } from './synth';

type Case = { name: string; tone: Tone; opts: SynthOptions; calibratedOnly?: boolean };

// Chao waypoints. "native" = textbook contour, "learner" = smaller, sloppier movement.
const CASES: Case[] = [
  { name: 'T1 native (55)', tone: 1, opts: { shape: [5, 5, 5] } },
  { name: 'T1 learner, slightly low and wobbly', tone: 1, opts: { shape: [4, 4.3, 4.1], jitterSt: 0.3 } },
  { name: 'T2 native (35) with early dip', tone: 2, opts: { shape: [3, 2.7, 3.5, 5] } },
  { name: 'T2 learner, small rise', tone: 2, opts: { shape: [2.5, 2.6, 4] } },
  { name: 'T3 full dip-rise (214)', tone: 3, opts: { shape: [2, 1.2, 1, 3.5] } },
  { name: 'T3 low fall (21) + creak', tone: 3, opts: { shape: [2.2, 1.4, 1.1, 1], creakTailMs: 120 } },
  { name: 'T3 low fall (21), no creak', tone: 3, opts: { shape: [2.2, 1.4, 1.1, 1] }, calibratedOnly: true },
  { name: 'T3 creak in the middle of the dip', tone: 3, opts: { shape: [2, 1, 1, 3], creakDropout: { from: 0.35, to: 0.6 } } },
  { name: 'T4 native (51)', tone: 4, opts: { shape: [5, 3.5, 1] } },
  { name: 'T4 learner, smaller fall', tone: 4, opts: { shape: [4.5, 3.5, 2.5] } },
  { name: 'T4 with creaky ending', tone: 4, opts: { shape: [5, 3, 1.2], creakTailMs: 80 } },
];

const VOICES: [string, SpeakerRange][] = [
  ['male', MALE],
  ['female', FEMALE],
];

function run(opts: SynthOptions, tone: Tone, range: SpeakerRange, calibrated: boolean, seed = 1) {
  return analyzeTone({
    frames: synth({ ...opts, range, seed }),
    expectedTone: tone,
    speakerRange: calibrated ? range : null,
  });
}

describe('classifies every tone across voices and calibration states', () => {
  for (const c of CASES)
    for (const [voice, range] of VOICES)
      for (const calibrated of [true, false]) {
        if (c.calibratedOnly && !calibrated) continue;
        it(`${c.name} — ${voice}, ${calibrated ? 'calibrated' : 'uncalibrated'}`, () => {
          for (const seed of [1, 2, 3]) {
            const r = run(c.opts, c.tone, range, calibrated, seed);
            expect(r.ok, JSON.stringify(r)).toBe(true);
            const s = r as ToneSuccess;
            expect(s.isCorrect, `seed ${seed}: ${JSON.stringify(s.toneScores)}`).toBe(true);
            expect(s.score).toBeGreaterThanOrEqual(50);
          }
        });
      }
});

describe('rejects the wrong tone', () => {
  // every native shape, scored against every OTHER expected tone
  const natives = CASES.filter((c) => c.name.includes('native') || c.name.includes('214'));
  for (const c of natives)
    for (const expected of [1, 2, 3, 4] as Tone[]) {
      if (expected === c.tone) continue;
      it(`${c.name} is not tone ${expected}`, () => {
        for (const calibrated of [true, false]) {
          const r = run(c.opts, expected, MALE, calibrated) as ToneSuccess;
          expect(r.ok).toBe(true);
          expect(r.isCorrect).toBe(false);
          expect(r.detectedTone).toBe(c.tone);
          expect(r.hint).toBeTruthy();
        }
      });
    }

  it('the Azure failure case: a clear tone 2 is never read as tone 4', () => {
    for (const [, range] of VOICES) {
      const r = run({ shape: [3, 2.7, 3.5, 5] }, 4, range, false) as ToneSuccess;
      expect(r.detectedTone).toBe(2);
      expect(r.toneScores[4]).toBeLessThan(0.1);
      expect(r.hint).toMatch(/rose/);
    }
  });
});

describe('timid attempts get coached, not passed', () => {
  it('a very small fall is not accepted as tone 4 once calibrated, and says to fall more', () => {
    for (const [, range] of VOICES) {
      const r = run({ shape: [4, 3.3, 2.8] }, 4, range, true) as ToneSuccess;
      expect(r.isCorrect).toBe(false);
      expect(r.hint).toMatch(/drop|fall/i);
    }
  });
});

describe('robustness', () => {
  it('survives octave errors from the pitch tracker', () => {
    const r = analyzeTone({
      frames: synth({
        shape: [3, 2.7, 3.5, 5],
        octaveGlitches: [
          { at: 12, factor: 2 },
          { at: 13, factor: 2 },
          { at: 30, factor: 0.5 },
        ],
      }),
      expectedTone: 2,
    }) as ToneSuccess;
    expect(r.isCorrect).toBe(true);
  });

  it('ignores the unvoiced consonant before the vowel', () => {
    const r = analyzeTone({ frames: synth({ shape: [5, 5, 5], consonantMs: 150 }), expectedTone: 1 });
    expect(r.ok && r.isCorrect).toBe(true);
  });

  it('returns a drawable contour on the Chao scale', () => {
    const r = analyzeTone({ frames: synth({ shape: [5, 1] }), expectedTone: 4, speakerRange: MALE }) as ToneSuccess;
    expect(r.contour).toHaveLength(20);
    expect(r.contour[0].x).toBe(0);
    expect(r.contour.at(-1)!.x).toBe(1);
    expect(r.contour[0].level).toBeGreaterThan(4);
    expect(r.contour.at(-1)!.level).toBeLessThan(2);
  });

  it('runs well under a millisecond per attempt', () => {
    const frames = synth({ shape: [2, 1, 1, 3.5] });
    const t0 = performance.now();
    for (let i = 0; i < 200; i++) analyzeTone({ frames, expectedTone: 3 });
    expect((performance.now() - t0) / 200).toBeLessThan(1);
  });
});

describe('recording problems', () => {
  const expectError = (opts: Partial<SynthOptions>, code: string, frames = synth({ shape: [5, 5], ...opts })) => {
    const r = analyzeTone({ frames, expectedTone: 1 });
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.error).toBe(code);
      expect(r.message.length).toBeGreaterThan(0);
    }
  };

  it('NO_VOICE when nobody speaks', () => expectError({ voicedMs: 0, consonantMs: 0 }, 'NO_VOICE'));
  it('TOO_NOISY when the room is loud', () => expectError({ noiseDb: -34, voiceDb: -28 }, 'TOO_NOISY'));
  it('TOO_QUIET when the voice barely clears a quiet floor', () => expectError({ noiseDb: -72, voiceDb: -63 }, 'TOO_QUIET'));
  it('CLIPPING when the input saturates', () => expectError({ peak: 1 }, 'CLIPPING'));
  it('TOO_SHORT for a clipped-off syllable', () => expectError({ voicedMs: 100 }, 'TOO_SHORT'));
  it('UNCLEAR when the pitch is erratic', () => expectError({ jitterSt: 3 }, 'UNCLEAR'));
});
