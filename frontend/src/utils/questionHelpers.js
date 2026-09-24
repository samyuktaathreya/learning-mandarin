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

export const isSpeakingQuestion = (qt) => qt === "speaking vocab" || qt === "speaking sentence";
export const hasChinese = (str) => /[一-鿿]/.test(str);
export const isListeningType = (qt) => qt === "listening vocab" || qt === "listening sentence";

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
    answer.replace(/[^a-z0-9一-鿿]/gi, '').length <= 2;

// Local exact-match check against the comma-separated accepted answers.
export const isExactMatch = (userAnswer, questionObj) => {
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