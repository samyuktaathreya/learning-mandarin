import { API_BASE_URL } from '../../../shared/config';
import { apiFetch } from '../../../shared/api/client';
import { synthesize, NonChineseTextError, ttsRequestsLeft, msUntilNextTtsSlot } from '../../../shared/speech';

// Cache of in-flight/resolved audio fetches, keyed by "text::slow::pinyin::recording"
const audioCache = new Map();
const cacheKey = (text, slow, pinyin, preferRecording) => `${text}::${slow}::${pinyin || ''}::${preferRecording}`;

// Our server: a real pinyin recording (a URL), or server-side TTS (base64).
const fetchFromServer = (text, slow, pinyin, preferRecording) =>
    apiFetch(`${API_BASE_URL}/api/audio`, {
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
        });

// Resolves to something an <audio> element can play.
//
// preferRecording (pinyin section): from our server, which plays a real
// recording of the syllable if it has one and falls back to TTS if not.
// Everything else: TTS straight from Azure (a blob URL). If Azure is
// unavailable or at its free-tier limit, the server's TTS is the fallback.
const fetchAudioSrc = (text, slow = false, pinyin = null, preferRecording = false) => {
    const key = cacheKey(text, slow, pinyin, preferRecording);
    if (audioCache.has(key)) return audioCache.get(key);

    const source = preferRecording
        ? fetchFromServer(text, slow, pinyin, true)
        : synthesize({ text, slow, pinyin }).catch((err) => {
            if (err instanceof NonChineseTextError) throw err; // nothing to say, from anywhere
            console.warn(`[audio] Azure TTS unavailable for "${text}" (${err.message}), using the server`);
            return fetchFromServer(text, slow, pinyin, false);
        });

    const promise = source.catch(err => {
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

// Fetches a list of sounds ({ text, slow, pinyin, preferRecording }) in order,
// a few at a time, so the first ones are ready soonest and the server isn't
// hit with every TTS request at once.
export const preloadAll = (items, concurrency = 3) => {
    const queue = items.filter(item => item.text);
    const worker = async () => {
        while (queue.length) {
            const { text, slow = false, pinyin = null, preferRecording = false } = queue.shift();
            await fetchAudioSrc(text, slow, pinyin, preferRecording).catch(err => console.error("Failed to preload audio", err));
        }
    };
    return Promise.all(Array.from({ length: concurrency }, worker));
};

// Also frees the Azure audio (blob URLs) held in memory.
// Fetches a list of Azure TTS sounds ({ text, slow, pinyin }) in order, one at a
// time, as fast as Azure's free tier allows (20 a minute). `reserve` requests a
// minute are left free for things the user plays that weren't preloaded
// (🐢 Slow, replays). Stops when `signal` is aborted.
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export const preloadPaced = async (items, { reserve = 4, signal } = {}) => {
    for (const { text, slow = false, pinyin = null } of items) {
        if (signal?.aborted) return;
        if (!text || audioCache.has(cacheKey(text, slow, pinyin, false))) continue;
        while (ttsRequestsLeft() <= reserve) {
            await sleep(Math.max(msUntilNextTtsSlot(), 1000));
            if (signal?.aborted) return;
        }
        await fetchAudioSrc(text, slow, pinyin, false).catch(err => console.error("Failed to preload audio", err));
    }
};

export const clearAudioCache = () => {
    for (const promise of audioCache.values()) {
        promise.then((src) => { if (src.startsWith('blob:')) URL.revokeObjectURL(src); }, () => {});
    }
    audioCache.clear();
};

// Tells the server it can drop its generated audio for this session.
export const clearServerAudio = () => apiFetch(`${API_BASE_URL}/api/audio/clear`, { method: 'POST' });

// Only one sound plays at a time, app-wide. Starting any sound cuts off
// whatever is already playing.
let nowPlaying = null; // { element, finish }
let playToken = 0;     // bumped on every play, so a slow fetch can't start after a newer one

const stopNowPlaying = () => {
    if (nowPlaying) nowPlaying.finish();
};

// Plays a src that's already in hand (e.g. a recording's blob URL). Resolves
// when it ends or gets cut off by another sound.
export const playSrc = (src, currentAudioRef = null) => {
    stopNowPlaying();
    const audioElement = new Audio(src);
    if (currentAudioRef) currentAudioRef.current = audioElement;

    return new Promise((resolve) => {
        const finish = () => {
            audioElement.onended = audioElement.onerror = null;
            audioElement.pause();
            if (nowPlaying?.element === audioElement) nowPlaying = null;
            if (currentAudioRef?.current === audioElement) currentAudioRef.current = null;
            resolve();
        };
        nowPlaying = { element: audioElement, finish };
        audioElement.onended = finish;
        audioElement.onerror = finish;
        audioElement.play().catch(finish);
    });
};

// Stops the audio element the ref is holding, if any.
export const stopCurrentAudio = (currentAudioRef) => {
    const element = currentAudioRef?.current;
    if (!element) return;
    if (nowPlaying?.element === element) nowPlaying.finish();
    else element.pause();
    currentAudioRef.current = null;
};

export const playAudio = async (text, slow = false, currentAudioRef = null, tokenRef = null, expectedToken = null, pinyin = null, preferRecording = false) => {
    if (!(text)) return;
    const myToken = ++playToken;
    stopNowPlaying();

    try {
        const src = await fetchAudioSrc(text, slow, pinyin, preferRecording); // instant if preloaded

        if (tokenRef && tokenRef.current !== expectedToken) return;
        if (myToken !== playToken) return; // another sound was requested meanwhile

        return playSrc(src, currentAudioRef);
    } catch (error) {
        console.error("Failed to play audio", error);
    }
};