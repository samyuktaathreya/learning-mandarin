/*
 * shared/speech/azure.js
 *
 * Azure speech recognition straight from the browser. Audio never touches
 * our server: the server only hands out a short-lived token (/api/speech/token).
 *
 * Everything slow happens before the user taps Record:
 *   prepareRecognition({ mode, referenceText })  loads the SDK, gets a token and
 *                                                opens the connection to Azure
 *     -> { begin, dispose }
 *   begin()   starts a take -> { write(int16Chunk), end(), cancel() }
 *     write   send ~100 ms of 16 kHz audio as it's recorded
 *     end     no more audio; resolves to the raw result:
 *               { kind: 'ok', text, lexical?, accuracy?, phonemes? }
 *               { kind: 'no_speech' } | { kind: 'timeout' } | { kind: 'error', message }
 *     cancel  drop the take (e.g. the mic closed mid-take)
 *
 * mode 'assessment':    pronunciation assessment against referenceText (words)
 * mode 'transcription': plain transcription, any length (sentences)
 *
 * A prepared recognition is good for one take; prepare another for a retry.
 */
import { API_BASE_URL } from '../config';
import { apiFetch } from '../api/client';
import { SAMPLE_RATE } from './mic';

// How long to wait for Azure after the user taps Stop.
const TIMEOUT_MS = { assessment: 5000, transcription: 10000 };

// ---------------------------------------------------------------- SDK

let sdkPromise = null;

// The SDK is large, so it's only downloaded when speaking practice needs it.
function loadSpeechSdk() {
    sdkPromise ??= import('microsoft-cognitiveservices-speech-sdk')
        .then((m) => (m.SpeechConfig ? m : m.default))
        .catch((err) => {
            sdkPromise = null; // let the next call retry
            throw err;
        });
    return sdkPromise;
}

// ---------------------------------------------------------------- token

let cachedToken = null; // { value, region, expiresAt }
let tokenRequest = null;

// The server already refreshes a minute before Azure's 10-minute expiry, so
// its expires_in can be used as is.
export function getSpeechToken() {
    if (cachedToken && Date.now() < cachedToken.expiresAt) return Promise.resolve(cachedToken);
    tokenRequest ??= apiFetch(`${API_BASE_URL}/api/speech/token`)
        .then(async (res) => {
            if (!res.ok) throw new Error(`Speech token request failed (${res.status})`);
            const data = await res.json();
            cachedToken = {
                value: data.token,
                region: data.region,
                expiresAt: Date.now() + data.expires_in * 1000,
            };
            return cachedToken;
        })
        .finally(() => { tokenRequest = null; });
    return tokenRequest;
}

// ---------------------------------------------------------------- recognition

export async function prepareRecognition({ mode, referenceText = '' }) {
    const [sdk, token] = await Promise.all([loadSpeechSdk(), getSpeechToken()]);

    const speechConfig = sdk.SpeechConfig.fromAuthorizationToken(token.value, token.region);
    speechConfig.speechRecognitionLanguage = 'zh-CN';
    // Detailed results include the spoken ("lexical") form: 三个, not 3个.
    speechConfig.outputFormat = sdk.OutputFormat.Detailed;

    const format = sdk.AudioStreamFormat.getWaveFormatPCM(SAMPLE_RATE, 16, 1);
    const pushStream = sdk.AudioInputStream.createPushStream(format);
    const recognizer = new sdk.SpeechRecognizer(speechConfig, sdk.AudioConfig.fromStreamInput(pushStream));

    if (mode === 'assessment') {
        new sdk.PronunciationAssessmentConfig(
            referenceText,
            sdk.PronunciationAssessmentGradingSystem.HundredMark,
            sdk.PronunciationAssessmentGranularity.Phoneme,
            false, // miscue: not needed for single words
        ).applyTo(recognizer);
    }

    // Open the connection now, so Record doesn't wait on the handshake. If it
    // fails, recognition simply connects when the take starts.
    sdk.Connection.fromRecognizer(recognizer).openConnection(
        undefined,
        (err) => console.warn('[speech] pre-connect failed, will connect on record', err),
    );

    let closed = false;
    const dispose = () => {
        if (closed) return;
        closed = true;
        try { recognizer.close(); } catch { /* already closed */ }
    };

    let begun = false;
    return {
        dispose,
        begin() {
            if (begun) throw new Error('A prepared recognition can only be used for one take');
            begun = true;
            const run = mode === 'assessment' ? runOnce(sdk, recognizer) : runContinuous(sdk, recognizer);
            let ended = false;

            return {
                write(chunk) {
                    // Copy: the mic keeps its own chunks for "Hear yourself".
                    if (!ended) pushStream.write(chunk.slice().buffer);
                },
                async end() {
                    if (ended) return { kind: 'error', message: 'Take already ended' };
                    ended = true;
                    // End of audio: Azure finishes now instead of waiting for silence.
                    pushStream.close();
                    const timeout = new Promise((resolve) =>
                        setTimeout(() => resolve({ kind: 'timeout' }), TIMEOUT_MS[mode]));
                    const outcome = await Promise.race([run.done, timeout]);
                    await run.stop();
                    dispose();
                    return outcome;
                },
                cancel() {
                    if (ended) return;
                    ended = true;
                    try { pushStream.close(); } catch { /* already closed */ }
                    run.stop().finally(dispose);
                },
            };
        },
    };
}

