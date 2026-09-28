import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { fetchPinyinRowByTag } from '../api/pinyin';
import { playAudio, preloadAudio, stopCurrentAudio } from '../api/audio';
import { getSoundSource } from '../utils/pinyinHelpers';

// Given a card item, looks up its example character (tones need no lookup)
// and exposes play() for normal and slow audio.
export function usePinyinSound(item) {
    const tag = item?.tag ?? null;
    const isTone = Boolean(item?.isTone);
    const category = item?.category ?? null; // 'initial' | 'final'

    const [rowState, setRowState] = useState({ tag: null, row: null, status: 'idle' });
    const [playing, setPlaying] = useState(null); // null | 'normal' | 'slow'
    const audioRef = useRef(null);

    useEffect(() => {
        if (!tag || isTone) return undefined;
        let cancelled = false;
        setRowState({ tag, row: null, status: 'loading' });

        fetchPinyinRowByTag(tag, category)
            .then((row) => { if (!cancelled) setRowState({ tag, row, status: row ? 'ready' : 'error' }); })
            .catch(() => { if (!cancelled) setRowState({ tag, row: null, status: 'error' }); });

        return () => { cancelled = true; };
    }, [tag, isTone, category]);

    const row = rowState.tag === tag ? rowState.row : null;
    const status = !tag ? 'idle' : isTone ? 'ready' : rowState.tag === tag ? rowState.status : 'loading';
    const source = useMemo(() => (item && status === 'ready' ? getSoundSource(item, row) : null), [item, row, status]);

    // Forced pronunciation, via numberedPinyin -- plain playback let some
    // characters get read with the wrong tone (e.g. 母 read as 2nd tone
    // instead of its actual 3rd tone) when Azure's default reading guesses
    // wrong. Forcing the exact syllable+tone prevents that.
    useEffect(() => {
        if (source) preloadAudio(source.character, false, source.numberedPinyin);
    }, [source]);

    // Stop audio when switching sounds or closing the popup.
    useEffect(() => () => {
        stopCurrentAudio(audioRef);
        setPlaying(null);
    }, [tag]);

    const play = useCallback(async (slow = false) => {
        if (!source) return;
        setPlaying(slow ? 'slow' : 'normal');
        await playAudio(source.character, slow, audioRef, null, null, source.numberedPinyin);
        setPlaying(null);
    }, [source]);

    return { source, status, playing, play };
}