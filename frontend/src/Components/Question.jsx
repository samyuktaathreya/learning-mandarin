import ChineseIMEInput from './ChineseIMEInput';
import { ClickableText } from './CharacterPopup';
import { useState, useEffect } from 'react';

const hasChinese = (str) => /[\u4e00-\u9fff]/.test(str);

const isListeningQuestion = (qt) =>
    qt === "listening vocab" || qt === "listening sentence";

// Question types whose question/answer pair never shows English anywhere
// (pure audio/hanzi/pinyin drills) -- reveal the translation after submit.
const TYPES_MISSING_ENGLISH = new Set([
    "listening vocab",
    "listening sentence",
    "transcribe word to pinyin",
]);

// "listening vocab" is the only type here whose question (hanzi, hidden
// while listening) and answer (pinyin) never show the characters at all.
const TYPES_MISSING_CHINESE = new Set(["listening vocab"]);

const needsIME = (qt) => [
    "translate english sentence to chinese",
    "translate english word to chinese",
    "fill in the blank",
    "listening sentence"
].includes(qt);

const questionTypeToInstruction = (question_type) => {
    switch (question_type) {
        case "fill in the blank":                       return "Fill in the blank:";
        case "listening vocab":                         return "Type the pinyin (with tones) for what you hear:";
        case "listening sentence":                      return "Write what you hear in Chinese characters:";
        case "speaking vocab":                          return "Say this word out loud:";
        case "speaking sentence":                       return "Say this sentence out loud:";
        case "translate english sentence to chinese":   return "Translate to Chinese:";
        case "translate chinese sentence to english":   return "Translate to English:";
        case "translate english word to chinese":       return "Translate to Chinese:";
        case "translate chinese word to english":       return "Translate to English:";
        case "transcribe word to pinyin":               return "Write the pinyin (with tones) for:";
        default:                                        return "Answer the question:";
    }
};

export default function Question({
    currentQuestionObj,
    currentIndex,
    totalQuestions,
    sessionType,
    debugMode,
    userAnswer,
    setUserAnswer,
    answerState,
    lastUserAnswer,
    onSubmit,
    onNext,
    onMarkCorrect,
    onPlayAudio,
    debug,
}) {
    const showReplayButton =
        currentQuestionObj.question_type !== "fill in the blank" &&
        (hasChinese(currentQuestionObj.question) || isListeningQuestion(currentQuestionObj.question_type));
    const isListening = isListeningQuestion(currentQuestionObj.question_type);
    const hasAnswered = answerState !== null;
    const isWrong = answerState === 'incorrect';

    const [correctPinyin, setCorrectPinyin] = useState("");

    useEffect(() => {
        if (isWrong && isListening) {
            fetch('/api/pinyin', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text: currentQuestionObj.answer }),
            })
                .then(r => r.json())
                .then(d => setCorrectPinyin(d.pinyin))
                .catch(() => setCorrectPinyin(""));
        } else {
            setCorrectPinyin("");
        }
    }, [isWrong]);

    return (
        <div className="session-view">
            <p>Question {currentIndex + 1} of {totalQuestions}</p>
            {currentQuestionObj.unit != null && <p>Unit {currentQuestionObj.unit}</p>}
            {sessionType === "unit_test" && <p>Unit Test</p>}
            {debugMode && <p>⚡ Debug mode</p>}
            <h2>{questionTypeToInstruction(currentQuestionObj.question_type)}</h2>

            {!isListening && (
                <h1 className={currentQuestionObj.question.length > 12 ? "question-text question-text--long" : "question-text"}>
                    <ClickableText text={currentQuestionObj.question} tags={currentQuestionObj.tags || []} isUnitTest={sessionType === "unit_test"} />
                </h1>
            )}
            
            {showReplayButton && (
                <>
                    <button type="button" onClick={() => onPlayAudio(currentQuestionObj.question)}>🔊 Replay</button>
                    <button type="button" onClick={() => onPlayAudio(currentQuestionObj.question, true)}>🐢 Slow</button>
                </>
            )}

            {hasAnswered && (
                <div>
                    {isWrong ? (
                        <>
                            <p>You answered: <strong>{lastUserAnswer}</strong></p>
                            <p>Correct answer: <strong>{currentQuestionObj.answer}</strong></p>
                            {isListening && correctPinyin && <p>Pinyin: <strong>{correctPinyin}</strong></p>}
                        </>
                    ) : (
                        <p style={{ color: 'green' }}>✓ Correct!</p>
                    )}
                    {TYPES_MISSING_CHINESE.has(currentQuestionObj.question_type) && currentQuestionObj.hanzi && (
                        <p>Characters: <strong>{currentQuestionObj.hanzi}</strong></p>
                    )}
                    {TYPES_MISSING_ENGLISH.has(currentQuestionObj.question_type) && currentQuestionObj.english && (
                        <p>Translation: <strong>{currentQuestionObj.english}</strong></p>
                    )}
                    <button type="button" onClick={onNext}>Next</button>
                </div>
            )}

            {!hasAnswered && (
                <form onSubmit={onSubmit}>
                    {needsIME(currentQuestionObj.question_type)
                        ? <ChineseIMEInput value={userAnswer} onChange={(val) => setUserAnswer(val)} autoFocus />
                        : <input value={userAnswer} onChange={(e) => setUserAnswer(e.target.value)} autoFocus />
                    }
                    <button type="submit">Submit</button>
                </form>
            )}

            {debug && !hasAnswered && (
                <button type="button" onClick={onMarkCorrect}>✓ Mark correct (debug)</button>
            )}
        </div>
    );
}