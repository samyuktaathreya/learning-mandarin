import { useCallback, useState } from 'react';
import type { SpeakerRange } from '../types';

const DEFAULT_KEY = 'tone.speakerRange.v1';

function read(key: string): SpeakerRange | null {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const v = JSON.parse(raw);
    return typeof v?.lowHz === 'number' && typeof v?.highHz === 'number' && v.highHz > v.lowHz ? v : null;
  } catch {
    return null;
  }
}

/**
 * The user's calibrated pitch range, persisted in localStorage.
 * Pass a per-user key (e.g. `tone.speakerRange.v1.${userId}`) if several
 * people share a browser. To sync it to your backend later, call your API
 * in `onChange`.
 */
export function useSpeakerRange(
  storageKey: string = DEFAULT_KEY,
  onChange?: (range: SpeakerRange | null) => void,
) {
  const [range, setRangeState] = useState<SpeakerRange | null>(() => read(storageKey));

  const setRange = useCallback(
    (next: SpeakerRange | null) => {
      setRangeState(next);
      try {
        if (next) localStorage.setItem(storageKey, JSON.stringify(next));
        else localStorage.removeItem(storageKey);
      } catch {
        // storage unavailable (private mode etc.): keep it in memory for this session
      }
      onChange?.(next);
    },
    [storageKey, onChange],
  );

  return { range, isCalibrated: range !== null, setRange, clear: () => setRange(null) };
}
