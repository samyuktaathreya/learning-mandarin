import { useEffect } from 'react';

export function useEscapeKey(onEscape, active = true) {
    useEffect(() => {
        if (!active) return undefined;
        const handleKey = (e) => { if (e.key === 'Escape') onEscape(); };
        document.addEventListener('keydown', handleKey);
        return () => document.removeEventListener('keydown', handleKey);
    }, [onEscape, active]);
}