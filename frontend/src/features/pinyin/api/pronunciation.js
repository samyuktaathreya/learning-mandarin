import { API_BASE_URL } from '../../../shared/config';
import { apiFetch } from '../../../shared/api/client';

// Pronunciation model endpoints (app/pronunciation/router.py).
const postRecording = async (path, blob, target) => {
    const body = new FormData();
    body.append('audio', blob, 'recording.webm');
    body.append('target', target);

    const res = await apiFetch(`${API_BASE_URL}/pronunciation/${path}`, { method: 'POST', body });
    if (!res.ok) {
        const detail = await res.json().then((d) => d.detail, () => null);
        throw new Error(detail || `Scoring failed (${res.status})`);
    }
    return res.json();
};

// Scores one recorded syllable. target: pinyin with a tone number, ü as v ("zhuang4", "lv4", "ma5").
// Resolves to the ScoreResponse: { target, parts: { initial, final, tone }, no_speech, understood, ms }.
export const scorePronunciation = (blob, target) => postRecording('score', blob, target);

// Scores several syllables said in one take (a two-syllable word). The server splits the take
// into syllables and scores each like scorePronunciation.
// targets: ['cao3', 'mei2']. Resolves to { target, syllables: [ScoreResponse...], no_speech,
// understood, cuts (seconds where the take was split), split_found, ms }.
export const scoreWordPronunciation = (blob, targets) => postRecording('score_word', blob, targets.join(' '));
