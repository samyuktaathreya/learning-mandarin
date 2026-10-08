import { useState } from 'react';
import { PracticeHome, useProgress } from '../features/progress';
import { PracticeSession, loadPracticeSession } from '../features/session';
import { useSettings } from '../features/settings';

// The "/" route: the progress home tab, or an active practice session once one starts.
export default function PracticePage() {
    const [session, setSession] = useState(null);
    const [isLoading, setIsLoading] = useState(false);
    const { progress, selectedUnit, setSelectedUnit, refreshProgress } = useProgress();
    const { settings } = useSettings();

    const startSession = async (debug = false, skipReview = false) => {
        setIsLoading(true);
        try {
            const loaded = await loadPracticeSession(skipReview, settings);
            if (!loaded) return;
            setSession({ ...loaded, debug, id: Date.now() });
        } catch (error) { console.error("Failed to load questions", error); }
        finally { setIsLoading(false); }
    };

    if (!session) {
        return (
            <PracticeHome
                progress={progress}
                selectedUnit={selectedUnit}
                onSelectUnit={setSelectedUnit}
                isLoading={isLoading}
                startSession={startSession}
            />
        );
    }

    return (
        <PracticeSession
            key={session.id}
            session={session}
            onExit={() => setSession(null)}
            onFinished={refreshProgress}
        />
    );
}
