import { useCallback, useEffect, useRef, useState } from 'react';
import { scorePronunciation, scoreWordPronunciation } from '../api/pronunciation';

// Stop a bit before the server's limits (MAX_SECONDS / MAX_SECONDS_WORD in app/pronunciation/constants.py).
const MAX_RECORD_MS = { syllable: 3500, word: 5500 };

// Records the learner in one take and scores it with the pronunciation model. Several targets
// (a two-syllable word) are said together; the server splits the take into syllables.
// targets: ['cao3', 'mei2'] (see toScoreTarget). onResult gets
// { ok: true, isCorrect, syllables: [{ isCorrect, response }] } after a take is scored,
// or { ok: false, message } if scoring failed.
// status: 'idle' | 'starting' | 'recording' | 'scoring' | 'done'
export function usePronunciationRecorder(targets, onResult) {
    const [status, setStatus] = useState('idle');
    const [responses, setResponses] = useState([]); // one ScoreResponse per syllable
    const [word, setWord] = useState(null);         // the whole /score_word response, for debugging
    const [message, setMessage] = useState(null);   // mic / server / "didn't hear you" errors
    const [recordingURL, setRecordingURL] = useState(null);

    const recorderRef = useRef(null);
    const timerRef = useRef(null);
    const onResultRef = useRef(onResult);
    onResultRef.current = onResult;
    const multi = targets.length > 1;

    const stop = useCallback(() => {
        clearTimeout(timerRef.current);
        if (recorderRef.current?.state === 'recording') recorderRef.current.stop();
    }, []);

    const handleRecording = async (blob) => {
        setRecordingURL(URL.createObjectURL(blob)); // the effect below revokes the old one
        setStatus('scoring');
        try {
            let syllableResponses;
            if (multi) {
                const result = await scoreWordPronunciation(blob, targets);
                setWord(result);
                syllableResponses = result.no_speech ? null : result.syllables;
            } else {
                const result = await scorePronunciation(blob, targets[0]);
                syllableResponses = result.no_speech ? null : [result];
            }
            if (!syllableResponses) {
                setMessage("Couldn't hear you — try again closer to the mic.");
                setStatus('idle');
                return;
            }
            setResponses(syllableResponses);
            setStatus('done');
            const syllables = syllableResponses.map((r) => ({ isCorrect: r.understood, response: r }));
            onResultRef.current?.({ ok: true, isCorrect: syllables.every((s) => s.isCorrect), syllables });
        } catch (err) {
            setMessage(err.message);
            setStatus('idle');
            onResultRef.current?.({ ok: false, message: err.message });
        }
    };
    const handleRecordingRef = useRef(handleRecording);
    handleRecordingRef.current = handleRecording;

    const start = useCallback(async () => {
        setMessage(null);
        setResponses([]);
        setWord(null);
        setStatus('starting');
        let stream;
        try {
            stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        } catch {
            setMessage('Microphone access was blocked. Allow it in your browser and try again.');
            setStatus('idle');
            return;
        }
        const chunks = [];
        const recorder = new MediaRecorder(stream);
        recorderRef.current = recorder;
        recorder.ondataavailable = (e) => { if (e.data.size > 0) chunks.push(e.data); };
        recorder.onstop = () => {
            stream.getTracks().forEach((t) => t.stop());
            if (recorderRef.current !== recorder) return; // unmounted mid-take
            recorderRef.current = null;
            handleRecordingRef.current(new Blob(chunks, { type: recorder.mimeType }));
        };
        recorder.start();
        setStatus('recording');
        timerRef.current = setTimeout(stop, multi ? MAX_RECORD_MS.word : MAX_RECORD_MS.syllable);
    }, [stop, multi]);

    // Release the mic and the last recording on unmount.
    useEffect(() => () => {
        const recorder = recorderRef.current;
        recorderRef.current = null;
        clearTimeout(timerRef.current);
        if (recorder?.state === 'recording') recorder.stop();
    }, []);
    useEffect(() => () => { if (recordingURL) URL.revokeObjectURL(recordingURL); }, [recordingURL]);

    return { status, responses, word, message, recordingURL, start, stop };
}
