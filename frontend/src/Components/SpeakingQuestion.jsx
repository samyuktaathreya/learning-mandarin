import { ClickableText } from './CharacterPopup';
import { useState, useEffect } from 'react';

const questionTypeToInstruction = (question_type) => {
    switch (question_type) {
        case "speaking vocab":    return "Say this word out loud:";
        case "speaking sentence": return "Say this sentence out loud:";
        default:                  return "Say this out loud:";
    }
};

const feedbackMessage = (feedback) => {
    switch (feedback) {
        case "tone":            return "✗ Right sounds, but your tone was off";
        case "sound":           return "✗ Your tone was right — the sound itself wasn't quite there";
        case "sound_and_tone":  return "✗ Not quite — both the sound and the tone need work";
        default:                return "✗ Not quite — compare what you said to the expected answer";
    }
};

const getScoreClass = (score) => {
    if (score == null) return 'score-inherit';
    if (score >= 90) return 'score-green';
    if (score >= 75) return 'score-orange';
    return 'score-red';
};

export default function SpeakingQuestion({
    currentQuestionObj,
    currentIndex,
    totalQuestions,
    sessionType,
    isRecording,
    isTranscribing,
    transcriptionResult,
    recordingURL,
    onStartRecording,
    onStopRecording,
    onAdvanceQuestion,
    onMarkCorrect,
    onTryAgain,
    onPlayAudio,
    debug,
}) {
    const isUnitTest = sessionType === "unit_test";
    const isAssessment = transcriptionResult?.mode === "assessment";
    
    // Check if the current question requires the shadowing phase
    const requiresShadowing = currentQuestionObj.question_type === "speaking sentence";

    // ── Tip UI & Logic state ──────────────────────────────────────
    const [showTipForm, setShowTipForm] = useState(false);
    const [tipKeyType, setTipKeyType] = useState("answer");
    const [tipDraft, setTipDraft] = useState("");
    const [tipSaveState, setTipSaveState] = useState(null); 
    const [audioCompleted, setAudioCompleted] = useState(false); 
    const [sentenceTagMetadata, setSentenceTagMetadata] = useState({});
    
    // Track if we are in the shadowing phase
    const [hasPassedFirstTry, setHasPassedFirstTry] = useState(false);

    // reset forms and logic state whenever the question changes
    useEffect(() => {
        setShowTipForm(false);
        setTipKeyType("answer");
        setTipDraft("");
        setTipSaveState(null);
        setAudioCompleted(false); 
        setHasPassedFirstTry(false); 
    }, [currentQuestionObj]);

    // Fetch sentence tags with context-aware definitions
    useEffect(() => {
        if (!currentQuestionObj || !currentQuestionObj.sentence_id) {
            setSentenceTagMetadata({});
            return;
        }
        
        fetch(`/api/sentence_tags/${currentQuestionObj.sentence_id}`)
            .then(res => res.json())
            .then(data => setSentenceTagMetadata(data.tags || {}))
            .catch(err => {
                console.error("Failed to fetch sentence tags", err);
                setSentenceTagMetadata({});
            });
    }, [currentQuestionObj?.sentence_id]);

    // Play target audio automatically after submission/transcription completes
    useEffect(() => {
        if (transcriptionResult && !transcriptionResult.error && !transcriptionResult.hallucination) {
            let isMounted = true;
            
            const playTarget = async () => {
                await onPlayAudio(currentQuestionObj.question);
                if (isMounted) setAudioCompleted(true);
            };
            
            playTarget();
            
            return () => { isMounted = false; };
        }
    }, [transcriptionResult, currentQuestionObj.question, onPlayAudio]);

    const saveTip = async () => {
        const keyValue = tipKeyType === "question" ? currentQuestionObj.question : currentQuestionObj.answer;
        if (!tipDraft.trim() || !keyValue) return;
        setTipSaveState('saving');
        try {
            await fetch('/api/tips', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ key_type: tipKeyType, key_value: keyValue, tip: tipDraft.trim() }),
            });
            setTipSaveState('saved');
        } catch (err) {
            console.error("Failed to save tip", err);
            setTipSaveState('error');
        }
    };

    return (
        <div className="session-view">
            <p>Question {currentIndex + 1} of {totalQuestions}</p>
            {currentQuestionObj.unit != null && <p>Unit {currentQuestionObj.unit}</p>}
            {isUnitTest && <p className="unit-test-label">Unit Test</p>}
            <h2>{questionTypeToInstruction(currentQuestionObj.question_type)}</h2>
            <h1>
                <ClickableText 
                    text={currentQuestionObj.question} 
                    tags={currentQuestionObj.tags || []} 
                    tagMetadata={sentenceTagMetadata}
                    isUnitTest={isUnitTest} 
                />
            </h1>

            {!transcriptionResult && !recordingURL && (
                <div className="recording-controls">
                    {hasPassedFirstTry && requiresShadowing && (
                        <div className="shadow-alert">
                            <strong>Shadowing Phase:</strong> Match the native speaker's speed and rhythm!
                            <div className="shadow-listen-wrapper">
                                <button type="button" className="shadow-listen-btn" onClick={() => onPlayAudio(currentQuestionObj.question)}>
                                    🔊 Listen again
                                </button>
                            </div>
                        </div>
                    )}
                    <button 
                        type="button" 
                        className={`record-btn ${isRecording ? 'is-recording' : ''}`}
                        onClick={isRecording ? onStopRecording : onStartRecording}
                    >
                        {isRecording ? '⏹ Stop' : '🎙 Record'}
                    </button>
                </div>
            )}

            {isTranscribing && <p className="status-message">Checking your pronunciation...</p>}

            {recordingURL && !isTranscribing && !transcriptionResult && (
                <div className="preview-controls">
                    <button type="button" onClick={() => new Audio(recordingURL).play()}>🎧 Hear yourself</button>
                    <button type="button" onClick={() => onAdvanceQuestion(false)}>Skip</button>
                </div>
            )}

            {transcriptionResult && (
                transcriptionResult.error
                    ? <div className="error-container">
                        <p className="warning-text">⚠️ Couldn't check that — skip or try again</p>
                        <button onClick={onTryAgain}>Try Again</button>
                      </div>
                    : transcriptionResult.hallucination
                        ? <div className="error-container">
                            <p className="warning-text">⚠️ Couldn't hear you — try again closer to the mic</p>
                            <button onClick={onTryAgain}>Try Again</button>
                          </div>
                        : <div className="result-container">
                            <div className="replay-buttons">
                                {recordingURL && (
                                    <button type="button" onClick={() => new Audio(recordingURL).play()}>🎧 Hear yourself</button>
                                )}
                                <button type="button" onClick={() => onPlayAudio(currentQuestionObj.question)}>🔊 Hear target</button>
                                <button type="button" onClick={() => onPlayAudio(currentQuestionObj.question, true)}>🐢 Slow</button>
                            </div>

                            <p className="transcription-text">
                                You said: <strong>{transcriptionResult.transcription}</strong> ({transcriptionResult.transcription_pinyin})
                            </p>
                            <p className="expected-text">
                                Expected: <strong>{currentQuestionObj.answer}</strong> ({transcriptionResult.expected_pinyin})
                            </p>

                            {isAssessment && (
                                <div className="assessment-stats">
                                    <p className="score-container">
                                        Pronunciation score:{' '}
                                        <strong className={`main-score ${getScoreClass(transcriptionResult.accuracy)}`}>
                                            {transcriptionResult.accuracy}
                                        </strong>
                                        <span className="score-max"> / 100 (need {transcriptionResult.accuracy_threshold})</span>
                                    </p>

                                    {transcriptionResult.phonemes?.length > 0 && (
                                        <p className="phoneme-list">
                                            {transcriptionResult.phonemes.map((p, i) => (
                                                <span key={i} className="phoneme-item">
                                                    <code>{p.phoneme}</code>{' '}
                                                    <strong className={getScoreClass(p.accuracy)}>{p.accuracy}</strong>
                                                </span>
                                            ))}
                                        </p>
                                    )}

                                    {!transcriptionResult.is_correct && transcriptionResult.weakest_phoneme && (
                                        <p className="weakest-phoneme">
                                            Weakest sound: <code>{transcriptionResult.weakest_phoneme.phoneme}</code>
                                            {' '}({transcriptionResult.weakest_phoneme.accuracy})
                                        </p>
                                    )}
                                </div>
                            )}

                            {transcriptionResult.is_correct ? (
                                requiresShadowing ? (
                                    !hasPassedFirstTry ? (
                                        <div className="shadow-pass-box">
                                            <p className="shadow-pass-title">✓ First pass correct!</p>
                                            <p className="shadow-pass-prompt">Now listen to the target audio and repeat it to build muscle memory.</p>
                                            <button 
                                                className="start-shadow-btn"
                                                onClick={() => {
                                                    setHasPassedFirstTry(true);
                                                    onTryAgain(); 
                                                }}
                                                disabled={!audioCompleted}
                                            >
                                                {audioCompleted ? "🎙️ Start Shadowing" : "Playing audio..."}
                                            </button>
                                        </div>
                                    ) : (
                                        <p className="shadow-complete-text">✓ Shadowing Complete!</p>
                                    )
                                ) : (
                                    <p className="correct-text">✓ Correct!</p>
                                )
                            ) : (
                                <p className="incorrect-text">
                                    {isAssessment
                                        ? feedbackMessage(transcriptionResult.feedback)
                                        : "✗ Not quite — compare what you said to the expected answer"}
                                </p>
                            )}

                            {currentQuestionObj.english && (
                                <p className="translation-text">Translation: <strong>{currentQuestionObj.english}</strong></p>
                            )}

                            {currentQuestionObj.tip && (
                                <p className="question-tip">💡 Tip: {currentQuestionObj.tip}</p>
                            )}

                            <div className="tip-editor">
                                {!showTipForm ? (
                                    <button type="button" className="tip-toggle-btn" onClick={() => setShowTipForm(true)}>
                                        {currentQuestionObj.tip ? 'Edit tip' : '+ Add a tip'}
                                    </button>
                                ) : (
                                    <div className="tip-form-container">
                                        <div className="tip-radio-group">
                                            <label className="radio-label">
                                                <input
                                                    type="radio"
                                                    checked={tipKeyType === "answer"}
                                                    onChange={() => setTipKeyType("answer")}
                                                /> Tip about the answer
                                            </label>
                                            <label className="radio-label">
                                                <input
                                                    type="radio"
                                                    checked={tipKeyType === "question"}
                                                    onChange={() => setTipKeyType("question")}
                                                /> Tip about the question
                                            </label>
                                        </div>
                                        <textarea
                                            className="tip-textarea"
                                            value={tipDraft}
                                            onChange={(e) => setTipDraft(e.target.value)}
                                            placeholder="e.g. the 儿 here is a rhotic suffix, blend it into the vowel rather than pronouncing it separately"
                                            rows={3}
                                        />
                                        <div className="tip-actions">
                                            <button type="button" className="save-tip-btn" onClick={saveTip} disabled={tipSaveState === 'saving'}>
                                                {tipSaveState === 'saving' ? 'Saving…' : 'Save tip'}
                                            </button>
                                            <button type="button" className="cancel-tip-btn" onClick={() => setShowTipForm(false)}>
                                                Cancel
                                            </button>
                                            {tipSaveState === 'saved' && <span className="tip-status saved">Saved ✓</span>}
                                            {tipSaveState === 'error' && <span className="tip-status error">Failed to save</span>}
                                        </div>
                                    </div>
                                )}
                            </div>

                            <div className="action-buttons">
                                {(!transcriptionResult.is_correct || !requiresShadowing || hasPassedFirstTry) && (
                                    <>
                                        <button 
                                            className="continue-btn"
                                            onClick={() => onAdvanceQuestion(transcriptionResult.is_correct)}
                                            disabled={transcriptionResult.is_correct && !audioCompleted}
                                        >
                                            {transcriptionResult.is_correct && !audioCompleted ? "Playing audio..." : "Continue"}
                                        </button>
                                        <button className="try-again-btn" onClick={onTryAgain}>Try Again</button>
                                    </>
                                )}
                            </div>
                          </div>
            )}

            {debug && (
                <button type="button" className="debug-btn" onClick={onMarkCorrect}>✓ Mark correct (debug)</button>
            )}
        </div>
    );
}