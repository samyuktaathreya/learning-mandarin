import { useState, useEffect, useCallback } from 'react';
import { fetchProgress } from '../api/practice';

// Loads the user's progress and tracks which unit is selected in the sidebar.
export default function useProgress() {
    const [progress, setProgress] = useState(null);
    const [selectedUnit, setSelectedUnit] = useState(null);

    const refreshProgress = useCallback(async () => {
        try {
            setProgress(await fetchProgress());
        } catch (e) { console.error("Failed to fetch progress", e); }
    }, []);

    useEffect(() => { refreshProgress(); }, [refreshProgress]);

    // Default the selection to the current unit once progress first loads.
    useEffect(() => {
        if (progress && selectedUnit === null) setSelectedUnit(progress.current_unit);
    }, [progress]);

    return { progress, selectedUnit, setSelectedUnit, refreshProgress };
}