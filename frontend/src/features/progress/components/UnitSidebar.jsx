function strengthBar(pct) {
    const filled = Math.round(pct / 100 * 5);
    return '█'.repeat(filled) + '░'.repeat(5 - filled);
}

export default function UnitSidebar({ progress, selectedUnit, onSelectUnit }) {
    if (!progress) return null;
    const units = Object.values(progress.unit_progress).sort((a, b) => a.unit - b.unit);

    return (
        <div className="unit-sidebar">
            {units.map(u => (
                <div
                    key={u.unit}
                    className={`unit-sidebar-item ${u.unit === selectedUnit ? 'selected' : ''}`}
                    onClick={() => onSelectUnit(u.unit)}
                >
                    {u.is_graduated
                        ? <span>Unit {u.unit} {strengthBar(u.progress_pct)}</span>
                        : u.is_current
                            ? <span>Unit {u.unit} ← current</span>
                            : <span>Unit {u.unit} 🔒</span>
                    }
                </div>
            ))}
        </div>
    );
}