const GRADUATION_THRESHOLD = 3;

function wordBar(correctCount) {
    const filled = Math.min(correctCount, GRADUATION_THRESHOLD);
    return '█'.repeat(filled) + '░'.repeat(GRADUATION_THRESHOLD - filled);
}

function strengthBar(pct) {
    const filled = Math.round(pct / 100 * 5);
    return '█'.repeat(filled) + '░'.repeat(5 - filled);
}

export default function UnitCenter({ progress, selectedUnit, onStartSession }) {
    if (!progress || selectedUnit === null) return null;

    const unitData = progress.unit_progress[String(selectedUnit)];
    if (!unitData) return null;

    const isCurrentUnit = unitData.is_current;
    const isGraduated = unitData.is_graduated;
    const isLocked = !isCurrentUnit && !isGraduated;
    const words = isCurrentUnit ? (progress.current_unit_words || []) : null;

    return (
        <div className="unit-center">
            <h2>Unit {selectedUnit}</h2>

            {isLocked && <p>🔒 Complete Unit {selectedUnit - 1} to unlock</p>}

            {isGraduated && (
                <div>
                    <p>✓ Graduated</p>
                    <p>Retention: {strengthBar(unitData.progress_pct)}</p>
                </div>
            )}

            {isCurrentUnit && (
                <div className="unit-center-actions">
                    <button onClick={() => onStartSession(false)}>Start Session</button>
                    <button onClick={() => onStartSession(true)} style={{ marginLeft: 8, opacity: 0.5, fontSize: '0.8rem' }}>
                        Debug
                    </button>
                </div>
            )}

            {isCurrentUnit && words && (
                <div className="word-progress-list">
                    {words.map(w => (
                        <div key={w.tag} className="word-progress-item">
                            <span style={{ fontFamily: 'monospace' }}>{wordBar(w.correct_count)}</span>
                            <span>{w.tag}</span>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}