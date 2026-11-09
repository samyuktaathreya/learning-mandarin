import { useEffect } from 'react';
import { setMicWanted } from './mic';

// Keeps the shared mic open while `enabled` is true, and closes it when it
// turns false or the component unmounts. Use it once, where the session lives.
export function useMic(enabled) {
    useEffect(() => {
        setMicWanted(enabled);
    }, [enabled]);

    useEffect(() => () => setMicWanted(false), []);
}