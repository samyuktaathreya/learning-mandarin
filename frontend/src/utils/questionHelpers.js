// Pure helpers for question types, text normalization and answer matching.

export const clean = (str) => {
    return str
        .toLowerCase()
        .replace(/[.,\/#!$%\^&\*;:{}=\-_`~()。？！、，：；""'']/g, "")
        .replace(/([一-鿿])\s+([一-鿿])/g, "$1$2")
        .replace(/\bim\b/g, "i am")
        .replace(/\byoure\b/g, "you are")
        .replace(/\bhes\b/g, "he is")
        .replace(/\bshes\b/g, "she is")
        .replace(/\ba\b|\ban\b|\bthe\b/g, "")
        .replace(/\s+/g, " ")
        .trim();
};

// Two-syllable pinyin questions ("cao3 mei2"), aimed at one tone pair.
export const PINYIN_PAIR_QUESTION_TYPES = new Set(["listening_pinyin_pair", "speaking_pinyin_pair"]);
export const PINYIN_QUESTION_TYPES = new Set(["listening_pinyin", "speaking_pinyin", ...PINYIN_PAIR_QUESTION_TYPES]);
// Pinyin questions whose answer is typed pinyin, graded by gradePinyinAnswer.
export const LISTENING_PINYIN_TYPES = new Set(["listening_pinyin", "listening_pinyin_pair"]);

export const isSpeakingQuestion = (qt) =>
    qt === "speaking vocab" || qt === "speaking sentence" || qt === "speaking_pinyin" || qt === "speaking_pinyin_pair";
export const hasChinese = (str) => /[一-鿿]/.test(str);
export const isListeningType = (qt) =>
    qt === "listening vocab" || qt === "listening sentence" || LISTENING_PINYIN_TYPES.has(qt);

export const TRANSLATE_TO_ENGLISH_TYPES = new Set([
    "translate chinese word to english",
    "translate chinese sentence to english",
]);

export const GRADE_ENGLISH_TO_CHINESE_TYPES = new Set([
    "listening sentence",
    "translate english sentence to chinese",
    "translate english word to chinese",
]);

export const CHARACTER_QUIZ_TYPES = new Set([
    "character_spot_difference",
    "character_pinyin_to_char",
    "radical_meaning",
]);

// Question types whose answers are pinyin, where spaces between syllables don't matter.
const PINYIN_TYPES = new Set([
    "listening vocab",
    "transcribe word to pinyin",
    "transcribe hanzi to pinyin",
]);

// True if the answer is at most two letters/characters long (e.g. a single syllable).
export const isSingleSyllableAnswer = (answer) =>
    answer.replace(/[^a-z0-9一-鿿]/gi, '').length <= 1;

const MARKED_VOWELS = {
    ā: ['a', 1], á: ['a', 2], ǎ: ['a', 3], à: ['a', 4],
    ē: ['e', 1], é: ['e', 2], ě: ['e', 3], è: ['e', 4],
    ī: ['i', 1], í: ['i', 2], ǐ: ['i', 3], ì: ['i', 4],
    ō: ['o', 1], ó: ['o', 2], ǒ: ['o', 3], ò: ['o', 4],
    ū: ['u', 1], ú: ['u', 2], ǔ: ['u', 3], ù: ['u', 4],
    ǖ: ['ü', 1], ǘ: ['ü', 2], ǚ: ['ü', 3], ǜ: ['ü', 4],
};

// "cǎo méi", "cao3mei2" and "CAO3 MEI2" all -> { letters: "caomei", tones: "32" }.
// Comparing letters and tone order separately means tone marks, tone numbers
// and spacing can be mixed freely, with no need to split syllables. Neutral
// tone (5 / 0 / unmarked) adds no tone digit.
const pinyinSignature = (str) => {
    let letters = '';
    let tones = '';
    for (const ch of str.normalize('NFC').toLowerCase().replace(/u:|v/g, 'ü')) {
        if (MARKED_VOWELS[ch]) {
            letters += MARKED_VOWELS[ch][0];
            tones += MARKED_VOWELS[ch][1];
        } else if (/[1-4]/.test(ch)) tones += ch;
        else if (/[a-zü]/.test(ch)) letters += ch;
    }
    return { letters, tones };
};

const sameSignature = (a, b) => a.letters === b.letters && a.tones === b.tones;

// Grades a typed pinyin answer for listening_pinyin(_pair). Accepts tone marks
// or numbers, with or without spaces, against every accepted answer (a sandhi
// word also accepts the 2-3 that was actually heard).
// Returns { isCorrect, syllableCorrect }: syllableCorrect has one boolean per
// syllable of a pair (the backend credits each syllable's sounds separately),
// or null when the answer can't be lined up with the syllables.
export const gradePinyinAnswer = (userAnswer, questionObj) => {
    const accepted = questionObj.accepted_answers?.length
        ? questionObj.accepted_answers
        : questionObj.answer.split(',');
    const given = pinyinSignature(userAnswer);
    const isCorrect = accepted.some((a) => sameSignature(pinyinSignature(a), given));

    const expectedParts = accepted[0].trim().split(/\s+/);
    if (expectedParts.length < 2) return { isCorrect, syllableCorrect: null };

    // line the answer up with the syllables: by its own spaces if it has the
    // right number of parts, else by tone order when the letters match exactly
    const givenParts = userAnswer.trim().split(/\s+/);
    const perSyllable = (expected) => {
        const parts = expected.trim().split(/\s+/).map(pinyinSignature);
        if (givenParts.length === parts.length) {
            return parts.map((p, j) => sameSignature(p, pinyinSignature(givenParts[j])));
        }
        const letters = parts.map((p) => p.letters).join('');
        if (given.letters === letters && given.tones.length === parts.length) {
            return parts.map((p, j) => p.tones === given.tones[j]);
        }
        return null;
    };

    // a syllable counts as right if it matches that syllable of ANY accepted answer
    const results = accepted.map(perSyllable).filter(Boolean);
    if (!results.length) return { isCorrect, syllableCorrect: isCorrect ? expectedParts.map(() => true) : null };
    const syllableCorrect = expectedParts.map((_, j) => results.some((r) => r[j]));
    return { isCorrect, syllableCorrect };
};

// Local exact-match check against the comma-separated accepted answers.
export const isExactMatch = (userAnswer, questionObj) => {
    if (LISTENING_PINYIN_TYPES.has(questionObj.question_type)) return gradePinyinAnswer(userAnswer, questionObj).isCorrect;
    const isPinyin = PINYIN_TYPES.has(questionObj.question_type);
    const normalize = (str) => {
        const cleaned = clean(str.trim());
        return isPinyin ? cleaned.replace(/\s+/g, "") : cleaned;
    };
    const given = normalize(userAnswer);
    return questionObj.answer.split(',').some(v => normalize(v) === given);
};

// Decides what to do with a question's audio when it first appears:
// 'autoplay', 'preload' (review sessions play it after answering), or 'none'.
export const getQuestionAudioMode = (questionObj, sessionType) => {
    const { question, question_type } = questionObj;

    const eligible =
        question_type !== "fill in the blank" &&
        !isSpeakingQuestion(question_type) &&
        !CHARACTER_QUIZ_TYPES.has(question_type);
    if (!eligible) return 'none';

    const isListening = isListeningType(question_type);
    const isReview = sessionType === "review_session";

    if ((hasChinese(question) || isListening) && (!isReview || isListening)) return 'autoplay';
    if (hasChinese(question)) return 'preload';
    return 'none';
};