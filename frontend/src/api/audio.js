import { API_BASE_URL } from '../config';
import { apiFetch } from './client';
import { hasChinese } from '../utils/questionHelpers';

// Cache of in-flight/resolved audio fetches, keyed by "text::slow::pinyin::recording"
const audioCache = new Map();

// Resolves to something an <audio> element can play: a URL for real pinyin
// recordings, or a base64 data URI for TTS.
//
// preferRecording: pinyin section only. The server plays a real recording of
// the syllable if it has one, and falls back to TTS if not.
const fetchAudioSrc = (text, slow = false, pinyin = null, preferRecording = false) => {
    const key = `${text}::${slow}::${pinyin || ''}::${preferRecording}`;
    if (audioCache.has(key)) return audioCache.get(key);

    const promise = apiFetch(`${API_BASE_URL}/api/audio`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, slow, pinyin, source: preferRecording ? 'pinyin' : undefined }),
    })
        .then(async (res) => {
            const data = await res.json().catch(() => null);
            if (!res.ok) {
                // Forced-pronunciation failures come back as 502 with what was
                // sent to Azure (sent_to_azure) and what Azure said (azure_response).
                console.error('Audio request failed', { status: res.status, request: { text, slow, pinyin }, response: data });
                throw new Error(`Audio request failed (${res.status})`);
            }
            if (data.source === 'tts') {
                // Runs once per sound, since the promise is cached
                console.warn(`[pinyin audio] No recording for "${pinyin || text}", using TTS`);
            }
            return data.url
                ? `${API_BASE_URL}${data.url}`
                : `data:audio/mpeg;base64,${data.audio}`;
        })
        .catch(err => {
            audioCache.delete(key);
            throw err;
        });

    audioCache.set(key, promise);
    return promise;
};

export const preloadAudio = (text, slow = false, pinyin = null, preferRecording = false) => {
    if (!(text)) return;
    fetchAudioSrc(text, slow, pinyin, preferRecording).catch(err => console.error("Failed to preload audio", err));
};

export const clearAudioCache = () => audioCache.clear();

// Tells the server it can drop its generated audio for this session.
export const clearServerAudio = () => apiFetch(`${API_BASE_URL}/api/audio/clear`, { method: 'POST' });

// Pauses whatever audio element the ref is holding, if any.
export const stopCurrentAudio = (currentAudioRef) => {
    if (currentAudioRef?.current) {
        currentAudioRef.current.pause();
        currentAudioRef.current = null;
    }
};

export const playAudio = async (text, slow = false, currentAudioRef = null, tokenRef = null, expectedToken = null, pinyin = null, preferRecording = false) => {
    if (!(text)) return;
    stopCurrentAudio(currentAudioRef);

    try {
        const src = await fetchAudioSrc(text, slow, pinyin, preferRecording); // instant if preloaded

        if (tokenRef && tokenRef.current !== expectedToken) return;

        if (currentAudioRef?.current) {
            currentAudioRef.current.pause();
        }

        const audioElement = new Audio(src);
        if (currentAudioRef) currentAudioRef.current = audioElement;

        return new Promise((resolve) => {
            const finish = () => {
                if (currentAudioRef?.current === audioElement) currentAudioRef.current = null;
                resolve();
            };
            audioElement.onended = finish;
            audioElement.onerror = finish;
            audioElement.play().catch(resolve);
        });
    } catch (error) {
        console.error("Failed to play audio", error);
    }
};