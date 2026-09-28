import { API_BASE_URL } from '../config';
import { apiFetch } from './client';

const getJson = async (url) => {
    const res = await apiFetch(url);
    if (!res.ok) throw new Error(`Request failed (${res.status}): ${url}`);
    return res.json();
};

export const fetchPinyinProgress = () => getJson(`${API_BASE_URL}/api/pinyin/progress`);

// Rows don't change during a session, so each (tag, category) is fetched at most once.
const rowCache = new Map();

// tag: bare initial/final tag, e.g. 'zh' or 'ang'. category: 'initial' | 'final'.
// Returns a representative syllable row for that sound (character, diacritic_pinyin, ...).
export const fetchPinyinRowByTag = (tag, category) => {
    const key = `${category}:${tag}`;
    if (rowCache.has(key)) return rowCache.get(key);

    const params = new URLSearchParams({ tag, category });
    const promise = getJson(`${API_BASE_URL}/api/pinyin/get_row_by_tag?${params}`)
        .catch((err) => {
            rowCache.delete(key);
            throw err;
        });

    rowCache.set(key, promise);
    return promise;
};