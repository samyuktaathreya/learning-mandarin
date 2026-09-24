import { useState, useRef, useCallback } from 'react';
import { transcribeAudio } from '../api/practice';

const blobToBase64 = (blob) => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => resolve(reader.result.split(',')[1]);
    reader.onerror = reject;
    reader.readAsDataURL(blob);
});

// Microphone recording + server transcription for speaking questions.
// Single-syllable answers are recorded for playback only, not transcribed.
export default function useSpeechRecorder({ questionObj, isSingleSyllable, onTranscribed }) {
    const [isRecording, setIsRecording] = useState(false);
    const [isTranscribing, setIsTranscribing] = useState(false);
    const [transcriptionResult, setTranscriptionResult] = useState(null);
    const [recordingURL, setRecordingURL] = useState(null);

    const mediaRecorderRef = useRef(null);
    const audioChunksRef = useRef([]);
    const recordingURLRef = useRef(null);

    // Latest values, so recorder callbacks never read stale props.
    const latest = useRef({});
    latest.current = { questionObj, isSingleSyllable, onTranscribed };

    const replaceRecordingURL = useCallback((url) => {
        if (recordingURLRef.current) URL.revokeObjectURL(recordingURLRef.current);
        recordingURLRef.current = url;
        setRecordingURL(url);
    }, []);

    // Clears the transcription and recording (used between questions and for "try again").
    const resetRecording = useCallback(() => {
        setTranscriptionResult(null);
        replaceRecordingURL(null);
    }, [replaceRecordingURL]);

    const transcribe = async (blob) => {
        try {
            const base64 = await blobToBase64(blob);
            const result = await transcribeAudio(base64, latest.current.questionObj);
            setTranscriptionResult(result);
            latest.current.onTranscribed?.();
        } catch (err) {
            console.error("Transcription failed", err);
        } finally {
            setIsTranscribing(false);
        }
    };

    const startRecording = async () => {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            audioChunksRef.current = [];
            const mediaRecorder = new MediaRecorder(stream);
            mediaRecorderRef.current = mediaRecorder;
            mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) audioChunksRef.current.push(e.data); };
            mediaRecorder.onstop = async () => {
                stream.getTracks().forEach(t => t.stop());
                const blob = new Blob(audioChunksRef.current, { type: 'audio/webm' });
                replaceRecordingURL(URL.createObjectURL(blob));
                setIsTranscribing(false);
                if (!latest.current.isSingleSyllable) await transcribe(blob);
            };
            mediaRecorder.start();
            setIsRecording(true);
        } catch (err) { console.error("Microphone access denied", err); }
    };

    const stopRecording = () => {
        mediaRecorderRef.current?.stop();
        setIsRecording(false);
        if (!isSingleSyllable) setIsTranscribing(true);
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