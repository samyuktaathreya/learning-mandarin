import UnitDetail from './UnitDetail';

export default function UnitCenter({ progress, selectedUnit, onStartSession }) {
    if (!progress || selectedUnit === null) return null;

    const unitData = progress.unit_progress[String(selectedUnit)];
    if (!unitData) return null;

    const isCurrentUnit = unitData.is_current;
    const isGraduated = unitData.is_graduated;
    const isLocked = !isCurrentUnit && !isGraduated;

    return (
        <div className="unit-center">
            <h2>Unit {selectedUnit}</h2>

            {isLocked && <p>🔒 Complete Unit {selectedUnit - 1} to unlock</p>}

            {isGraduated && <p>✓ Graduated</p>}

            {/* current + graduated units show the per-facet detail view (the
                review-debugging window). Locked units show nothing but the
                message above. */}
            {(isCurrentUnit || isGraduated) && <UnitDetail unit={selectedUnit} />}
        </div>
    );
}