import { API_BASE_URL } from '../../../shared/config';
import { apiFetch } from '../../../shared/api/client';

// hskLevel is optional; leave it out to get the user's own level.
export const fetchProgress = async (hskLevel) => {
    const query = hskLevel != null ? `?hsk_level=${hskLevel}` : '';
    const res = await apiFetch(`${API_BASE_URL}/api/progress/${query}`);
    return res.json();
};

// Moves the user from unit 0 (pinyin) straight to unit 1.
export const skipPinyinUnit = async () => {
    const res = await apiFetch(`${API_BASE_URL}/api/skip_pinyin`, { method: 'POST' });
    if (!res.ok) throw new Error(`skip_pinyin failed: ${res.status}`);
    return res.json();
};
