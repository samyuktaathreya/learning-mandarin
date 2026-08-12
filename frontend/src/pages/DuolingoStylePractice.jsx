import { useState, useEffect, useRef } from 'react';
import Header from '../Components/Header';
import UnitSidebar from '../Components/UnitSidebar';
import UnitCenter from '../Components/UnitCenter';
import SessionControls from '../Components/PhaseTabs';
import Question from '../Components/Question';
import SpeakingQuestion from '../Components/SpeakingQuestion';
import Results from '../Components/Results';
import Modal from '../Components/Modal';
import ReviewCounter from '../Components/ReviewCounter';

const USER_ID = 1;

const clean = (str) => {
    return str
        .toLowerCase()
        .replace(/[.,\/#!$%\^&\*;:{}=\-_`~()。？！、，：；""'']/g, "")
        .replace(/([\u4e00-\u9fff])\s+([\u4e00-\u9fff])/g, "$1$2")
        .replace(/\bim\b/g, "i am")
        .replace(/\byoure\b/g, "you are")
        .replace(/\bhes\b/g, "he is")
        .replace(/\bshes\b/g, "she is")
        .replace(/\ba\b|\ban\b|\bthe\b/g, "")
        .replace(/\s+/g, " ")
        .trim();
};

const isSpeakingQuestion = (qt) => qt === "speaking vocab" || qt === "speaking sentence";
const hasChinese = (str) => /[\u4e00-\u9fff]/.test(str);
const isListeningType = (qt) => qt === "listening vocab" || qt === "listening sentence";

const TRANSLATE_TO_ENGLISH_TYPES = new Set([
    "translate chinese word to english",
    "translate chinese sentence to english",
]);

const GRADE_ENGLISH_TO_CHINESE_TYPES = new Set([
    "listening sentence",
    "translate english sentence to chinese",
    "translate english word to chinese",
]);

// ADDED: Set containing the character quiz types
const CHARACTER_QUIZ_TYPES = new Set([
    "character_spot_difference",
    "character_pinyin_to_char",
    "radical_meaning",
]);

// Cache of in-flight/resolved audio fetches, keyed by "text::slow"
const audioCache = new Map();

const fetchAudioData = (text, slow = false) => {
    const key = `${text}::${slow}`;
    if (audioCache.has(key)) return audioCache.get(key);

    const promise = fetch('/api/audio', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, slow }),
    })
        .then(res => res.json())
        .then(data => data.audio)
        .catch(err => {
            audioCache.delete(key); // don't cache a failure — allow retry
            throw err;
        });

    audioCache.set(key, promise);
    return promise;
};

// Fetches and caches audio without playing it.
const preloadAudio = (text, slow = false) => {
    if (!hasChinese(text)) return;
    fetchAudioData(text, slow).catch(err => console.error("Failed to preload audio", err));
};

const clearAudioCache = () => audioCache.clear();

const playAudio = async (text, slow = false, currentAudioRef = null, tokenRef = null, expectedToken = null) => {
    if (!hasChinese(text)) return;
    if (currentAudioRef?.current) {
        currentAudioRef.current.pause();
        currentAudioRef.current = null;
    }

    try {
        const audio = await fetchAudioData(text, slow); // instant if preloaded

        if (tokenRef && tokenRef.current !== expectedToken) return;

        if (currentAudioRef?.current) {
            currentAudioRef.current.pause();
        }

        const audioElement = new Audio(`data:audio/mpeg;base64,${audio}`);
        if (currentAudioRef) currentAudioRef.current = audioElement;

        return new Promise((resolve) => {
            audioElement.onended = () => {
                if (currentAudioRef?.current === audioElement) currentAudioRef.current = null;
                resolve();
            };
            audioElement.onerror = () => {
                if (currentAudioRef?.current === audioElement) currentAudioRef.current = null;
                resolve();
            };
            audioElement.play().catch(resolve);
        });
    } catch (error) {
        console.error("Failed to play audio", error);
    }
};

