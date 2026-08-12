import ChineseIMEInput from './ChineseIMEInput';
import { ClickableText } from './CharacterPopup';
import MultipleChoice from './MultipleChoice';
import { useState, useEffect } from 'react';
import CharacterDecomposition from './CharacterDecomposition'

const hasChinese = (str) => /[\u4e00-\u9fff]/.test(str);

const isListeningQuestion = (qt) =>
    qt === "listening vocab" || qt === "listening sentence";

const TYPES_MISSING_ENGLISH = new Set([
    "listening vocab",
    "listening sentence",
    "transcribe word to pinyin",
]);

const TYPES_MISSING_CHINESE = new Set(["listening vocab"]);

const CHARACTER_QUIZ_TYPES = new Set([
    "character_spot_difference",
    "character_pinyin_to_char",
    "radical_meaning",
]);

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
        case "transcribe hanzi to pinyin":              return "Write the pinyin for the character";
        case "character_spot_difference":               return "Spot the character:";
        case "character_pinyin_to_char":                return "Match pinyin to character:";
        case "radical_meaning":                         return "Identify the radical:";
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
    onPreloadAudio
}) {

    const showReplayButton =
        !CHARACTER_QUIZ_TYPES.has(currentQuestionObj.question_type) &&
        currentQuestionObj.question_type !== "fill in the blank" &&
        (hasChinese(currentQuestionObj.question) || isListeningQuestion(currentQuestionObj.question_type));

    const isListening = isListeningQuestion(currentQuestionObj.question_type);
    
    const isTranscriptionToPinyin = 
        currentQuestionObj.question_type === "transcribe word to pinyin" || 
        currentQuestionObj.question_type === "transcribe hanzi to pinyin";

    const hasAnswered = answerState !== null;
    const isWrong = answerState === 'incorrect';
    const isMultipleChoice = Array.isArray(currentQuestionObj.options) && currentQuestionObj.options.length > 0;

    const [correctPinyin, setCorrectPinyin] = useState("");
    const [decompositionData, setDecompositionData] = useState(null);
    const [sentenceTagMetadata, setSentenceTagMetadata] = useState({});

    // ── Grammar Tip UI state ──────────────────────────────────────
    const [isGrammarTipOpen, setIsGrammarTipOpen] = useState(false);

    // ── User Tip UI state ─────────────────────────────────────────
    const [showTipForm, setShowTipForm] = useState(false);
    const [tipKeyType, setTipKeyType] = useState("answer");
    const [tipDraft, setTipDraft] = useState("");
    const [tipSaveState, setTipSaveState] = useState(null); 

    // reset forms, sidebars, and decompositions whenever the question changes
    useEffect(() => {
        setShowTipForm(false);
        setTipKeyType("answer");
        setTipDraft("");
        setTipSaveState(null);
        setIsGrammarTipOpen(false);
        setDecompositionData(null);
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

    useEffect(() => {
        if (showReplayButton && onPreloadAudio) {
            onPreloadAudio(currentQuestionObj.question);
        }
    }, [currentQuestionObj]);

    useEffect(() => {
        if (hasAnswered && CHARACTER_QUIZ_TYPES.has(currentQuestionObj.question_type)) {
            const chars = [currentQuestionObj.answer];
            if (answerState === 'incorrect' && lastUserAnswer && lastUserAnswer !== currentQuestionObj.answer) {
                chars.push(lastUserAnswer);
            }
            const uniqueChars = Array.from(new Set(chars.join("").split(""))).join("");

            console.debug("[decomposition] fetching for:", uniqueChars, "from chars:", chars);

            fetch(`/api/characters/decompose?text=${encodeURIComponent(uniqueChars)}&recursive=false`)
                .then(res => res.json())
                .then(data => {
                    console.debug("[decomposition] API response:", data);
                    setDecompositionData(data);
                })
                .catch(err => console.error("Failed to fetch character decomposition", err));
        }
    }, [hasAnswered, currentQuestionObj, answerState, lastUserAnswer]);

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

    const renderChineseText = (text) => {
        if (!text || typeof text !== 'string') return text;
        return hasChinese(text) ? (
            <ClickableText 
                text={text} 
                tags={currentQuestionObj.tags || []} 
                tagMetadata={sentenceTagMetadata}
                isUnitTest={sessionType === "unit_test"} 
            />
        ) : (
            text
        );
    };

    const renderGrammarTip = (tip) => {
        if (!tip || !Array.isArray(tip.sections)) return null;

        return tip.sections.map((section, sIdx) => (
            <div key={sIdx} className="grammar-tip-section">
                <h4>{renderChineseText(section.title)}</h4>
                <p>{renderChineseText(section.body)}</p>

                {section.table && (
                    <table className="grammar-tip-table">
                        <thead>
                            <tr>
                                {section.table.headers.map((h, hIdx) => (
                                    <th key={hIdx}>{renderChineseText(h)}</th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {section.table.rows.map((row, rIdx) => (
                                <tr key={rIdx}>
                                    {row.map((cell, cIdx) => (
                                        <td key={cIdx}>{renderChineseText(cell)}</td>
                                    ))}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>
        ));
    };

    return (
        <div className="session-view">
            {/* ── GRAMMAR TIP SIDEBAR ── */}
            {isGrammarTipOpen && currentQuestionObj.grammar_tips?.length > 0 && (
                <div className="grammar-tip-sidebar">
                    <div className="grammar-tip-header">
                        <h3>Grammar Tip{currentQuestionObj.grammar_tips.length > 1 ? "s" : ""}</h3>
                        <button type="button" onClick={() => setIsGrammarTipOpen(false)}>Close</button>
                    </div>
                    <div className="grammar-tip-content">
                        {currentQuestionObj.grammar_tips.map((tip, i) => (
                            <div key={i} className="grammar-tip-entry">
                                {renderGrammarTip(tip)}
                                {i < currentQuestionObj.grammar_tips.length - 1 && <hr />}
                            </div>
                        ))}
                    </div>
                </div>
            )}
            
            <p>Question {currentIndex + 1} of {totalQuestions}</p>
            {currentQuestionObj.unit != null && <p>Unit {currentQuestionObj.unit}</p>}
            {sessionType === "unit_test" && <p>Unit Test</p>}
            {debugMode && <p>⚡ Debug mode</p>}
            
            <h2>{questionTypeToInstruction(currentQuestionObj.question_type)}</h2>
            
            {["translate english sentence to chinese", "translate english word to chinese", "fill in the blank"].includes(currentQuestionObj.question_type)
                && /\d/.test(currentQuestionObj.answer) && (
                <p className="digit-hint">Write numbers as digits (e.g. 50, not 五十)</p>
            )}

            {currentQuestionObj.grammar_tips?.length > 0 && (
                <button 
                    type="button" 
                    className="grammar-tip-toggle"
                    onClick={() => setIsGrammarTipOpen(true)}
                >
                    Show grammar tip{currentQuestionObj.grammar_tips.length > 1 ? "s" : ""}
                </button>
            )}

            {!isListening && (
                <h1 className={currentQuestionObj.question.length > 12 ? "question-text question-text--long" : "question-text"}>
                    {isTranscriptionToPinyin && !hasAnswered ? (
                        currentQuestionObj.question
                    ) : (
                        <ClickableText 
                            text={currentQuestionObj.question} 
                            tags={currentQuestionObj.tags || []} 
                            tagMetadata={sentenceTagMetadata}
                            isUnitTest={sessionType === "unit_test"} 
                        />
                    )}
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
                    {isMultipleChoice && (
                        <MultipleChoice 
                            options={currentQuestionObj.options}
                            userAnswer={lastUserAnswer}
                            setUserAnswer={() => {}}
                            hasAnswered={true}
                            correctAnswer={currentQuestionObj.answer}
                        />
                    )}

                    {isWrong ? (
                        <>
                            <p>You answered: <strong>{renderChineseText(lastUserAnswer)}</strong></p>
                            <p>Correct answer: <strong>{renderChineseText(currentQuestionObj.answer)}</strong></p>
                            
                            {isListening && correctPinyin && <p>Pinyin: <strong>{correctPinyin}</strong></p>}
                        </>
                    ) : (
                        <>
                            <p className="correct-text">✓ Correct!</p>
                            {hasChinese(lastUserAnswer || "") && (
                                <p>You answered: <strong>{renderChineseText(lastUserAnswer)}</strong></p>
                            )}
                        </>
                    )}

                    {decompositionData && decompositionData.length > 0 && (
                        <CharacterDecomposition data={decompositionData} />
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

                    <div className="tip-editor">
                        {!showTipForm ? (
                            <button type="button" onClick={() => setShowTipForm(true)}>
                                {currentQuestionObj.tip ? 'Edit tip' : '+ Add a tip'}
                            </button>
                        ) : (
                            <div className="tip-form-container">
                                <div>
                                    <label className="tip-radio-label">
                                        <input type="radio" checked={tipKeyType === "answer"} onChange={() => setTipKeyType("answer")} /> Tip about the answer
                                    </label>
                                    <label>
                                        <input type="radio" checked={tipKeyType === "question"} onChange={() => setTipKeyType("question")} /> Tip about the question
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
                                    <button type="button" onClick={() => setShowTipForm(false)} className="btn-cancel">Cancel</button>
                                    {tipSaveState === 'saved' && <span className="tip-status-success">Saved ✓</span>}
                                    {tipSaveState === 'error' && <span className="tip-status-error">Failed to save</span>}
                                </div>
                            </div>
                        )}
                    </div>

                    <button type="button" onClick={onNext} className="btn-next">Next</button>
                </div>
            )}

            {!hasAnswered && (
                <form onSubmit={onSubmit}>
                    {isMultipleChoice ? (
                        <MultipleChoice 
                            options={currentQuestionObj.options}
                            userAnswer={userAnswer}
                            setUserAnswer={setUserAnswer}
                            hasAnswered={false}
                        />
                    ) : needsIME(currentQuestionObj.question_type) ? (
                        <ChineseIMEInput value={userAnswer} onChange={(val) => setUserAnswer(val)} autoFocus disabled={isGrading} />
                    ) : (
                        <input value={userAnswer} onChange={(e) => setUserAnswer(e.target.value)} autoFocus disabled={isGrading} />
                    )}
                    
                    <button type="submit" disabled={isGrading || (isMultipleChoice && !userAnswer)} className="btn-submit">
                        {isGrading ? "Checking…" : "Submit"}
                    </button>
                </form>
            )}

            {debug && !hasAnswered && (
                <button type="button" onClick={onMarkCorrect} disabled={isGrading} className="btn-debug">✓ Mark correct (debug)</button>
            )}
        </div>
    );
}