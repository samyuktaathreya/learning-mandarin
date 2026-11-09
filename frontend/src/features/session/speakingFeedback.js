/*
 * speakingFeedback.js (practice feature)
 *
 * Turns Azure's raw result into what SpeakingQuestion shows. This is feedback
 * only: speaking never changes the score. Any attempt the user finishes is
 * logged as correct.
 *
 * The result has the same shape /api/transcribe used to return, so the
 * existing UI keeps working:
 *   { mode, transcription, transcription_pinyin, expected_pinyin, is_correct,
 *     error?, hallucination?,
 *     assessment only: accuracy, accuracy_threshold, accuracy_ok, tone_ok,
 *                      feedback, phonemes, weakest_phoneme }
 * is_correct means "sounded right". It drives the message and, for sentences,
 * whether shadowing is offered.
 */
import { PINYIN_QUESTION_TYPES } from './questionHelpers';

const ACCURACY_THRESHOLD = 90;
const PINYIN_ACCURACY_THRESHOLD = 80;
// Share of the sentence's syllables that must be heard, in order, for a
// sentence to count as "sounded right".
const SENTENCE_MATCH = 0.8;

let pinyinPromise = null;

// pinyin-pro's dictionary is large, so it loads when speaking practice starts.
export function loadPinyin() {
    pinyinPromise ??= import('pinyin-pro').catch((err) => {
        pinyinPromise = null;
        throw err;
    });
    return pinyinPromise;
}

const CJK = /[㐀-鿿]/;
const stripPunct = (s = '') => s.replace(/[^\p{L}\p{N}]/gu, '');

// The characters Azure scores against.
export function referenceTextFor(q) {
    if (q?.hanzi) return q.hanzi;
    return CJK.test(q?.question ?? '') ? q.question : '';
}

export function speechModeFor(q) {
    if (q?.question_type === 'speaking sentence') return 'transcription';
    return stripPunct(referenceTextFor(q)) ? 'assessment' : 'transcription';
}

// ---------------------------------------------------------------- pinyin helpers

// One spelling everywhere: lowercase, ü as v, neutral tone as 5.
function normSyllable(s) {
    let x = s.toLowerCase().replace(/u:|ü/g, 'v');
    if (x.endsWith('0')) x = `${x.slice(0, -1)}5`;
    else if (!/[1-5]$/.test(x)) x += '5';
    return x;
}

const toNumbered = (pinyin, text, options = {}) =>
    pinyin(text, { toneType: 'num', type: 'array', nonZh: 'removed', v: true, ...options })
        .filter((s) => /[a-zü]/i.test(s))
        .map(normSyllable);

// 'lao3shi1' -> ['lao3', 'shi1']
const splitSyllables = (answer = '') =>
    (answer.toLowerCase().match(/[a-züv:]+[0-5]?/g) ?? []).map(normSyllable);

// Every reading of one character: 行 -> ['xing2', 'hang2', 'hang4', 'heng2']
const allReadings = (pinyin, ch) =>
    pinyin(ch, { multiple: true, type: 'array', toneType: 'num', v: true }).map(normSyllable);

// A neutral-tone target matches any tone of the same syllable, since
// dictionaries rarely list neutral readings (系 in 没关系).
const readingMatches = (readings, target) =>
    readings.includes(target)
    || (target.endsWith('5') && readings.some((r) => r.slice(0, -1) === target.slice(0, -1)));

// Display only: two 3rd tones in a row, the first is said as a 2nd (ni3hao3 -> ni2hao3).
function thirdToneSandhi(syllables) {
    return syllables.map((s, i) =>
        (s.endsWith('3') && syllables[i + 1]?.endsWith('3') ? `${s.slice(0, -1)}2` : s));
}

function lcsLength(a, b) {
    const row = new Array(b.length + 1).fill(0);
    for (let i = 1; i <= a.length; i++) {
        let diag = 0;
        for (let j = 1; j <= b.length; j++) {
            const up = row[j];
            row[j] = a[i - 1] === b[j - 1] ? diag + 1 : Math.max(row[j], row[j - 1]);
            diag = up;
        }
    }
    return row[b.length];
}

