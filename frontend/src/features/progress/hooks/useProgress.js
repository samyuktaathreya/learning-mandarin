import { useState, useEffect, useCallback, useRef } from 'react';
import { fetchProgress } from '../api/progress';

// Loads the user's progress for the HSK level being viewed and tracks which
// unit is selected in the sidebar. userLevel is the level the user is actually
// on (the highest one unlocked); viewedLevel starts there and can be moved to
// any earlier level to look back at old units.
export default function useProgress() {
    const [progress, setProgress] = useState(null);
    const [selectedUnit, setSelectedUnit] = useState(null);
    const [userLevel, setUserLevel] = useState(null);
    const [viewedLevel, setViewedLevel] = useState(null);
    const latestRequest = useRef(0);
    // What was last shown, so a reload can tell whether the user moved on.
    const shown = useRef({ level: null, currentUnit: null });

    // level == null means "whatever level the user is on now".
    const loadLevel = useCallback(async (level) => {
        const requestId = ++latestRequest.current;
        try {
            const data = await fetchProgress(level);
            if (requestId !== latestRequest.current) return; // a newer level was picked
            const ownLevel = data.hsk_level ?? 1;
            const shownLevel = level ?? ownLevel;

            // Follow the user forward: if the level changed (e.g. HSK 1 just
            // graduated into HSK 2) or they were looking at the unit that was
            // current, clear the selection so it snaps to the new current unit.
            const prev = shown.current;
            setSelectedUnit(sel =>
                shownLevel !== prev.level || sel === prev.currentUnit ? null : sel
            );
            shown.current = { level: shownLevel, currentUnit: data.current_unit };

            setUserLevel(ownLevel);
            setViewedLevel(shownLevel);
            setProgress(data);
        } catch (e) { console.error("Failed to fetch progress", e); }
    }, []);

    // When viewing their own level, reload without pinning a level so a
    // graduation into the next HSK level shows up straight away.
    const refreshProgress = useCallback(
        () => loadLevel(viewedLevel === userLevel ? null : viewedLevel),
        [loadLevel, viewedLevel, userLevel],
    );

    useEffect(() => { loadLevel(null); }, [loadLevel]);

    const selectLevel = useCallback((level) => {
        if (userLevel == null || level < 1 || level > userLevel || level === viewedLevel) return;
        setProgress(null);
        setSelectedUnit(null);
        setViewedLevel(level);
        loadLevel(level);
    }, [userLevel, viewedLevel, loadLevel]);

    // Default the selection once a level's progress loads: the current unit on
    // the user's own level, otherwise the first unit of the level.
    useEffect(() => {
        if (!progress || selectedUnit !== null) return;
        if (viewedLevel === userLevel) {
            setSelectedUnit(progress.current_unit);
        } else {
            const units = Object.values(progress.unit_progress).map(u => u.unit);
            if (units.length) setSelectedUnit(Math.min(...units));
        }
    }, [progress, selectedUnit]);

    return {
        progress, selectedUnit, setSelectedUnit, refreshProgress,
        userLevel, viewedLevel, selectLevel,
    };
}
