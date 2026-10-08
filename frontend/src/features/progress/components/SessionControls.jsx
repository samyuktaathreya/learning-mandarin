// The old 3-tab (Listen/Vocab/Sentence) phase gate is gone -- the backend now
// drives a single unified per-word tier session (see TIER_QUESTION_TYPES in
// session.py), mixing question types across tiers in one practice_session.
// There's nothing left to gate or select, so this is just a start button.
export default function SessionControls({ onStartSession, onDebug, disabled = false }) {
    return (
        <div className="phase-tabs">
            <div className="phase-tabs-row">
                <button
                    className="phase-tab-button active"
                    onClick={onStartSession}
                    disabled={disabled}
                    title="Start a practice session"
                >
                    Practice
                </button>

                <button
                    className="phase-tab-button phase-tab-debug"
                    onClick={onDebug}
                    disabled={disabled}
                    style={{ opacity: 0.5, fontSize: '0.8rem' }}
                >
                    Debug
                </button>
            </div>
        </div>
    );
}
