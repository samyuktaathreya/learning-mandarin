import { useState } from 'react';
import Header from './Header';
import UnitSidebar from './UnitSidebar';
import UnitCenter from './UnitCenter';
import SessionControls from './PhaseTabs';
import Modal from './Modal';
import ReviewCounter from './ReviewCounter';

function SkipReviewModal({ open, dueNow, onCancel, onConfirm }) {
    return (
        <Modal
            open={open}
            onClose={onCancel}
            title="Skip today’s review?"
            actions={
                <>
                    <button onClick={onCancel}>Do the review</button>
                    <button onClick={onConfirm} style={{ color: '#c0392b' }}>
                        Skip anyway
                    </button>
                </>
            }
        >
            You have <strong>{dueNow}</strong> word{dueNow === 1 ? '' : 's'} due for review.
            Skipping means you’ll likely forget them — spaced review is what moves words
            into long-term memory. Only skip if you already know this material cold.
        </Modal>
    );
}

// The unit overview shown when no session is running.
// startSession(debug, skipReview) is the session starter from the practice page.
export default function PracticeHome({ progress, selectedUnit, onSelectUnit, isLoading, startSession }) {
    const [showSkipWarning, setShowSkipWarning] = useState(false);
    const dueNow = progress?.review_due_word_count ?? 0;

    const confirmSkipReview = () => { setShowSkipWarning(false); startSession(false, true); };
    const cancelSkipReview = () => setShowSkipWarning(false);

    if (progress != null) console.log("unit_progress:", progress.unit_progress);

    return (
        <div className="website-page">
            <Header />
            <div className="progress-layout">
                <UnitSidebar
                    progress={progress}
                    selectedUnit={selectedUnit}
                    onSelectUnit={onSelectUnit}
                />
                <div className="unit-center-column">
                    {progress && selectedUnit === progress.current_unit && (
                        <>
                            <SessionControls
                                onStartSession={() => startSession(false, false)}
                                onDebug={() => startSession(true)}
                                disabled={isLoading}
                            />
                            {dueNow > 0 && (
                                <div className="skip-review-row" style={{ marginTop: '0.5rem' }}>
                                    <button onClick={() => setShowSkipWarning(true)} disabled={isLoading}>
                                        Skip review ({dueNow} due)
                                    </button>
                                </div>
                            )}
                        </>
                    )}
                    <UnitCenter
                        progress={progress}
                        selectedUnit={selectedUnit}
                        onStartSession={startSession}
                    />
                </div>
            </div>

            <ReviewCounter progress={progress} />

            <SkipReviewModal
                open={showSkipWarning}
                dueNow={dueNow}
                onCancel={cancelSkipReview}
                onConfirm={confirmSkipReview}
            />
        </div>
    );
}