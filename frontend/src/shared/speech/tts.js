/*
 * shared/speech/tts.js
 *
 * Text-to-speech straight from Azure in the browser, so question audio no
 * longer waits on our server (TTS + ffmpeg on every request). It uses the same
 * voices, speeds, tone contours and forced pronunciation the server did.
 *
 *   synthesize({ text, slow, pinyin }) -> Promise<string>, a blob URL of an mp3
 *     pinyin: optional numbered pinyin ('ma3', 'ma 3'); forces that exact reading
 *
 * Azure's free tier allows 20 TTS requests per 60 seconds. This keeps its own
 * count and stops a little short of that, rejecting with TtsUnavailableError
 * so the caller can fall back to the server instead.
 */
import { loadSpeechSdk, getSpeechToken } from './azure';

const VOICES = [
    'zh-CN-XiaoxiaoNeural',
    'zh-CN-YunxiNeural',
    'zh-CN-XiaoyiNeural',
    'zh-CN-YunyangNeural',
];
// Voices that sound wrong on a given tone (forced pronunciation only).
const BAD_VOICES_BY_TONE = { 3: new Set(['zh-CN-XiaoyiNeural']) };

const TONE_CONTOURS = {
    1: '(0%,+15%) (100%,+15%)',
    2: '(0%,-10%) (100%,+30%)',
    3: '(0%,-5%) (35%,-35%) (75%,-35%) (100%,+10%)',
    4: '(0%,+35%) (55%,-25%) (100%,-40%)',
};
// Slower audio smooths pitch changes, so exaggerate the 4th tone a bit.
const SLOW_TONE_CONTOURS = { ...TONE_CONTOURS, 4: '(0%,+45%) (40%,-30%) (100%,-45%)' };

const RATE_LIMIT = 18;      // requests per window; Azure's free tier allows 20
const WINDOW_MS = 60_000;
const REQUEST_TIMEOUT_MS = 8000;

export class TtsUnavailableError extends Error {}
export class NonChineseTextError extends Error {}

// ---------------------------------------------------------------- text

const CJK = /[㐀-䶿一-鿿豈-﫿\u{20000}-\u{2ffff}]/u;
const LATIN = /[A-Za-zÀ-ɏ]+/g;
// A bracketed aside with Latin in it, e.g. the gloss in "老师___！ (Goodbye, teacher!)"
const LATIN_ASIDE = /[(（][^()（）]*[A-Za-zÀ-ɏ][^()（）]*[)）]/g;

// Never read English or pinyin out loud.
function chineseOnly(text) {
    const cleaned = text.replace(LATIN_ASIDE, '').replace(LATIN, '').split(/\s+/).filter(Boolean).join(' ');
    if (!CJK.test(cleaned)) throw new NonChineseTextError(`No Chinese to speak in ${JSON.stringify(text)}`);
    return cleaned;
}

const stripPunct = (s) => s.replace(/[^\p{L}\p{N}]/gu, '');

// 'ma3' -> 'ma 3', 'ni3hao3' -> 'ni 3 hao 3' (the format Azure's sapi phonemes want)
const normalizeSapiPinyin = (pinyin) =>
    pinyin.trim().toLowerCase().replace(/([a-zü]+)\s*([0-5])/g, '$1 $2 ').split(/\s+/).filter(Boolean).join(' ');

const xmlEscape = (s) => s.replace(/[<>&'"]/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', "'": '&apos;', '"': '&quot;' })[c]);

// Stable per item, so normal and slow of the same word share a voice.
function pickVoice(seed, pinyin) {
    let hash = 0x811c9dc5; // FNV-1a
    for (const ch of seed) {
        hash ^= ch.codePointAt(0);
        hash = Math.imul(hash, 0x01000193) >>> 0;
    }
    const bad = pinyin ? BAD_VOICES_BY_TONE[pinyin.split(' ').at(-1)] : null;
    const pool = bad ? VOICES.filter((v) => !bad.has(v)) : VOICES;
    return pool[hash % pool.length];
}

function rateFor(text, slow) {
    if (!slow) return '+0%';
    // A single syllable is over so fast it needs the slowest setting to sound slower.
    return stripPunct(text).length <= 1 ? '-50%' : '-30%';
}

