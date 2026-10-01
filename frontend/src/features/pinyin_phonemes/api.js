import { API_BASE_URL } from '../../config';
import { apiFetch } from '../../api/client';

const EXTENSION = { 'audio/webm': 'webm', 'audio/ogg': 'ogg', 'audio/mp4': 'm4a', 'audio/mpeg': 'mp3' };

/**
 * POST a recorded clip to the backend phoneme checker.
 * Tones are ignored server-side: "zhe2" spoken as zhe4 comes back correct.
 *
 * Resolves to:
 *   { correct, transcript, syllables: [{ expected, heard, expected_initial,
 *     expected_final, heard_initial, heard_final, initial_ok, final_ok }] }
 */
export async function checkPhonemes(blob, expectedPinyin, { signal } = {}) {
  const type = (blob.type || '').split(';')[0];
  const form = new FormData();
  form.append('audio', blob, `take.${EXTENSION[type] ?? 'webm'}`);
  form.append('expected_pinyin', expectedPinyin);

  // No Content-Type header: the browser sets the multipart boundary itself.
  const res = await apiFetch(`${API_BASE_URL}/api/pinyin/check_phonemes`, {
    method: 'POST',
    body: form,
    signal,
  });
  if (!res.ok) throw new Error(`Phoneme check failed (${res.status})`);
  return res.json();
}