// ---------------------------------------------------------------- results

export async function buildSpeakingResult(raw, q) {
    const mode = speechModeFor(q);
    const base = {
        mode,
        transcription: '',
        transcription_pinyin: '',
        expected_pinyin: (q.answer ?? '').toLowerCase().replace(/[\s,]/g, ''),
        is_correct: false,
    };
    if (raw.kind === 'timeout') return { ...base, error: 'timeout' };
    if (raw.kind === 'error') return { ...base, error: raw.message || 'error' };
    if (raw.kind !== 'ok' || !stripPunct(raw.text)) return { ...base, hallucination: true };

    const { pinyin } = await loadPinyin();
    return mode === 'assessment'
        ? assessmentResult(pinyin, raw, q, base)
        : transcriptionResult(pinyin, raw, q, base);
}

// "Tone ok" if each character Azure heard has a reading that matches the
// target syllable. Azure leans toward the reference text, so a wrong tone can
// still come back as the target character and pass; that's accepted for now.
function tonesPlausible(pinyin, recognized, reference, answer) {
    if (recognized === reference) return true;
    const heard = [...recognized];
    if (heard.length !== [...reference].length) return false;
    const citation = toNumbered(pinyin, reference, { toneSandhi: false }); // 一/不 without sandhi
    const display = splitSyllables(answer);                                  // with sandhi (ni2hao3)
    return heard.every((ch, i) => {
        const readings = allReadings(pinyin, ch);
        return [citation[i], display[i]].some((target) => target && readingMatches(readings, target));
    });
}

function assessmentResult(pinyin, raw, q, base) {
    const recognized = stripPunct(raw.text);
    const reference = stripPunct(referenceTextFor(q));
    const accuracy = Math.round(raw.accuracy ?? 0);
    const threshold = PINYIN_QUESTION_TYPES.has(q.question_type) ? PINYIN_ACCURACY_THRESHOLD : ACCURACY_THRESHOLD;
    const accuracy_ok = accuracy >= threshold;
    const tone_ok = tonesPlausible(pinyin, recognized, reference, q.answer);
    const is_correct = accuracy_ok && tone_ok;

    let feedback = 'correct';
    if (!accuracy_ok && !tone_ok) feedback = 'sound_and_tone';
    else if (!accuracy_ok) feedback = 'sound';
    else if (!tone_ok) feedback = 'tone';

    const phonemes = (raw.phonemes ?? []).map((p) => ({ phoneme: p.phoneme, accuracy: Math.round(p.accuracy ?? 0) }));
    const weakest_phoneme = phonemes.length
        ? phonemes.reduce((worst, p) => (p.accuracy < worst.accuracy ? p : worst))
        : null;

    return {
        ...base,
        transcription: recognized,
        transcription_pinyin: thirdToneSandhi(toNumbered(pinyin, recognized)).join(''),
        is_correct,
        accuracy,
        accuracy_threshold: threshold,
        accuracy_ok,
        tone_ok,
        feedback,
        phonemes,
        weakest_phoneme,
    };
}

function transcriptionResult(pinyin, raw, q, base) {
    // Compare the spoken form (三个), not the display form (3个).
    const spoken = stripPunct(raw.lexical || raw.text);
    const reference = stripPunct(referenceTextFor(q));

    // Far more text than the sentence: Azure picked up something else.
    if (reference && spoken.length > reference.length * 3) {
        return { ...base, transcription: raw.text, hallucination: true };
    }

    const heard = toNumbered(pinyin, spoken);
    const target = reference ? toNumbered(pinyin, reference) : splitSyllables(q.answer);
    const match = target.length ? lcsLength(heard, target) / target.length : 0;

    return {
        ...base,
        transcription: raw.text,
        transcription_pinyin: heard.join(' '),
        is_correct: match >= SENTENCE_MATCH,
        match: Math.round(match * 100),
    };
}