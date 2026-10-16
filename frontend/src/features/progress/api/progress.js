import { API_BASE_URL } from '../../../shared/config';
import { apiFetch } from '../../../shared/api/client';

// hskLevel is optional; leave it out to get the user's own level.
export const fetchProgress = async (hskLevel) => {
    const query = hskLevel != null ? `?hsk_level=${hskLevel}` : '';
    const res = await apiFetch(`${API_BASE_URL}/api/progress/${query}`);
    return res.json();
};
