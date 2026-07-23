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
    isGrading,
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

    // ── Tip UI state ──────────────────────────────────────────────
    const [showTipForm, setShowTipForm] = useState(false);
    const [tipKeyType, setTipKeyType] = useState("answer");
    const [tipDraft, setTipDraft] = useState("");
    const [tipSaveState, setTipSaveState] = useState(null); // null | 'saving' | 'saved' | 'error'

    // reset the tip form whenever the question changes
    useEffect(() => {
        setShowTipForm(false);
        setTipKeyType("answer");
        setTipDraft("");
        setTipSaveState(null);
    }, [currentQuestionObj]);

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
    }, [isWrong, isListening, currentQuestionObj.answer]);

    // Helper to wrap Chinese text with the clickable dictionary popup
    const renderChineseText = (text) => {
        if (!text || typeof text !== 'string') return text;
        return hasChinese(text) ? (
            <ClickableText 
                text={text} 
                tags={currentQuestionObj.tags || []} 
                isUnitTest={sessionType === "unit_test"} 
            />
        ) : (
            text
        );
    };

    return (
        <div className="session-view">
            <p>Question {currentIndex + 1} of {totalQuestions}</p>
            {currentQuestionObj.unit != null && <p>Unit {currentQuestionObj.unit}</p>}
            {sessionType === "unit_test" && <p>Unit Test</p>}
            {debugMode && <p>⚡ Debug mode</p>}
            <h2>{questionTypeToInstruction(currentQuestionObj.question_type)}</h2>
            {["translate english sentence to chinese", "translate english word to chinese", "fill in the blank"].includes(currentQuestionObj.question_type)
                && /\d/.test(currentQuestionObj.answer) && (
                <p className="digit-hint">Write numbers as digits (e.g. 50, not 五十)</p>
            )}

            {!isListening && (
                <h1 className={currentQuestionObj.question.length > 12 ? "question-text question-text--long" : "question-text"}>
                    <ClickableText text={currentQuestionObj.question} tags={currentQuestionObj.tags || []} isUnitTest={sessionType === "unit_test"} />
                </h1>
            )}

            {showReplayButton && (
                <>
                    <button type="button" onClick={() => onPlayAudio(currentQuestionObj.question)} disabled={isGrading}>🔊 Replay</button>
                    <button type="button" onClick={() => onPlayAudio(currentQuestionObj.question, true)} disabled={isGrading}>🐢 Slow</button>
                </>
            )}

            {hasAnswered && (
                <div>
                    {isWrong ? (
                        <>
                            <p>You answered: <strong>{renderChineseText(lastUserAnswer)}</strong></p>
                            <p>Correct answer: <strong>{renderChineseText(currentQuestionObj.answer)}</strong></p>
                            {isListening && correctPinyin && <p>Pinyin: <strong>{correctPinyin}</strong></p>}
                        </>
                    ) : (
                        <>
                            <p style={{ color: 'green' }}>✓ Correct!</p>
                            {/* Show the correct answer again if it's Chinese so they can inspect characters */}
                            {hasChinese(lastUserAnswer || "") && (
                                <p>You answered: <strong>{renderChineseText(lastUserAnswer)}</strong></p>
                            )}
                        </>
                    )}
                    
                    {TYPES_MISSING_CHINESE.has(currentQuestionObj.question_type) && currentQuestionObj.hanzi && (
                        <p>Characters: <strong>{renderChineseText(currentQuestionObj.hanzi)}</strong></p>
                    )}
                    {TYPES_MISSING_ENGLISH.has(currentQuestionObj.question_type) && currentQuestionObj.english && (
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
                                    placeholder="e.g. 他在哪儿呢 adds a softening/curious tone vs plain 他在哪儿"
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

                    <button type="button" onClick={onNext}>Next</button>
                </div>
            )}

            {!hasAnswered && (
                <form onSubmit={onSubmit}>
                    {needsIME(currentQuestionObj.question_type)
                        ? <ChineseIMEInput value={userAnswer} onChange={(val) => setUserAnswer(val)} autoFocus disabled={isGrading} />
                        : <input value={userAnswer} onChange={(e) => setUserAnswer(e.target.value)} autoFocus disabled={isGrading} />
                    }
                    <button type="submit" disabled={isGrading}>
                        {isGrading ? "Checking…" : "Submit"}
                    </button>
                </form>
            )}

            {debug && !hasAnswered && (
                <button type="button" onClick={onMarkCorrect} disabled={isGrading}>✓ Mark correct (debug)</button>
            )}
        </div>
    );
}