// Renders one structured grammar tip: { sections: [{ title, body, table }] }
// Logs to console if the shape looks wrong so it's obvious in devtools
// why a tip isn't showing content.
const renderGrammarTip = (tip, tipIndex) => {
    if (!tip) {
        console.warn(`[GrammarTip debug] tip at index ${tipIndex} is null/undefined`);
        return null;
    }
    if (typeof tip === 'string') {
        console.warn(
            `[GrammarTip debug] tip at index ${tipIndex} is a raw string, not the expected ` +
            `{ sections: [...] } object -- this data is stale (old markdown format). ` +
            `Re-run the pipeline or check the API response shape.`,
            tip
        );
        return <p style={{ color: '#c0392b' }}>⚠ Malformed grammar tip data (raw string, see console)</p>;
    }
    if (!Array.isArray(tip.sections)) {
        console.warn(`[GrammarTip debug] tip at index ${tipIndex} has no "sections" array:`, tip);
        return <p style={{ color: '#c0392b' }}>⚠ Malformed grammar tip data (see console)</p>;
    }
    if (tip.sections.length === 0) {
        console.warn(`[GrammarTip debug] tip at index ${tipIndex} has an empty "sections" array`);
    }

    return tip.sections.map((section, sIdx) => {
        if (!section || (!section.title && !section.body && !section.table)) {
            console.warn(`[GrammarTip debug] tip ${tipIndex}, section ${sIdx} is empty:`, section);
        }
        return (
            <div key={sIdx} style={{ marginBottom: '16px' }}>
                <h4 style={{ margin: '0 0 6px 0' }}>{section.title}</h4>
                <p style={{ whiteSpace: 'pre-wrap', margin: '0 0 10px 0' }}>{section.body}</p>

                {section.table && (
                    <table style={{ borderCollapse: 'collapse', width: '100%', margin: '8px 0' }}>
                        <thead>
                            <tr>
                                {section.table.headers.map((h, hIdx) => (
                                    <th
                                        key={hIdx}
                                        style={{
                                            border: '1px solid #ccc',
                                            padding: '8px',
                                            backgroundColor: '#f0f0f0',
                                            textAlign: 'left',
                                        }}
                                    >
                                        {h}
                                    </th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {section.table.rows.map((row, rIdx) => (
                                <tr key={rIdx}>
                                    {row.map((cell, cIdx) => (
                                        <td key={cIdx} style={{ border: '1px solid #ccc', padding: '8px' }}>
                                            {cell}
                                        </td>
                                    ))}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>
        );
    });
};

export default function DuolingoStyleQuestions() {
    const [questions, setQuestions] = useState([]);
    const [currentIndex, setCurrentIndex] = useState(0);
    const [userAnswer, setUserAnswer] = useState("");
    const [isSessionStarted, setIsSessionStarted] = useState(false);
    const [isLoading, setIsLoading] = useState(false);
    const [score, setScore] = useState(0);
    const [answerLog, setAnswerLog] = useState([]);
    const [sessionType, setSessionType] = useState("practice_session");
    const [debugMode, setDebugMode] = useState(false);
    const [progress, setProgress] = useState(null);
    const [selectedUnit, setSelectedUnit] = useState(null);
    const [lastUserAnswer, setLastUserAnswer] = useState("");
    const [answerState, setAnswerState] = useState(null);
    const [isGrading, setIsGrading] = useState(false);
    const [showSkipWarning, setShowSkipWarning] = useState(false);

    // ── Grammar Tip State ──
    const [isGrammarTipOpen, setIsGrammarTipOpen] = useState(false);

    const [isRecording, setIsRecording] = useState(false);
    const [isTranscribing, setIsTranscribing] = useState(false);
    const [transcriptionResult, setTranscriptionResult] = useState(null);
    const [recordingURL, setRecordingURL] = useState(null);
    const mediaRecorderRef = useRef(null);
    const audioChunksRef = useRef([]);
    const advancingRef = useRef(false);
    const gradingRef = useRef(false);
    const questionTokenRef = useRef(0);
    const currentAudioRef = useRef(null);

    const currentQuestionObj = questions[currentIndex] ?? null;
    useEffect(() => { console.log('currentQuestionObj:', currentQuestionObj); }, [currentQuestionObj]);
    const isSingleSyllable = currentQuestionObj
        ? currentQuestionObj.answer.replace(/[^a-z0-9\u4e00-\u9fff]/gi, '').length <= 2
        : false;

    useEffect(() => { fetchProgress(); }, []);

    useEffect(() => {
        if (progress && selectedUnit === null) setSelectedUnit(progress.current_unit);
    }, [progress]);

    // Debug: log grammar_tips shape whenever the sidebar is opened for a question
    useEffect(() => {
        if (isGrammarTipOpen && currentQuestionObj) {
            console.log(
                '[GrammarTip debug] grammar_tips for current question:',
                currentQuestionObj.grammar_tips
            );
        }
    }, [isGrammarTipOpen, currentQuestionObj]);

    useEffect(() => {
        advancingRef.current = false;
        gradingRef.current = false;
        questionTokenRef.current += 1;

        if (currentAudioRef.current) {
            currentAudioRef.current.pause();
            currentAudioRef.current = null;
        }

        if (!currentQuestionObj) return;
        advancingRef.current = false;
        gradingRef.current = false;
        questionTokenRef.current += 1;

        // Stop whatever was playing from the previous question, immediately.
        if (currentAudioRef.current) {
            currentAudioRef.current.pause();
            currentAudioRef.current = null;
        }

        setIsGrading(false);
        setTranscriptionResult(null);
        setAnswerState(null);
        setIsGrammarTipOpen(false);
        setLastUserAnswer("");
        if (recordingURL) { URL.revokeObjectURL(recordingURL); setRecordingURL(null); }

        if (debugMode) {
            const timer = setTimeout(() => advanceQuestion(true), 300);
            return () => clearTimeout(timer);
        }

        const { question, question_type } = currentQuestionObj;
        const isListening = question_type === "listening vocab" || question_type === "listening sentence";
        const isReview = sessionType === "review_session";

        // ADDED: !CHARACTER_QUIZ_TYPES.has(question_type)
        const shouldAutoPlay =
            question_type !== "fill in the blank" &&
            !isSpeakingQuestion(question_type) &&
            !CHARACTER_QUIZ_TYPES.has(question_type) &&
            (hasChinese(question) || isListening) &&
            (!isReview || isListening);

        if (shouldAutoPlay) {
            playAudio(question, false, currentAudioRef, questionTokenRef, questionTokenRef.current);
        } else if (
            hasChinese(question) &&
            question_type !== "fill in the blank" &&
            !isSpeakingQuestion(question_type) &&
            !CHARACTER_QUIZ_TYPES.has(question_type)
        ) {
            // Review-session case: don't autoplay, but fetch now so revealAnswer's
            // playAudio call later is instant instead of waiting on the network.
            preloadAudio(question);
        }
    }, [currentIndex, questions]);

    useEffect(() => {
        const speakingReady = transcriptionResult && !transcriptionResult.error && !transcriptionResult.hallucination;
        if (!answerState && !speakingReady) return;
        const onKeyDown = (e) => {
            if (e.key !== 'Enter') return;
            e.preventDefault();
            if (answerState === 'incorrect') advanceQuestion(false, true);
            else if (answerState === 'correct') advanceQuestion(true);
            else if (speakingReady) advanceQuestion(transcriptionResult.is_correct);
        };
        window.addEventListener('keydown', onKeyDown);
        return () => window.removeEventListener('keydown', onKeyDown);
    }, [answerState, transcriptionResult]);

    const fetchProgress = async () => {
        try {
            const res = await fetch(`/api/progress/${USER_ID}`);
            setProgress(await res.json());
        } catch (e) { console.error("Failed to fetch progress", e); }
    };

    const startSession = async (debug = false, skipReview = false) => {
        setIsLoading(true);
        setDebugMode(debug);
        try {
            const url = `/api/generate_session/${USER_ID}` + (skipReview ? '?skip_review=true' : '');
            const response = await fetch(url);
            if (!response.ok) {
                setIsLoading(false);
                return;
            }
            const data = await response.json();
            setQuestions(data.question_set);
            setSessionType(data.session_type);
            setCurrentIndex(0);
            setScore(0);
            setAnswerLog([]);
            setUserAnswer("");
            setAnswerState(null);
            setTranscriptionResult(null);
            setRecordingURL(null);
            setIsSessionStarted(true);
        } catch (error) { console.error("Failed to load questions", error); }
        finally { setIsLoading(false); }
    };

    const handleStart = () => startSession(false, false);
    const requestSkipReview = () => setShowSkipWarning(true);
    const confirmSkipReview = () => { setShowSkipWarning(false); startSession(false, true); };
    const cancelSkipReview = () => setShowSkipWarning(false);

    const submitSession = async (finalAnswerLog) => {
        try {
            await fetch(`/api/submit_session/${USER_ID}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    list_of_question_data: finalAnswerLog.map(e => e.question_data),
                    is_correct: finalAnswerLog.map(e => e.is_correct),
                    is_unit_test: sessionType === "unit_test",
                }),
            });
            await fetch('/api/audio/clear', { method: 'POST' });
            clearAudioCache();
            fetchProgress();
        } catch (error) { console.error("Failed to submit session", error); }
    };

    const advanceQuestion = (wasCorrect, requeue = false) => {
        if (advancingRef.current) return;
        advancingRef.current = true;

        if (recordingURL) { URL.revokeObjectURL(recordingURL); setRecordingURL(null); }
        const log = [...answerLog, { question_data: currentQuestionObj, is_correct: wasCorrect }];
        setAnswerLog(log);
        if (wasCorrect) setScore(s => s + 1);
        if (requeue && !wasCorrect) setQuestions(prev => [...prev, currentQuestionObj]);

        const nextIndex = currentIndex + 1;
        if (nextIndex >= questions.length && !requeue) submitSession(log);
        setCurrentIndex(nextIndex);
        setUserAnswer("");
        setLastUserAnswer("");
        setAnswerState(null);
        setTranscriptionResult(null);
    };

    const revealAnswer = (correct, answerGiven) => {
        advancingRef.current = false;
        if (!correct) setLastUserAnswer(answerGiven);
        setAnswerState(correct ? 'correct' : 'incorrect');
        setUserAnswer("");

        if (sessionType === 'review_session' &&
            !isListeningType(currentQuestionObj.question_type) &&
            hasChinese(currentQuestionObj.question)) {
            playAudio(currentQuestionObj.question, false, currentAudioRef, questionTokenRef, questionTokenRef.current)
        }
    };

    const handleNext = () => {
        if (advancingRef.current) return;
        if (answerState === 'incorrect') advanceQuestion(false, true);
        else if (answerState === 'correct') advanceQuestion(true);
    };

    const handleSubmit = async (e) => {
        e.preventDefault();
        if (!currentQuestionObj) return;
        if (advancingRef.current || gradingRef.current) return;

        if (!userAnswer || !userAnswer.trim()) { revealAnswer(false, "(no answer)"); return; }

        const questionAtSubmit = currentQuestionObj;
        const tokenAtSubmit = questionTokenRef.current;
        const answerAtSubmit = userAnswer;
        const isStale = () => questionTokenRef.current !== tokenAtSubmit;

        const question_type = questionAtSubmit.question_type;
        
        const isPinyinType = ["listening vocab", "transcribe word to pinyin", "transcribe hanzi to pinyin"].includes(question_type);
        const normalizeMatch = (str) => {
            const cleaned = clean(str.trim());
            return isPinyinType ? cleaned.replace(/\s+/g, "") : cleaned;
        };

        const expectedVariants = questionAtSubmit.answer.split(',').map(v => normalizeMatch(v));
        if (expectedVariants.some(v => v === normalizeMatch(answerAtSubmit))) { revealAnswer(true); return; }

        gradingRef.current = true;
        setIsGrading(true);
        try {
            if (GRADE_ENGLISH_TO_CHINESE_TYPES.has(question_type)) {
                try {
                    const res = await fetch('/api/grade_english_to_chinese', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            user_answer: answerAtSubmit,
                            expected_answer: questionAtSubmit.answer,
                            question_type: questionAtSubmit.question_type,
                            question: questionAtSubmit.question,
                        }),
                    });
                    const { is_correct } = await res.json();
                    if (isStale()) return;   
                    if (is_correct) { revealAnswer(true); return; }
                } catch (err) { console.error("Chinese grading failed", err); }
            }

            if (TRANSLATE_TO_ENGLISH_TYPES.has(question_type)) {
                try {
                    const res = await fetch('/api/grade_chinese_to_english', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            user_answer: answerAtSubmit,
                            question: questionAtSubmit.question,
                        }),
                    });
                    const { is_correct } = await res.json();
                    if (isStale()) return;   
                    if (is_correct) { revealAnswer(true); return; }
                } catch (err) { console.error("Grading failed", err); }
            }

            if (isStale()) return;
            revealAnswer(false, answerAtSubmit);
        } finally {
            gradingRef.current = false;
            setIsGrading(false);
        }
    };

    const startRecording = async () => {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            audioChunksRef.current = [];
            const mediaRecorder = new MediaRecorder(stream);
            mediaRecorderRef.current = mediaRecorder;
            mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) audioChunksRef.current.push(e.data); };
            mediaRecorder.onstop = async () => {
                stream.getTracks().forEach(t => t.stop());
                const blob = new Blob(audioChunksRef.current, { type: 'audio/webm' });
                if (recordingURL) URL.revokeObjectURL(recordingURL);
                setRecordingURL(URL.createObjectURL(blob));
                setIsTranscribing(false);
                if (!isSingleSyllable) await sendToAzure(blob);
            };
            mediaRecorder.start();
            setIsRecording(true);
        } catch (err) { console.error("Microphone access denied", err); }
    };

    const stopRecording = () => {
        mediaRecorderRef.current?.stop();
        setIsRecording(false);
        if (!isSingleSyllable) setIsTranscribing(true);
    };

    const sendToAzure = async (blob) => {
        try {
            const reader = new FileReader();
            reader.onloadend = async () => {
                const base64 = reader.result.split(',')[1];
                const res = await fetch('/api/transcribe', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        audio: base64,
                        expected: currentQuestionObj.answer,
                        hanzi: currentQuestionObj.question,          
                        question_type: currentQuestionObj.question_type,
                    }),
                });

                setTranscriptionResult(await res.json());
                advancingRef.current = false;
                setIsTranscribing(false);
            };
            reader.readAsDataURL(blob);
        } catch (err) { console.error("Transcription failed", err); setIsTranscribing(false); }
    };

    const handleTryAgain = () => {
        setTranscriptionResult(null);
        if (recordingURL) { URL.revokeObjectURL(recordingURL); setRecordingURL(null); }
    };

    if (isSessionStarted) {
        if (isLoading) return <div className="website-page"><Header /><div>Loading...</div></div>;

        if (questions.length === 0) return (
            <div className="website-page">
                <Header />
                <div className="session-view">
                    <h2>All caught up</h2>
                    <p>Nothing to practice here right now — check back later for review.</p>
                    <button onClick={() => { setIsSessionStarted(false); setDebugMode(false); }}>Back</button>
                </div>
            </div>
        );

        if (currentIndex >= questions.length) return (
            <div className="website-page">
                <Header />
                <Results
                    score={score}
                    questions={questions}
                    sessionType={sessionType}
                    onBack={() => { setIsSessionStarted(false); setQuestions([]); setDebugMode(false); }}
                />
            </div>
        );

        return (
            <div className="website-page">
                <Header />
                {/* ── SIDE-BY-SIDE LAYOUT ── */}
                <div className="session-layout-wrapper">
                    
                    {/* LEFT COLUMN: Main Question */}
                    <div className="session-main-content">
                        {sessionType === "review_session" && (
                            <div className="session-banner" style={{ textAlign: 'center', opacity: 0.7, fontSize: '0.85rem' }}>
                                Review session · {questions.length - currentIndex} left
                            </div>
                        )}
                        {isSpeakingQuestion(currentQuestionObj.question_type)
                            ? <SpeakingQuestion
                                currentQuestionObj={currentQuestionObj}
                                currentIndex={currentIndex}
                                totalQuestions={questions.length}
                                sessionType={sessionType}
                                isSingleSyllable={isSingleSyllable}
                                isRecording={isRecording}
                                isTranscribing={isTranscribing}
                                transcriptionResult={transcriptionResult}
                                recordingURL={recordingURL}
                                onStartRecording={startRecording}
                                onStopRecording={stopRecording}
                                onAdvanceQuestion={advanceQuestion}
                                onMarkCorrect={() => advanceQuestion(true)}
                                onTryAgain={handleTryAgain}
                                onPlayAudio={playAudio}
                                onToggleGrammar={() => setIsGrammarTipOpen(!isGrammarTipOpen)}
                              />
                            : <Question
                                currentQuestionObj={currentQuestionObj}
                                currentIndex={currentIndex}
                                totalQuestions={questions.length}
                                sessionType={sessionType}
                                debugMode={debugMode}
                                userAnswer={userAnswer}
                                setUserAnswer={setUserAnswer}
                                answerState={answerState}
                                lastUserAnswer={lastUserAnswer}
                                isGrading={isGrading}
                                onSubmit={handleSubmit}
                                onNext={handleNext}
                                onMarkCorrect={() => advanceQuestion(true)}
                                onPlayAudio={playAudio}
                                onToggleGrammar={() => setIsGrammarTipOpen(!isGrammarTipOpen)}
                              />
                        }
                    </div>

                    {/* RIGHT COLUMN: Grammar Tip */}
                    {isGrammarTipOpen && (
                        <div className="grammar-tip-sidebar">
                            <div className="grammar-tip-header">
                                <h3>Grammar Tips</h3>
                                <button type="button" onClick={() => setIsGrammarTipOpen(false)}>✕</button>
                            </div>
                            <div className="grammar-tip-content">

                                {/* ── VISIBLE DEBUG PANEL ── remove once working ── */}
                                <div style={{ background: '#1a1a2e', border: '1px solid #e74c3c', borderRadius: 6, padding: 12, marginBottom: 16, fontSize: 12, fontFamily: 'monospace', color: '#e74c3c' }}>
                                    <strong>DEBUG — raw question keys:</strong>
                                    <div style={{ color: '#f39c12', marginTop: 4 }}>
                                        {Object.keys(currentQuestionObj).join(', ')}
                                    </div>
                                    <strong style={{ marginTop: 8, display: 'block' }}>grammar_tip (singular):</strong>
                                    <div style={{ color: '#2ecc71', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                                        {JSON.stringify(currentQuestionObj.grammar_tip, null, 2)}
                                    </div>
                                    <strong style={{ marginTop: 8, display: 'block' }}>grammar_tips (plural):</strong>
                                    <div style={{ color: '#2ecc71', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                                        {JSON.stringify(currentQuestionObj.grammar_tips, null, 2)}
                                    </div>
                                </div>
                                {/* ── END DEBUG PANEL ── */}

                                {/* Render whichever field actually has data */}
                                {(() => {
                                    // Support both field names until we confirm which the API uses
                                    const tips = currentQuestionObj.grammar_tips ?? currentQuestionObj.grammar_tip;
                                    if (!tips || (Array.isArray(tips) && tips.length === 0)) {
                                        return <p style={{ opacity: 0.6 }}>No grammar tips for this question.</p>;
                                    }
                                    // Normalise: could be a single object, array of objects, or legacy string
                                    const tipArray = Array.isArray(tips) ? tips : [tips];
                                    return tipArray.map((tip, i) => (
                                        <div key={i} className="grammar-tip-entry">
                                            {renderGrammarTip(tip, i)}
                                            {i < tipArray.length - 1 && <hr />}
                                        </div>
                                    ));
                                })()}
                            </div>
                        </div>
                    )}
                </div>
            </div>
        );
    }

    const dueNow = progress?.review_due_word_count ?? 0;

    return (
        <div className="website-page">
            <Header />
            <div className="progress-layout">
                <UnitSidebar
                    progress={progress}
                    selectedUnit={selectedUnit}
                    onSelectUnit={setSelectedUnit}
                />
                <div className="unit-center-column">
                    {progress && selectedUnit === progress.current_unit && (
                        <>
                            <SessionControls
                                onStartSession={handleStart}
                                onDebug={() => startSession(true)}
                                disabled={isLoading}
                            />
                            {dueNow > 0 && (
                                <div className="skip-review-row" style={{ marginTop: '0.5rem' }}>
                                    <button onClick={requestSkipReview} disabled={isLoading}>
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

            <Modal
                open={showSkipWarning}
                onClose={cancelSkipReview}
                title="Skip today’s review?"
                actions={
                    <>
                        <button onClick={cancelSkipReview}>Do the review</button>
                        <button onClick={confirmSkipReview} style={{ color: '#c0392b' }}>
                            Skip anyway
                        </button>
                    </>
                }
            >
                You have <strong>{dueNow}</strong> word{dueNow === 1 ? '' : 's'} due for review.
                Skipping means you’ll likely forget them — spaced review is what moves words
                into long-term memory. Only skip if you already know this material cold.
            </Modal>
        </div>
    );
}