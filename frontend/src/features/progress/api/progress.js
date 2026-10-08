import { API_BASE_URL } from '../../../shared/config';
import { apiFetch } from '../../../shared/api/client';

export const fetchProgress = async () => {
    const res = await apiFetch(`${API_BASE_URL}/api/progress/`);
    return res.json();
};