function contourFor(pinyin, slow) {
    const parts = pinyin.split(' ');
    if (parts.length !== 2) return ''; // single syllables only
    const contour = (slow ? SLOW_TONE_CONTOURS : TONE_CONTOURS)[parts[1]];
    return contour ? ` contour="${contour}"` : '';
}

function buildSsml(text, slow, pinyin) {
    const voice = pickVoice(pinyin || text, pinyin);
    const spoken = pinyin
        ? `<phoneme alphabet="sapi" ph="${pinyin}">${xmlEscape(text)}</phoneme>`
        : xmlEscape(text);
    const contour = pinyin ? contourFor(pinyin, slow) : '';
    // Leading-exact 0ms: no ~0.2 s of silence at the start (the server trimmed it with ffmpeg).
    return `<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="zh-CN">`
        + `<voice name="${voice}"><mstts:silence type="Leading-exact" value="0ms"/>`
        + `<prosody rate="${rateFor(text, slow)}"${contour}>${spoken}</prosody>`
        + `</voice></speak>`;
}

// ---------------------------------------------------------------- Azure

let synth = null;              // one synthesizer, reused so its connection stays open
let queue = Promise.resolve(); // one request at a time on it
const sentAt = [];             // when recent requests went out, for the rate limit

async function getSynth() {
    const [sdk, token] = await Promise.all([loadSpeechSdk(), getSpeechToken()]);
    if (!synth) {
        const config = sdk.SpeechConfig.fromAuthorizationToken(token.value, token.region);
        config.speechSynthesisOutputFormat = sdk.SpeechSynthesisOutputFormat.Audio24Khz48KBitRateMonoMp3;
        synth = new sdk.SpeechSynthesizer(config, null); // null: hand back the audio, don't play it
    } else {
        synth.authorizationToken = token.value; // the cached token, or a fresh one if it expired
    }
    return { sdk, synth };
}

function resetSynth() {
    try { synth?.close(); } catch { /* already closed */ }
    synth = null;
}

function speak(sdk, s, ssml) {
    const request = new Promise((resolve, reject) => {
        s.speakSsmlAsync(
            ssml,
            (result) => (result.reason === sdk.ResultReason.SynthesizingAudioCompleted
                ? resolve(result.audioData)
                : reject(new TtsUnavailableError(result.errorDetails || 'Synthesis canceled'))),
            (err) => reject(new TtsUnavailableError(String(err))),
        );
    });
    const timeout = new Promise((_, reject) =>
        setTimeout(() => reject(new TtsUnavailableError('Synthesis timed out')), REQUEST_TIMEOUT_MS));
    return Promise.race([request, timeout]);
}

function pruneSent() {
    while (sentAt.length && Date.now() - sentAt[0] > WINDOW_MS) sentAt.shift();
}

function underRateLimit() {
    pruneSent();
    return sentAt.length < RATE_LIMIT;
}

// How many more requests fit in the current minute, and how long until the
// next one frees up (0 if one is free now). For pacing background preloads.
export function ttsRequestsLeft() {
    pruneSent();
    return RATE_LIMIT - sentAt.length;
}

export function msUntilNextTtsSlot() {
    pruneSent();
    return sentAt.length < RATE_LIMIT ? 0 : WINDOW_MS - (Date.now() - sentAt[0]);
}

export async function synthesize({ text, slow = false, pinyin = null }) {
    const spoken = chineseOnly(text);
    const ph = pinyin ? normalizeSapiPinyin(pinyin) : null;

    const run = async () => {
        if (!underRateLimit()) throw new TtsUnavailableError('Over the free tier rate limit');
        sentAt.push(Date.now());
        const startedAt = performance.now();
        const { sdk, synth: s } = await getSynth();
        let audio;
        try {
            audio = await speak(sdk, s, buildSsml(spoken, slow, ph));
        } catch (err) {
            resetSynth(); // start clean next time
            throw err;
        }
        console.info(`[tts] ${spoken}${slow ? ' (slow)' : ''}: ${Math.round(performance.now() - startedAt)} ms`);
        return URL.createObjectURL(new Blob([audio], { type: 'audio/mpeg' }));
    };

    const result = queue.then(run, run);
    queue = result.catch(() => {});
    return result;
}