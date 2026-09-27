import UnitDetail from './UnitDetail';
import PinyinProgress from './GetPinyinProgress';

export default function UnitCenter({ progress, selectedUnit, onStartSession }) {
    if (!progress || selectedUnit === null) return null;

    const unitData = progress.unit_progress[String(selectedUnit)];
    if (!unitData) return null;

    const isCurrentUnit = unitData.is_current;
    const isGraduated = unitData.is_graduated;
    const isLocked = !isCurrentUnit && !isGraduated;
    const isPinyinUnit = selectedUnit === 0;

    return (
        <div className="unit-center">
            <h2>Unit {selectedUnit}</h2>

            {isLocked && <p>🔒 Complete Unit {selectedUnit - 1} to unlock</p>}

            {isGraduated && <p>✓ Graduated</p>}

            {/* Unit 0 is the pinyin unit -- show unlocked-sound progress instead of
                the word-facet detail view, which doesn't apply to pinyin sounds. */}
            {(isCurrentUnit || isGraduated) && (
                isPinyinUnit ? <PinyinProgress /> : <UnitDetail unit={selectedUnit} />
            )}
        </div>
    );
}