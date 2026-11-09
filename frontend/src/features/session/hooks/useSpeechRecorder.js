import { useState, useRef, useCallback, useEffect } from 'react';
import { startCapture, prepareRecognition } from '../../../shared/speech';
import { buildSpeakingResult, speechModeFor, referenceTextFor, loadPinyin } from '../speakingFeedback';

const prepareFor = (q) => prepareRecognition({ mode: speechModeFor(q), referenceText: referenceTextFor(q) });

// Recording + Azure feedback for speaking questions.
//
// The mic (shared/speech/mic.js) is already open, and with `prepareAhead` the
// Azure connection is opened as soon as the question appears. While the user
// speaks, audio streams to Azure, so tapping Stop only waits for Azure to
// finish the last moment of audio.
//
// The result is feedback only (see speakingFeedback.js).
export default function useSpeechRecorder({ questionObj, prepareAhead, onTranscribed }) {
    const [isRecording, setIsRecording] = useState(false);
    const [isTranscribing, setIsTranscribing] = useState(false);
    const [transcriptionResult, setTranscriptionResult] = useState(null);
    const [recordingURL, setRecordingURL] = useState(null);

    const takeRef = useRef(null);      // { capture, ready, q } while recording
    const startingRef = useRef(false); // guards double taps while the mic starts
    const recordingURLRef = useRef(null);
    const preparedRef = useRef(null);  // { q, promise }: Azure warmed up for the next take

    // Latest values, so async callbacks never read stale props.
    const latest = useRef({});
    latest.current = { questionObj, prepareAhead, onTranscribed };

    const replaceRecordingURL = useCallback((url) => {
        if (recordingURLRef.current) URL.revokeObjectURL(recordingURLRef.current);
        recordingURLRef.current = url;
        setRecordingURL(url);
    }, []);

    // Clears the result and recording (used between questions and for "try again").
    const resetRecording = useCallback(() => {
        setTranscriptionResult(null);
        replaceRecordingURL(null);
    }, [replaceRecordingURL]);

    const discardPrepared = useCallback(() => {
        const entry = preparedRef.current;
        preparedRef.current = null;
        entry?.promise.then((r) => r.dispose(), () => {});
    }, []);

    const prepareAheadFor = useCallback((q) => {
        discardPrepared();
        const promise = prepareFor(q);
        promise.catch((err) => console.error('[speech] could not prepare Azure', err));
        preparedRef.current = { q, promise };
    }, [discardPrepared]);

    // Warm up as soon as a speaking question appears.
    useEffect(() => {
        if (!prepareAhead || !questionObj) return undefined;
        loadPinyin().catch((err) => console.error('[speech] pinyin-pro failed to load', err));
        prepareAheadFor(questionObj);
        return discardPrepared;
    }, [questionObj, prepareAhead, prepareAheadFor, discardPrepared]);

    // Leaving mid-take: drop the Azure connection too.
    useEffect(() => () => {
        takeRef.current?.ready.then((s) => s.cancel(), () => {});
        takeRef.current = null;
    }, []);

    const startRecording = async () => {
        if (takeRef.current || startingRef.current) return;
        startingRef.current = true;
        const q = latest.current.questionObj;

        // Use the warmed-up connection if there is one (retrying once if it failed).
        const warm = preparedRef.current?.q === q ? preparedRef.current.promise : null;
        preparedRef.current = null;
        const prepared = warm ? warm.catch(() => prepareFor(q)) : prepareFor(q);

        // Audio that arrives before Azure is ready waits here, then goes in order.
        const live = { stream: null, pending: [] };
        let capture;
        try {
            capture = await startCapture({
                onChunk: (chunk) => (live.stream ? live.stream.write(chunk) : live.pending.push(chunk)),
            });
        } catch (err) {
            console.error('Microphone unavailable', err);
            prepared.then((r) => r.dispose(), () => {});
            startingRef.current = false;
            return;
        }

        const ready = prepared.then((recognition) => {
            const stream = recognition.begin();
            live.pending.forEach((chunk) => stream.write(chunk));
            live.pending = [];
            live.stream = stream;
            return stream;
        });
        ready.catch(() => {}); // handled in stopRecording

        takeRef.current = { capture, ready, q };
        startingRef.current = false;
        setIsRecording(true);
    };

    const stopRecording = async () => {
        const take = takeRef.current;
        if (!take) return;
        takeRef.current = null;
        setIsRecording(false);
        setIsTranscribing(true);

        const stoppedAt = performance.now();
        const recording = await take.capture.stop();
        if (!recording || latest.current.questionObj !== take.q) { // cut off, or moved on
            take.ready.then((s) => s.cancel(), () => {});
            setIsTranscribing(false);
            return;
        }
        replaceRecordingURL(URL.createObjectURL(recording.wav));

        let raw;
        try {
            raw = await (await take.ready).end();
        } catch (err) {
            raw = { kind: 'error', message: err?.message ?? String(err) };
        }
        const result = await buildSpeakingResult(raw, take.q).catch((err) => ({
            mode: speechModeFor(take.q),
            error: `Feedback failed: ${err?.message ?? err}`,
            is_correct: false,
        }));

        // Stop tap -> feedback on screen. The target is under a second.
        console.info(
            `[speech] stop -> result: ${Math.round(performance.now() - stoppedAt)} ms `
            + `(${recording.durationMs} ms of audio)`,
            result,
        );

        setIsTranscribing(false);
        if (latest.current.questionObj !== take.q) return;
        setTranscriptionResult(result);
        latest.current.onTranscribed?.();
        if (latest.current.prepareAhead) prepareAheadFor(take.q); // ready for "Try again"
    };

    return {
        isRecording,
        isTranscribing,
        transcriptionResult,
        recordingURL,
        startRecording,
        stopRecording,
        resetRecording,
    };
}