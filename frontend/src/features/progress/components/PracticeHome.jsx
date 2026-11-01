import { useState } from 'react';
import Header from '../../../shared/components/Header';
import UnitSidebar from './UnitSidebar';
import UnitCenter from './UnitCenter';
import SessionControls from './SessionControls';
import Modal from '../../../shared/components/Modal';
import ReviewCounter from './ReviewCounter';
import HskLevelSelect from './HskLevelSelect';
import { skipPinyinUnit } from '../api/progress';

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

function SkipPinyinModal({ open, busy, onCancel, onConfirm }) {
    return (
        <Modal
            open={open}
            onClose={onCancel}
            title="Skip pinyin practice?"
            actions={
                <>
                    <button onClick={onCancel} disabled={busy}>Keep practicing pinyin</button>
                    <button onClick={onConfirm} disabled={busy}>
                        {busy ? 'Skipping…' : 'Start at Unit 1'}
                    </button>
                </>
            }
        >
            Unit 0 drills pinyin sounds and tones. If you can already read pinyin,
            you can jump straight to Unit 1. This can’t be undone, but your pinyin
            progress stays viewable under Unit 0.
        </Modal>
    );
}

// The unit overview shown when no session is running.
// startSession(debug, skipReview) is the session starter from the practice page.
// userLevel/viewedLevel/onSelectLevel drive the HSK level dropdown; sessions
// can only be started from the user's own level.
// onProgressChanged reloads progress after the user skips the pinyin unit.
export default function PracticeHome({
    progress, selectedUnit, onSelectUnit, isLoading, startSession,
    userLevel, viewedLevel, onSelectLevel, onProgressChanged,
}) {
    const [showSkipWarning, setShowSkipWarning] = useState(false);
    const [showSkipPinyin, setShowSkipPinyin] = useState(false);
    const [skippingPinyin, setSkippingPinyin] = useState(false);
    const dueNow = progress?.review_due_word_count ?? 0;
    const onOwnLevel = viewedLevel === userLevel;
    const onPinyinUnit = progress && onOwnLevel && userLevel === 1
        && progress.current_unit === 0 && selectedUnit === 0;

    const confirmSkipReview = () => { setShowSkipWarning(false); startSession(false, true); };
    const cancelSkipReview = () => setShowSkipWarning(false);

    const confirmSkipPinyin = async () => {
        setSkippingPinyin(true);
        try {
            await skipPinyinUnit();
            setShowSkipPinyin(false);
            await onProgressChanged?.();
        } catch (e) { console.error("Failed to skip pinyin unit", e); }
        finally { setSkippingPinyin(false); }
    };

    if (progress != null) console.log("unit_progress:", progress.unit_progress);

    return (
        <div className="website-page">
            <HskLevelSelect
                userLevel={userLevel}
                viewedLevel={viewedLevel}
                onSelectLevel={onSelectLevel}
            />
            <Header />
            {!progress && viewedLevel != null && (
                <div className="unit-center">Loading HSK {viewedLevel}…</div>
            )}
            <div className="progress-layout">
                <UnitSidebar
                    progress={progress}
                    selectedUnit={selectedUnit}
                    onSelectUnit={onSelectUnit}
                />
                <div className="unit-center-column">
                    {progress && onOwnLevel && selectedUnit === progress.current_unit && (
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
                            {onPinyinUnit && (
                                <div className="skip-pinyin-row" style={{ marginTop: '0.5rem' }}>
                                    <button onClick={() => setShowSkipPinyin(true)} disabled={isLoading}>
                                        Already know pinyin? Start at Unit 1
                                    </button>
                                </div>
                            )}
                        </>
                    )}
                    <UnitCenter
                        progress={progress}
                        selectedUnit={selectedUnit}
                        hskLevel={viewedLevel}
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

            <SkipPinyinModal
                open={showSkipPinyin}
                busy={skippingPinyin}
                onCancel={() => !skippingPinyin && setShowSkipPinyin(false)}
                onConfirm={confirmSkipPinyin}
            />
        </div>
    );
}