// Single words: one utterance, with pronunciation scores.
function runOnce(sdk, recognizer) {
    const done = new Promise((resolve) => {
        recognizer.recognizeOnceAsync(
            (result) => resolve(parseAssessment(sdk, result)),
            (err) => resolve({ kind: 'error', message: String(err) }),
        );
    });
    return { done, stop: () => Promise.resolve() };
}

// Sentences: keep going through pauses until the audio ends.
function runContinuous(sdk, recognizer) {
    const display = [];
    const lexical = [];
    let error = null;

    const done = new Promise((resolve) => {
        const finish = () => {
            if (error) return resolve({ kind: 'error', message: error });
            if (!display.length) return resolve({ kind: 'no_speech' });
            resolve({ kind: 'ok', text: display.join(''), lexical: lexical.join('') });
        };
        recognizer.recognized = (_, e) => {
            if (e.result.reason !== sdk.ResultReason.RecognizedSpeech) return;
            logRaw(sdk, e.result);
            display.push(e.result.text);
            lexical.push(lexicalForm(sdk, e.result));
        };
        recognizer.canceled = (_, e) => {
            // EndOfStream is the normal ending; sessionStopped follows it.
            if (e.reason === sdk.CancellationReason.Error) {
                error = e.errorDetails || 'Recognition canceled';
                finish();
            }
        };
        recognizer.sessionStopped = finish;
        recognizer.startContinuousRecognitionAsync(undefined, (err) => {
            error = String(err);
            finish();
        });
    });

    const stop = () => new Promise((resolve) => {
        try {
            recognizer.stopContinuousRecognitionAsync(resolve, resolve);
        } catch {
            resolve(); // recognizer already closed
        }
    });
    return { done, stop };
}

function parseAssessment(sdk, result) {
    if (result.reason === sdk.ResultReason.Canceled) {
        const details = sdk.CancellationDetails.fromResult(result);
        return details.reason === sdk.CancellationReason.Error
            ? { kind: 'error', message: details.errorDetails || 'Recognition canceled' }
            : { kind: 'no_speech' };
    }
    if (result.reason !== sdk.ResultReason.RecognizedSpeech) return { kind: 'no_speech' };

    logRaw(sdk, result);
    const pa = sdk.PronunciationAssessmentResult.fromResult(result);
    const words = pa.detailResult?.Words ?? [];
    return {
        kind: 'ok',
        text: result.text ?? '',
        accuracy: pa.accuracyScore,
        phonemes: words.flatMap((w) => (w.Phonemes ?? []).map((p) => ({
            phoneme: p.Phoneme,
            accuracy: p.PronunciationAssessment?.AccuracyScore,
        }))),
    };
}

function rawJson(sdk, result) {
    try {
        return JSON.parse(result.properties.getProperty(sdk.PropertyId.SpeechServiceResponse_JsonResult));
    } catch {
        return null;
    }
}

function lexicalForm(sdk, result) {
    return rawJson(sdk, result)?.NBest?.[0]?.Lexical ?? result.text ?? '';
}

// Everything Azure sent, including syllable/phoneme detail (useful later for tones).
function logRaw(sdk, result) {
    console.debug('[speech] Azure result', rawJson(sdk, result));
}