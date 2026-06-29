import { ClickableText } from './CharacterPopup';

const questionTypeToInstruction = (question_type) => {
    switch (question_type) {
        case "speaking vocab":    return "Say this word out loud:";
        case "speaking sentence": return "Say this sentence out loud:";
        default:                  return "Say this out loud:";
    }
};

export default function SpeakingQuestion({
    currentQuestionObj,
    currentIndex,
    totalQuestions,
    sessionType,
    isSingleSyllable,
    isRecording,
    isTranscribing,
    transcriptionResult,
    recordingURL,
    onStartRecording,
    onStopRecording,
    onAdvanceQuestion,
    onTryAgain,
    onPlayAudio,
}) {
    const isUnitTest = sessionType === "unit_test";

    return (
        <div className="session-view">
            <p>Question {currentIndex + 1} of {totalQuestions}</p>
            {isUnitTest && <p>Unit Test</p>}
            <h2>{questionTypeToInstruction(currentQuestionObj.question_type)}</h2>
            <h1><ClickableText text={currentQuestionObj.question} tags={currentQuestionObj.tags || []} isUnitTest={isUnitTest} /></h1>

            {!isUnitTest && (
                <>
                    <button type="button" onClick={() => onPlayAudio(currentQuestionObj.question)}>🔊 Hear it</button>
                    <button type="button" onClick={() => onPlayAudio(currentQuestionObj.question, true)}>🐢 Slow</button>
                </>
            )}

            {/* record button — hide once we have a result */}
            {!transcriptionResult && !recordingURL && (
                <button type="button" onClick={isRecording ? onStopRecording : onStartRecording}
                    style={{ color: isRecording ? 'red' : 'inherit' }}>
                    {isRecording ? '⏹ Stop' : '🎙 Record'}
                </button>
            )}

            {isTranscribing && <p>Transcribing...</p>}

            {/* playback + skip always available after recording */}
            {recordingURL && !isTranscribing && (
                <>
                    <button type="button" onClick={() => new Audio(recordingURL).play()}>🎧 Hear yourself</button>
                    <button type="button" onClick={() => onAdvanceQuestion(false)}>Skip</button>
                </>
            )}

            {/* single syllable: self-grade */}
            {isSingleSyllable && recordingURL && !isTranscribing && (
                <div>
                    <p>Did you say it right?</p>
                    <button onClick={() => onAdvanceQuestion(true)}>✓ Yes</button>
                    <button onClick={onTryAgain}>✗ No, try again</button>
                </div>
            )}

            {/* multi syllable: Azure grading */}
            {!isSingleSyllable && transcriptionResult && (
                transcriptionResult.error
                    ? <div>
                        <p style={{ color: 'orange' }}>⚠️ Transcription failed — skip or try again</p>
                        <button onClick={onTryAgain}>Try Again</button>
                    </div>
                    : transcriptionResult.hallucination
                        ? <div>
                            <p style={{ color: 'orange' }}>⚠️ Couldn't understand — try again closer to the mic</p>
                            <button onClick={onTryAgain}>Try Again</button>
                        </div>
                        : <div>
                            <p>You said: <strong>{transcriptionResult.transcription}</strong> ({transcriptionResult.transcription_pinyin})</p>
                            <p>Expected: <strong>{currentQuestionObj.answer}</strong> ({transcriptionResult.expected_pinyin})</p>
                            {transcriptionResult.is_correct
                                ? <p style={{ color: 'green' }}>✓ Correct!</p>
                                : <p style={{ color: 'red' }}>✗ Not quite — check your tones</p>}
                            <button onClick={() => onAdvanceQuestion(transcriptionResult.is_correct)}>Continue</button>
                            <button onClick={onTryAgain}>Try Again</button>
                        </div>
            )}
        </div>
    );
}