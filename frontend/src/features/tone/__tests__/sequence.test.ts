import { describe, expect, it } from 'vitest';
import { analyzeTones } from '../analysis/analyze';
import { LiveTracker } from '../analysis/live';
import { parsePinyinSyllables } from '../parsePinyin';
import type { SpeakerRange, SyllableTone, ToneSequenceSuccess } from '../types';
import { FEMALE, MALE, synthWord, type WordOptions } from './synth';

// native-ish Chao shapes, shortened the way they are inside a word
const SHAPE: Record<1 | 2 | 3 | 4, number[]> = {
  1: [5, 5, 5],
  2: [3, 2.7, 3.5, 5],
  3: [2, 1.2, 1, 3.5],
  4: [5, 3.5, 1],
};

const VOICES: [string, SpeakerRange][] = [
  ['male', MALE],
  ['female', FEMALE],
];

function run(tones: SyllableTone[], opts: Omit<WordOptions, 'range'>, range: SpeakerRange, calibrated: boolean) {
  return analyzeTones({
    frames: synthWord({ ...opts, range }),
    expectedTones: tones,
    speakerRange: calibrated ? range : null,
  });
}

describe('two-syllable words: every tone pair, every join', () => {
  const pairs = ([1, 2, 3, 4] as const).flatMap((a) => ([1, 2, 3, 4] as const).map((b) => [a, b] as const));
  for (const join of ['consonant', 'nasal', 'pause'] as const)
    for (const [a, b] of pairs)
      it(`${a}-${b} joined by ${join}`, () => {
        for (const [, range] of VOICES)
          for (const calibrated of [true, false])
            for (const seed of [1, 2]) {
              const r = run([a, b], { shapes: [SHAPE[a], SHAPE[b]], join, seed }, range, calibrated);
              expect(r.ok, JSON.stringify(r)).toBe(true);
              const s = r as ToneSequenceSuccess;
              const detected = s.syllables.map((x) => x?.detectedTone);
              expect(s.isCorrect, `${calibrated ? 'cal' : 'uncal'} seed ${seed}: got ${detected}`).toBe(true);
            }
      });
});

describe('two-syllable words: wrong tones are caught per syllable', () => {
  it('saying 4-4 for an expected 2-4 fails only the first syllable', () => {
    for (const [, range] of VOICES) {
      const s = run([2, 4], { shapes: [SHAPE[4], SHAPE[4]] }, range, true) as ToneSequenceSuccess;
      expect(s.ok).toBe(true);
      expect(s.isCorrect).toBe(false);
      expect(s.syllables[0]!.isCorrect).toBe(false);
      expect(s.syllables[0]!.detectedTone).toBe(4);
      expect(s.syllables[0]!.hint).toBeTruthy();
      expect(s.syllables[1]!.isCorrect).toBe(true);
    }
  });

  it('swapped tones (1-4 said as 4-1) fail both', () => {
    const s = run([1, 4], { shapes: [SHAPE[4], SHAPE[1]] }, MALE, true) as ToneSequenceSuccess;
    expect(s.syllables.map((x) => x!.isCorrect)).toEqual([false, false]);
  });
});

describe('two-syllable words: neutral tone and creak', () => {
  it('a neutral second syllable is split off but not graded', () => {
    const s = run([4, 5], { shapes: [SHAPE[4], [2, 1.8]], voicedMs: [280, 150] }, MALE, true) as ToneSequenceSuccess;
    expect(s.ok).toBe(true);
    expect(s.syllables[1]).toBeNull();
    expect(s.syllables[0]!.isCorrect).toBe(true);
    expect(s.isCorrect).toBe(true);
  });

  it('a creaky half-third first syllable still counts as tone 3', () => {
    const s = run([3, 1], { shapes: [[2.2, 1.4, 1.1, 1], SHAPE[1]], creakTailMs: { 0: 80 } }, MALE, true) as ToneSequenceSuccess;
    expect(s.ok, JSON.stringify(s)).toBe(true);
    expect(s.syllables.map((x) => x!.isCorrect)).toEqual([true, true]);
  });
});

describe('two-syllable words: recording problems', () => {
  it('MISSING_SYLLABLE when only one short syllable was said', () => {
    const r = run([1, 4], { shapes: [SHAPE[1]], voicedMs: 200 }, MALE, true);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toBe('MISSING_SYLLABLE');
  });

  it('names the syllable that could not be graded', () => {
    const r = run([1, 4], { shapes: [SHAPE[1], SHAPE[4]], voicedMs: [300, 60], join: 'pause' }, MALE, true);
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.syllableIndex).toBe(1);
      expect(r.message).toMatch(/^Second syllable:/);
    }
  });
});

describe('two-syllable words: the uncalibrated graph keeps relative height', () => {
  it('1-3: the first syllable is drawn above the second', () => {
    const s = run([1, 3], { shapes: [SHAPE[1], SHAPE[3]] }, MALE, false) as ToneSequenceSuccess;
    const mean = (i: number) => s.syllables[i]!.contour.reduce((a, p) => a + p.level, 0) / 20;
    expect(mean(0)).toBeGreaterThan(mean(1) + 1);
  });
});

describe('LiveTracker with several syllables', () => {
  it('a pause between syllables does not end the take', () => {
    const frames = synthWord({ shapes: [SHAPE[1], SHAPE[4]], join: 'pause', joinMs: 450, trailingSilenceMs: 2000 });
    const tr = new LiveTracker(1, MALE, undefined, 2);
    for (const f of frames) if (tr.push(f) && tr.phase === 'finished') break;
    expect(tr.finishReason).toBe('silence');
    const r = analyzeTones({ frames: tr.frames, expectedTones: [1, 4], speakerRange: MALE });
    expect(r.ok && r.isCorrect).toBe(true);
  });
});

describe('parsePinyinSyllables', () => {
  it('reads numbered, marked and neutral syllables', () => {
    expect(parsePinyinSyllables('cao3 mei2')).toEqual([{ syllable: 'cao', tone: 3 }, { syllable: 'mei', tone: 2 }]);
    expect(parsePinyinSyllables('cǎo méi')).toEqual([{ syllable: 'cao', tone: 3 }, { syllable: 'mei', tone: 2 }]);
    expect(parsePinyinSyllables('xie4 xie5')).toEqual([{ syllable: 'xie', tone: 4 }, { syllable: 'xie', tone: 5 }]);
    expect(parsePinyinSyllables('xiè xie')).toEqual([{ syllable: 'xie', tone: 4 }, { syllable: 'xie', tone: 5 }]);
    expect(parsePinyinSyllables('lv4 se4')).toEqual([{ syllable: 'lü', tone: 4 }, { syllable: 'se', tone: 4 }]);
  });
  it('rejects junk and all-neutral input', () => {
    expect(parsePinyinSyllables('')).toBeNull();
    expect(parsePinyinSyllables('ma5 ma5')).toBeNull();
    expect(parsePinyinSyllables('ma3 123')).toBeNull();
  });
});
