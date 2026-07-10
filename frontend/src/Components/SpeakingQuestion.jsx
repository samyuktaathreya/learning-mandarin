import { ClickableText } from './CharacterPopup';

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

    return (
        <div className="session-view">
            <p>Question {currentIndex + 1} of {totalQuestions}</p>
            {currentQuestionObj.unit != null && <p>Unit {currentQuestionObj.unit}</p>}
            {isUnitTest && <p>Unit Test</p>}
            <h2>{questionTypeToInstruction(currentQuestionObj.question_type)}</h2>
            <h1><ClickableText text={currentQuestionObj.question} tags={currentQuestionObj.tags || []} isUnitTest={isUnitTest} /></h1>

            {!isUnitTest && (
                <>
                    <button type="button" onClick={() => onPlayAudio(currentQuestionObj.question)}>🔊 Hear it</button>
                    <button type="button" onClick={() => onPlayAudio(currentQuestionObj.question, true)}>🐢 Slow</button>
                </>
            )}

            {!transcriptionResult && !recordingURL && (
                <button type="button" onClick={isRecording ? onStopRecording : onStartRecording}
                    style={{ color: isRecording ? 'red' : 'inherit' }}>
                    {isRecording ? '⏹ Stop' : '🎙 Record'}
                </button>
            )}

            {isTranscribing && <p>Checking your pronunciation...</p>}

            {recordingURL && !isTranscribing && (
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

                            <button onClick={() => onAdvanceQuestion(transcriptionResult.is_correct)}>Continue</button>
                            <button onClick={onTryAgain}>Try Again</button>
                          </div>
            )}

            {debug && (
                <button type="button" onClick={onMarkCorrect}>✓ Mark correct (debug)</button>
            )}
        </div>
    );
}