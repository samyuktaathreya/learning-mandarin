// The three learning-tab buttons (Listen / Vocab / Sentence). Note the Vocab
// tab spans TWO backend phases: c2e (chinese→english teaching, shown once) and
// e2c (english→chinese, Anki-tracked). The backend picks which one to serve
// from the user's current phase, so the button always sends mode "vocab".
//
// unit_phase progresses: listening → c2e → e2c → sentences.
const TABS = [
    { label: 'Listen',   mode: 'listening', phases: ['listening'] },
    { label: 'Vocab',    mode: 'vocab',     phases: ['c2e', 'e2c'] },
    { label: 'Sentence', mode: 'sentence',  phases: ['sentences'] },
];

// Coverage line for whichever phase the user is currently in. Sentences have
// no per-word coverage gate, so they show nothing.
function coverageLabel(currentPhase, coverage) {
    if (!coverage) return null;
    if (currentPhase === 'listening') return `${coverage.listening_seen}/${coverage.listening_total} seen`;
    if (currentPhase === 'c2e')       return `${coverage.c2e_seen}/${coverage.c2e_total} learned`;
    if (currentPhase === 'e2c')       return `${coverage.e2c_seen}/${coverage.e2c_total} recalled`;
    return null;
}

// Is the tab that owns the current phase "done for now" -- i.e. the active
// phase is complete AND (for e2c) nothing is due for review? If so we disable
// its button with a done label instead of letting the user click into an
// empty session. We can only judge this for the ACTIVE phase, because that's
// the only one whose coverage/review the backend session would draw from.
function isActivePhaseDone(tab, currentPhase, coverage) {
    if (!coverage) return false;
    if (!tab.phases.includes(currentPhase)) return false;   // not the active tab
    if (currentPhase === 'listening') return coverage.listening_complete;
    if (currentPhase === 'c2e')       return coverage.c2e_complete;
    // e2c: complete only counts as "done" if there's also nothing due. The
    // backend tells us due-count via coverage.e2c_due (0 when nothing due).
    if (currentPhase === 'e2c')       return coverage.e2c_complete && !coverage.e2c_due;
    return false;
}

export default function PhaseTabs({
    unlockedPhases = [],
    currentPhase,
    coverage,
    onStartSession,
    onDebug,
    disabled = false,
}) {
    const unlocked = new Set(unlockedPhases);

    return (
        <div className="phase-tabs">
            <div className="phase-tabs-row">
                {TABS.map((tab) => {
                    const { label, mode, phases } = tab;
                    const isUnlocked = phases.some((p) => unlocked.has(p));
                    const isActive = phases.includes(currentPhase);
                    const isDone = isActivePhaseDone(tab, currentPhase, coverage);
                    const isDisabled = !isUnlocked || isDone || disabled;

                    let text = label;
                    if (!isUnlocked) text = `🔒 ${label}`;
                    else if (isDone) text = `✓ ${label}`;

                    let title = `Start ${label}`;
                    if (!isUnlocked) title = 'Complete the previous phase to unlock';
                    else if (isDone) title = 'Done for now — nothing left to practice';

                    return (
                        <div key={mode} className="phase-tab">
                            <button
                                className={`phase-tab-button${isActive ? ' active' : ''}${isDone ? ' done' : ''}`}
                                onClick={() => onStartSession(mode)}
                                disabled={isDisabled}
                                title={title}
                            >
                                {text}
                            </button>
                            {isActive && (
                                <span className="phase-tab-coverage">
                                    {isDone ? 'done for now' : (coverageLabel(currentPhase, coverage) || '\u00A0')}
                                </span>
                            )}
                        </div>
                    );
                })}

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