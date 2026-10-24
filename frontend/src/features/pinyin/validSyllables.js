// The set of real Mandarin syllables, bundled from src/valid_pinyin_syllables_list.txt.
// The list spells ü as "v" ("lv", "nve") and has no tones.
import listText from '../../valid_pinyin_syllables_list.txt?raw';
import { parsePinyinSyllables } from './pinyinHelpers';

const VALID_SYLLABLES = new Set(
    listText.split(/\s+/).map((s) => s.trim().toLowerCase()).filter(Boolean)
);

// 'lü' -> true if it's in the list. Tone is ignored.
export const isValidSyllable = (syllable) =>
    VALID_SYLLABLES.has(syllable.replace(/ü/g, 'v'));

// The syllables in `text` that aren't real Mandarin, as the learner typed them
// ("be1 ma3" -> ["be1"]). Empty if everything is valid or the text doesn't
// parse as pinyin at all.
export const findInvalidSyllables = (text) => {
    if (typeof text !== 'string') return [];
    const parsed = parsePinyinSyllables(text);
    if (!parsed) return [];
    const typed = text.trim().split(/[\s.,!?;:。，！？]+/).filter(Boolean);
    return parsed
        .map((p, i) => (isValidSyllable(p.syllable) ? null : (typed[i] ?? p.syllable)))
        .filter(Boolean);
};
