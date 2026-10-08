// Pure helpers for the pinyin progress screen. No React, no fetching.
import { parsePinyinSyllable } from '../tone';

// Fallback until /api/pinyin/progress returns `mastery_threshold`.
// Keep in sync with pinyin_services.MASTERY_THRESHOLD, or delete once the backend sends it.
export const DEFAULT_MASTERY_THRESHOLD = 0.8;

// Tones are shown on "ma" and played from these fixed characters,
// so no row lookup is needed for them.
// `numbered` is what the audio endpoint's forced-pronunciation synthesis
// expects: "<syllable> <tone digit>" with a space (e.g. 'ma 2'), matching
// the SAPI phoneme format. Assumes neutral tone is stored as tone 5 --
// adjust if the DB uses 0 instead.
export const TONES = {
    tone1: {
        character: '妈', pinyin: 'mā', numbered: 'ma 1', label: '1st',
        description: "Start high and hold the pitch steady and flat, like singing one sustained high note.",
    },
    tone2: {
        character: '麻', pinyin: 'má', numbered: 'ma 2', label: '2nd',
        description: "Start at a medium pitch and rise sharply to high, like the rising intonation of a surprised 'What?'.",
    },
    tone3: {
        character: '马', pinyin: 'mǎ', numbered: 'ma 3', label: '3rd',
        description: "A low, flat pitch when spoken normally in continuous speech ('half third tone'), rising at the end only when heavily emphasized or spoken completely in isolation ('full third tone')",
    },
    tone4: {
        character: '骂', pinyin: 'mà', numbered: 'ma 4', label: '4th',
        description: "Fourth tone, falling (pitch 51), as in mà (骂 scold): start high and drop sharply to low, like firmly saying 'No!' or giving a command.",
    },
    tone5: {
        character: '吗', pinyin: 'ma', numbered: 'ma 5', label: 'neutral',
        description: "Neutral tone, light and short with no fixed pitch, as in ma (吗 question particle): say it softly and quickly, letting its pitch follow on from the previous syllable's tone.",
    },
};

// Standard teaching order (the backend sorts alphabetically).
// Tags not listed here go at the end, alphabetically.
const INITIAL_ORDER = [
    'b', 'p', 'm', 'f', 'd', 't', 'n', 'l', 'g', 'k', 'h',
    'j', 'q', 'x', 'z', 'c', 's', 'zh', 'ch', 'sh', 'r', 'y', 'w',
];

const FINAL_ORDER = [
    'a', 'o', 'e', 'i', 'u', 'ü', 'v',
    'ai', 'ei', 'ao', 'ou', 'an', 'en', 'ang', 'eng', 'ong', 'er',
    'ia', 'ie', 'iao', 'iu', 'iou', 'ian', 'in', 'iang', 'ing', 'iong',
    'ua', 'uo', 'uai', 'ui', 'uei', 'uan', 'un', 'uen', 'uang', 'ueng',
    'üe', 've', 'üan', 'van', 'ün', 'vn',
];

const TONE_ORDER = Object.keys(TONES);

// `key` matches the field name in the /api/pinyin/progress response.
// `category` matches what /api/pinyin/get_row_by_tag expects ('initial' | 'final');
// tones have no category since they're looked up by fixed character, not by tag.
const SECTIONS = [
    { key: 'tones', title: 'Tones', subtitle: 'Each syllable has one of five tones', order: TONE_ORDER, category: null },
    { key: 'consonants', title: 'Initials', subtitle: 'Sounds that go at the beginning of a syllable', order: INITIAL_ORDER, category: 'initial' },
    { key: 'vowels', title: 'Finals', subtitle: 'Sounds that go at the end of a syllable', order: FINAL_ORDER, category: 'final' },
];

export const getToneInfo = (tag) => TONES[tag] ?? null;

export const getAccuracy = ({ attempts, successes }) => (attempts ? successes / attempts : 0);

const sortByOrder = (entries, order) => {
    const rank = (tag) => {
        const i = order.indexOf(tag);
        return i === -1 ? order.length : i;
    };
    return [...entries].sort((a, b) => rank(a.tag) - rank(b.tag) || a.tag.localeCompare(b.tag));
};

// Turns a raw { tag, attempts, successes } entry into everything a card needs.
// Mastery matches the backend: attempts > 0 and successes / attempts >= threshold.
// The bar shows progress toward that threshold; graduating fills every bar.
export const toCardItem = (entry, { threshold, graduated, category }) => {
    const tone = getToneInfo(entry.tag);
    const accuracy = getAccuracy(entry);
    const mastered = graduated || (entry.attempts > 0 && accuracy >= threshold);

    return {
        ...entry,
        category, // 'initial' | 'final' | null (tones)
        isTone: Boolean(tone),
        main: tone ? tone.pinyin : entry.tag,
        sub: tone ? tone.label : null,
        accuracy,
        mastered,
        percent: mastered ? 100 : Math.round(Math.min(accuracy / threshold, 1) * 100),
    };
};

