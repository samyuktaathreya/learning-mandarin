// Pure helpers for the pinyin progress screen. No React, no fetching.

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
    tone1: { character: '妈', pinyin: 'mā', numbered: 'ma 1', label: '1st' },
    tone2: { character: '麻', pinyin: 'má', numbered: 'ma 2', label: '2nd' },
    tone3: { character: '马', pinyin: 'mǎ', numbered: 'ma 3', label: '3rd' },
    tone4: { character: '骂', pinyin: 'mà', numbered: 'ma 4', label: '4th' },
    tone5: { character: '吗', pinyin: 'ma', numbered: 'ma 5', label: 'neutral' },
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

export const getPopupTitle = (item) => (item.isTone ? `${item.main} · ${item.sub} tone` : item.main);

export const formatStats = ({ attempts, successes, accuracy }) =>
    attempts
        ? `${successes} of ${attempts} correct (${Math.round(accuracy * 100)}%)`
        : 'Not practiced yet';