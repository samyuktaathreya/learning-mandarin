import { useCallback, useEffect, useMemo, useState } from 'react';
import { fetchPinyinProgress } from '../api/pinyin';
import { buildSections, summarize } from '../utils/pinyinHelpers';

export function usePinyinProgress() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [reloadKey, setReloadKey] = useState(0);

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        setError(null);

        fetchPinyinProgress()
            .then((res) => { if (!cancelled) setData(res); })
            .catch((err) => { if (!cancelled) setError(err); })
            .finally(() => { if (!cancelled) setLoading(false); });

        return () => { cancelled = true; };
    }, [reloadKey]);

    const sections = useMemo(() => (data ? buildSections(data) : []), [data]);
    const summary = useMemo(() => summarize(sections), [sections]);
    const reload = useCallback(() => setReloadKey((k) => k + 1), []);

    return { sections, summary, graduated: Boolean(data?.graduated), loading, error, reload };
}