// Response from /api/pinyin/progress -> sections ready to render.
// Empty sections (nothing unlocked yet) are dropped.
export const buildSections = (data) => {
    const threshold = data.mastery_threshold ?? DEFAULT_MASTERY_THRESHOLD;
    const graduated = Boolean(data.graduated);

    return SECTIONS
        .map(({ key, title, subtitle, order, category }) => ({
            key,
            title,
            subtitle,
            items: sortByOrder(data[key] ?? [], order).map((entry) => toCardItem(entry, { threshold, graduated, category })),
        }))
        .filter((section) => section.items.length > 0);
};

export const summarize = (sections) => {
    const items = sections.flatMap((s) => s.items);
    return { total: items.length, mastered: items.filter((i) => i.mastered).length };
};

// What's shown/played for a card. `row` is the /api/pinyin/get_row_by_tag
// response (unused for tones). `description` is optional until the backend
// adds it; the popup falls back when it's missing.
//
// numberedPinyin is "<syllable> <tone digit>" (a space between them, e.g.
// 'mu 3') -- that's the format the audio endpoint's forced-pronunciation
// SSML <phoneme alphabet="sapi"> tag actually expects. No space (e.g. 'mu3')
// gets rejected as an unknown phoneme.
export const getSoundSource = (item, row) => {
    // Whole syllable (from app.pinyinToSyllableItem): no row lookup, there's no
    // example character, so the syllable itself is what's shown and played.
    if (item.isSyllable) {
        return {
            character: item.main,
            displayPinyin: item.tag,
            // SAPI spells ü as v ('nv 3').
            numberedPinyin: `${item.syllable.replace(/ü/g, 'v')} ${item.tone}`,
            description: TONES[`tone${item.tone}`]?.description ?? null,
        };
    }
    const tone = getToneInfo(item.tag);
    if (tone) {
        return {
            character: tone.character,
            displayPinyin: tone.pinyin,
            numberedPinyin: tone.numbered,
            description: tone.description ?? null,
        };
    }
    if (!row) return null;
    return {
        character: row.character,
        displayPinyin: row.diacritic_pinyin,
        numberedPinyin: `${row.syllable} ${row.tone}`,
        description: row.description ?? null,
    };
};

// Turns a pinyin question's `tags` (consonant, vowel, tone -- any order, the
// consonant may be missing for zero-initial syllables) into popup items.
// These have no progress stats, so the popup is opened with showProgress={false}.
export const tagsToSoundItems = (tags = []) => {
    const items = [];
    for (const tag of tags) {
        let category = null;
        if (TONES[tag]) category = null;
        else if (INITIAL_ORDER.includes(tag)) category = 'initial';
        else if (FINAL_ORDER.includes(tag)) category = 'final';
        else continue; // not a pinyin sound tag (e.g. a grammar tag)

        const tone = getToneInfo(tag);
        items.push({
            tag,
            category,
            isTone: Boolean(tone),
            main: tone ? tone.pinyin : tag,
            sub: tone ? tone.label : null,
        });
    }
    const rank = (i) => (i.isTone ? 2 : i.category === 'final' ? 1 : 0);
    return items.sort((a, b) => rank(a) - rank(b));
};

const TONE_MARKS = { a: 'āáǎà', e: 'ēéěè', i: 'īíǐì', o: 'ōóǒò', u: 'ūúǔù', ü: 'ǖǘǚǜ' };

// ('ma', 4) -> 'mà'. Mark goes on a, else e, else the o of "ou", else the last vowel.
export const toDiacriticPinyin = (syllable, tone) => {
    if (tone < 1 || tone > 4) return syllable;
    const idx = syllable.includes('a') ? syllable.indexOf('a')
        : syllable.includes('e') ? syllable.indexOf('e')
        : syllable.includes('ou') ? syllable.indexOf('o')
        : syllable.search(/[iouü](?=[^iouü]*$)/);
    if (idx === -1) return syllable;
    return syllable.slice(0, idx) + TONE_MARKS[syllable[idx]][tone - 1] + syllable.slice(idx + 1);
};

// Turns one whole pinyin syllable as displayed in a question ("ma4", "mǎ",
// "ma5") into a popup item. Returns null if it isn't a single toned syllable,
// so callers can render it as plain text.
export const pinyinToSyllableItem = (text) => {
    if (typeof text !== 'string') return null;
    let parsed = parsePinyinSyllable(text);
    if (!parsed) {
        // parsePinyinSyllable rejects neutral tone (the tone checker can't grade it).
        const neutral = text.trim().toLowerCase().replace(/u:|v/g, 'ü').match(/^([a-zü]+)5$/);
        if (neutral) parsed = { syllable: neutral[1], tone: 5 };
    }
    if (!parsed || !/[aeiouü]/.test(parsed.syllable)) return null;

    const { syllable, tone } = parsed;
    return {
        tag: `${syllable}${tone}`,
        category: null,
        isTone: false,
        isSyllable: true,
        main: toDiacriticPinyin(syllable, tone),
        sub: TONES[`tone${tone}`].label,
        syllable,
        tone,
    };
};

export const getPopupTitle = (item) =>
    (item.isTone || item.isSyllable ? `${item.main} · ${item.sub} tone` : item.main);

export const formatStats = ({ attempts, successes, accuracy }) =>
    attempts
        ? `${successes} of ${attempts} correct (${Math.round(accuracy * 100)}%)`
        : 'Not practiced yet';