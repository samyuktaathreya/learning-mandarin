import { ClickableText } from './CharacterPopup';
import { useState, useEffect } from 'react';

const questionTypeToInstruction = (question_type) => {
    switch (question_type) {
        case "speaking vocab":    return "Say this word out loud:";
        case "speaking sentence": return "Say this sentence out loud:";
        default:                  return "Say this out loud:";
    }
};

// Which gate failed determines the advice. Telling someone to "check your
// tones" when they actually mispronounced the consonant is misleading.
const feedbackMessage = (feedback) => {
    switch (feedback) {
        case "tone":            return "✗ Right sounds, but your tone was off";
        case "sound":           return "✗ Your tone was right — the sound itself wasn't quite there";
        case "sound_and_tone":  return "✗ Not quite — both the sound and the tone need work";
        default:                return "✗ Not quite — compare what you said to the expected answer";
    }
};

const scoreColor = (score) => {
    if (score == null) return 'inherit';
    if (score >= 90) return 'green';
    if (score >= 75) return 'orange';
    return 'red';
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

    // ── Tip UI state ──────────────────────────────────────────────
    const [showTipForm, setShowTipForm] = useState(false);
    const [tipKeyType, setTipKeyType] = useState("answer");
    const [tipDraft, setTipDraft] = useState("");
    const [tipSaveState, setTipSaveState] = useState(null); // null | 'saving' | 'saved' | 'error'
    const [audioCompleted, setAudioCompleted] = useState(false); // <-- Add this

    // reset the tip form whenever the question changes
    // reset the tip form and audio state whenever the question changes
    useEffect(() => {
        setShowTipForm(false);
        setTipKeyType("answer");
        setTipDraft("");
        setTipSaveState(null);
        setAudioCompleted(false); // <-- Reset audio state
    }, [currentQuestionObj]);

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
            {isUnitTest && <p>Unit Test</p>}
            <h2>{questionTypeToInstruction(currentQuestionObj.question_type)}</h2>
            <h1><ClickableText text={currentQuestionObj.question} tags={currentQuestionObj.tags || []} isUnitTest={isUnitTest} /></h1>

            {!transcriptionResult && !recordingURL && (
                <button type="button" onClick={isRecording ? onStopRecording : onStartRecording}
                    style={{ color: isRecording ? 'red' : 'inherit' }}>
                    {isRecording ? '⏹ Stop' : '🎙 Record'}
                </button>
            )}

            {isTranscribing && <p>Checking your pronunciation...</p>}

            {recordingURL && !isTranscribing && !transcriptionResult && (
                <>
                    <button type="button" onClick={() => new Audio(recordingURL).play()}>🎧 Hear yourself</button>
                    <button type="button" onClick={() => onAdvanceQuestion(false)}>Skip</button>
                </>
            )}

            {transcriptionResult && (
                transcriptionResult.error
                    ? <div>
                        <p style={{ color: 'orange' }}>⚠️ Couldn't check that — skip or try again</p>
                        <button onClick={onTryAgain}>Try Again</button>
                      </div>
                    : transcriptionResult.hallucination
                        ? <div>
                            <p style={{ color: 'orange' }}>⚠️ Couldn't hear you — try again closer to the mic</p>
                            <button onClick={onTryAgain}>Try Again</button>
                          </div>
                        : <div>
                            {/* Replay buttons available after submitting */}
                            <div style={{ marginBottom: '1rem', display: 'flex', gap: '0.5rem' }}>
                                {recordingURL && (
                                    <button type="button" onClick={() => new Audio(recordingURL).play()}>🎧 Hear yourself</button>
                                )}
                                <button type="button" onClick={() => onPlayAudio(currentQuestionObj.question)}>🔊 Hear target</button>
                                <button type="button" onClick={() => onPlayAudio(currentQuestionObj.question, true)}>🐢 Slow</button>
                            </div>

                            <p>You said: <strong>{transcriptionResult.transcription}</strong> ({transcriptionResult.transcription_pinyin})</p>
                            <p>Expected: <strong>{currentQuestionObj.answer}</strong> ({transcriptionResult.expected_pinyin})</p>

                            {isAssessment && (
                                <div style={{ margin: '12px 0', padding: 12, border: '1px solid #ddd', borderRadius: 6 }}>
                                    <p style={{ margin: 0 }}>
                                        Pronunciation score:{' '}
                                        <strong style={{ fontSize: 24, color: scoreColor(transcriptionResult.accuracy) }}>
                                            {transcriptionResult.accuracy}
                                        </strong>
                                        <span style={{ color: '#888' }}> / 100 (need {transcriptionResult.accuracy_threshold})</span>
                                    </p>

                                    {transcriptionResult.phonemes?.length > 0 && (
                                        <p style={{ marginTop: 8, marginBottom: 0 }}>
                                            {transcriptionResult.phonemes.map((p, i) => (
                                                <span key={i} style={{ marginRight: 16 }}>
                                                    <code>{p.phoneme}</code>{' '}
                                                    <strong style={{ color: scoreColor(p.accuracy) }}>{p.accuracy}</strong>
                                                </span>
                                            ))}
                                        </p>
                                    )}

                                    {!transcriptionResult.is_correct && transcriptionResult.weakest_phoneme && (
                                        <p style={{ marginTop: 8, marginBottom: 0, color: '#666' }}>
                                            Weakest sound: <code>{transcriptionResult.weakest_phoneme.phoneme}</code>
                                            {' '}({transcriptionResult.weakest_phoneme.accuracy})
                                        </p>
                                    )}
                                </div>
                            )}

                            {transcriptionResult.is_correct
                                ? <p style={{ color: 'green' }}>✓ Correct!</p>
                                : <p style={{ color: 'red' }}>
                                    {isAssessment
                                        ? feedbackMessage(transcriptionResult.feedback)
                                        : "✗ Not quite — compare what you said to the expected answer"}
                                  </p>}

                            {currentQuestionObj.english && (
                                <p>Translation: <strong>{currentQuestionObj.english}</strong></p>
                            )}

                            {currentQuestionObj.tip && (
                                <p className="question-tip">💡 Tip: {currentQuestionObj.tip}</p>
                            )}

                            <div className="tip-editor" style={{ marginTop: '0.75rem', fontSize: '0.85rem', opacity: 0.85 }}>
                                {!showTipForm ? (
                                    <button type="button" onClick={() => setShowTipForm(true)}>
                                        {currentQuestionObj.tip ? 'Edit tip' : '+ Add a tip'}
                                    </button>
                                ) : (
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem', maxWidth: 420 }}>
                                        <div>
                                            <label style={{ marginRight: '1rem' }}>
                                                <input
                                                    type="radio"
                                                    checked={tipKeyType === "answer"}
                                                    onChange={() => setTipKeyType("answer")}
                                                /> Tip about the answer
                                            </label>
                                            <label>
                                                <input
                                                    type="radio"
                                                    checked={tipKeyType === "question"}
                                                    onChange={() => setTipKeyType("question")}
                                                /> Tip about the question
                                            </label>
                                        </div>
                                        <textarea
                                            value={tipDraft}
                                            onChange={(e) => setTipDraft(e.target.value)}
                                            placeholder="e.g. the 儿 here is a rhotic suffix, blend it into the vowel rather than pronouncing it separately"
                                            rows={3}
                                        />
                                        <div>
                                            <button type="button" onClick={saveTip} disabled={tipSaveState === 'saving'}>
                                                {tipSaveState === 'saving' ? 'Saving…' : 'Save tip'}
                                            </button>
                                            <button type="button" onClick={() => setShowTipForm(false)} style={{ marginLeft: '0.5rem' }}>
                                                Cancel
                                            </button>
                                            {tipSaveState === 'saved' && <span style={{ marginLeft: '0.5rem', color: 'green' }}>Saved ✓</span>}
                                            {tipSaveState === 'error' && <span style={{ marginLeft: '0.5rem', color: '#c0392b' }}>Failed to save</span>}
                                        </div>
                                    </div>
                                )}
                            </div>

                            <button 
                                onClick={() => onAdvanceQuestion(transcriptionResult.is_correct)}
                                disabled={!audioCompleted}
                            >
                                {audioCompleted ? "Continue" : "Playing audio..."}
                            </button>
                            <button onClick={onTryAgain}>Try Again</button>
                          </div>
            )}

            {debug && (
                <button type="button" onClick={onMarkCorrect}>✓ Mark correct (debug)</button>
            )}
        </div>
    );
}