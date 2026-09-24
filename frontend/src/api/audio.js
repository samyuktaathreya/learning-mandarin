import { API_BASE_URL } from '../config';
import { apiFetch } from './client';
import { hasChinese } from '../utils/questionHelpers';

// Cache of in-flight/resolved audio fetches, keyed by "text::slow"
const audioCache = new Map();

const fetchAudioData = (text, slow = false) => {
    const key = `${text}::${slow}`;
    if (audioCache.has(key)) return audioCache.get(key);

    const promise = apiFetch(`${API_BASE_URL}/api/audio`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, slow }),
    })
        .then(res => res.json())
        .then(data => data.audio)
        .catch(err => {
            audioCache.delete(key); // don't cache a failure — allow retry
            throw err;
        });

    audioCache.set(key, promise);
    return promise;
};

// Fetches and caches audio without playing it.
export const preloadAudio = (text, slow = false) => {
    if (!hasChinese(text)) return;
    fetchAudioData(text, slow).catch(err => console.error("Failed to preload audio", err));
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

export const playAudio = async (text, slow = false, currentAudioRef = null, tokenRef = null, expectedToken = null) => {
    if (!hasChinese(text)) return;
    stopCurrentAudio(currentAudioRef);

    try {
        const audio = await fetchAudioData(text, slow); // instant if preloaded

        if (tokenRef && tokenRef.current !== expectedToken) return;

        if (currentAudioRef?.current) {
            currentAudioRef.current.pause();
        }

        const audioElement = new Audio(`data:audio/mpeg;base64,${audio}`);
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