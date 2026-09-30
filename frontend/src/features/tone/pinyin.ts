import type { Tone } from './types';

const MARKS: Record<string, string[]> = {
  a: ['ā', 'á', 'ǎ', 'à'],
  e: ['ē', 'é', 'ě', 'è'],
  i: ['ī', 'í', 'ǐ', 'ì'],
  o: ['ō', 'ó', 'ǒ', 'ò'],
  u: ['ū', 'ú', 'ǔ', 'ù'],
  ü: ['ǖ', 'ǘ', 'ǚ', 'ǜ'],
};

/**
 * "ma" + 3 → "mǎ". Standard placement: a or e takes the mark; in "ou" the o
 * does; otherwise the last vowel. Accepts "v" or "u:" for ü.
 */
export function withToneMark(syllable: string, tone: Tone): string {
  const s = syllable.toLowerCase().replace(/u:|v/g, 'ü');
  let idx = s.search(/[ae]/);
  if (idx === -1) idx = s.indexOf('ou');
  if (idx === -1) {
    for (let i = s.length - 1; i >= 0; i--)
      if ('iouü'.includes(s[i])) {
        idx = i;
        break;
      }
  }
  if (idx === -1) return s;
  return s.slice(0, idx) + MARKS[s[idx]][tone - 1] + s.slice(idx + 1);
}

export const TONE_NAMES: Record<Tone, string> = {
  1: 'Tone 1 · high and level',
  2: 'Tone 2 · rising',
  3: 'Tone 3 · low dip',
  4: 'Tone 4 · falling',
};
