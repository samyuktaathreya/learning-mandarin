// Turns a single pinyin syllable into { syllable, tone } for the tone checker.
// Accepts tone marks ("mǎ") or tone numbers ("ma3"); "v" or "u:" work for ü.
// Returns null for anything the checker can't grade: neutral tone, no tone,
// or more than one syllable ("nǐhǎo", "ni3 hao3").

const MARKED = {
  ā: ['a', 1], á: ['a', 2], ǎ: ['a', 3], à: ['a', 4],
  ē: ['e', 1], é: ['e', 2], ě: ['e', 3], è: ['e', 4],
  ī: ['i', 1], í: ['i', 2], ǐ: ['i', 3], ì: ['i', 4],
  ō: ['o', 1], ó: ['o', 2], ǒ: ['o', 3], ò: ['o', 4],
  ū: ['u', 1], ú: ['u', 2], ǔ: ['u', 3], ù: ['u', 4],
  ǖ: ['ü', 1], ǘ: ['ü', 2], ǚ: ['ü', 3], ǜ: ['ü', 4],
};

export function parsePinyinSyllable(input) {
  if (typeof input !== 'string') return null;
  const s = input
    .normalize('NFC')
    .trim()
    .toLowerCase()
    .replace(/[.,!?;:。，！？]+$/, '')
    .replace(/u:|v/g, 'ü');
  if (!s || /\s/.test(s)) return null;

  // tone number: "ma3"
  const numbered = s.match(/^([a-zü]+)([1-5])$/);
  if (numbered) {
    const tone = Number(numbered[2]);
    return tone <= 4 ? { syllable: numbered[1], tone } : null;
  }

  // tone mark: exactly one marked vowel
  let tone = null;
  let syllable = '';
  for (const ch of s) {
    if (MARKED[ch]) {
      if (tone !== null) return null; // two marks = two syllables
      syllable += MARKED[ch][0];
      tone = MARKED[ch][1];
    } else if (/[a-zü]/.test(ch)) {
      syllable += ch;
    } else {
      return null;
    }
  }
  return tone === null ? null : { syllable, tone };
}
// Turns space-separated pinyin ("cao3 mei2", "cǎo méi", "xie4 xie5") into
// [{ syllable, tone }, ...] for the tone checker. Unlike parsePinyinSyllable,
// neutral tone is kept as tone 5 ("xie5", or an unmarked "xie" after a toned
// syllable): the checker splits it off but doesn't grade it. Returns null if any
// part isn't a syllable, or if every syllable is neutral.
export function parsePinyinSyllables(input) {
  if (typeof input !== 'string') return null;
  const parts = input
    .normalize('NFC')
    .trim()
    .toLowerCase()
    .replace(/[.,!?;:。，！？]+/g, ' ')
    .replace(/u:|v/g, 'ü')
    .split(/\s+/)
    .filter(Boolean);
  if (!parts.length) return null;

  const out = [];
  for (const part of parts) {
    const neutral = part.match(/^([a-zü]+)[05]?$/);
    const parsed = parsePinyinSyllable(part) ?? (neutral && /[aeiouü]/.test(neutral[1]) ? { syllable: neutral[1], tone: 5 } : null);
    if (!parsed) return null;
    out.push(parsed);
  }
  return out.some((p) => p.tone !== 5) ? out : null;
}
