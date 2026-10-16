const MAX_HSK_LEVEL = 6;

// Top-left dropdown for switching which HSK level's units are shown.
// Levels above the user's own level are listed but locked.
export default function HskLevelSelect({ userLevel, viewedLevel, onSelectLevel }) {
    if (userLevel == null || viewedLevel == null) return null;

    const levels = Array.from({ length: MAX_HSK_LEVEL }, (_, i) => i + 1);

    return (
        <div className="hsk-level-select">
            <label htmlFor="hsk-level-select">Level</label>
            <select
                id="hsk-level-select"
                value={viewedLevel}
                onChange={e => onSelectLevel(Number(e.target.value))}
            >
                {levels.map(level => (
                    <option key={level} value={level} disabled={level > userLevel}>
                        HSK {level}
                        {level === userLevel ? ' (current)' : ''}
                        {level > userLevel ? ' 🔒' : ''}
                    </option>
                ))}
            </select>
        </div>
    );
}
