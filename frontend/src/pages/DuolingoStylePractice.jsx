import { useState, useEffect, useRef } from 'react';
import Header from '../Components/Header';
import UnitSidebar from '../Components/UnitSidebar';
import UnitCenter from '../Components/UnitCenter';
import SessionControls from '../Components/PhaseTabs';
import Question from '../Components/Question';
import SpeakingQuestion from '../Components/SpeakingQuestion';
import Results from '../Components/Results';

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

const TRANSLATE_TO_ENGLISH_TYPES = new Set([
    "translate chinese word to english",
    "translate chinese sentence to english",
]);

const playAudio = async (text, slow = false) => {
    try {
        const response = await fetch('/api/audio', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text, slow }),
        });
        const { audio } = await response.json();
        new Audio(`data:audio/mpeg;base64,${audio}`).play();
    } catch (error) {
        console.error("Failed to play audio", error);
    }
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
    // null while the user is still answering; 'correct' / 'incorrect' once
    // they've submitted and are looking at the reveal (translation + Next)
    const [answerState, setAnswerState] = useState(null);

    // recording state
    const [isRecording, setIsRecording] = useState(false);
    const [isTranscribing, setIsTranscribing] = useState(false);
    const [transcriptionResult, setTranscriptionResult] = useState(null);
    const [recordingURL, setRecordingURL] = useState(null);
    const mediaRecorderRef = useRef(null);
    const audioChunksRef = useRef([]);
    // Re-entrancy lock: mashing Enter can fire advanceQuestion multiple times
    // before React commits the state reset, double-advancing and corrupting
    // answerLog/currentIndex. This ref blocks a second advance until the next
    // question's reveal re-arms it.
    const advancingRef = useRef(false);

    const currentQuestionObj = questions[currentIndex] ?? null;
    const isSingleSyllable = currentQuestionObj
        ? currentQuestionObj.answer.replace(/[^a-z0-9\u4e00-\u9fff]/gi, '').length <= 2
        : false;

    useEffect(() => { fetchProgress(); }, []);

    useEffect(() => {
        if (progress && selectedUnit === null) setSelectedUnit(progress.current_unit);
    }, [progress]);

    useEffect(() => {
        if (!currentQuestionObj) return;
        advancingRef.current = false;   // new question rendered -- re-arm
        setTranscriptionResult(null);
        setAnswerState(null);

        setLastUserAnswer("");
        if (recordingURL) { URL.revokeObjectURL(recordingURL); setRecordingURL(null); }
        
        if (debugMode) {
            advancingRef.current = false;   // debug auto-answers; no reveal to re-arm us
            const timer = setTimeout(() => advanceQuestion(true), 300);
            return () => clearTimeout(timer);
        }

        const { question, question_type } = currentQuestionObj;
        const isListening = question_type === "listening vocab" || question_type === "listening sentence";
        const shouldAutoPlay =
            question_type !== "fill in the blank" &&
            (/[\u4e00-\u9fff]/.test(question) || isListening);
        if (shouldAutoPlay) playAudio(question);
    }, [currentIndex, questions]);

    // Once an answer has been revealed (typed answer graded, or a speaking
    // question's pronunciation result came back), Enter also triggers Next.
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

    const startSession = async (debug = false) => {
        setIsLoading(true);
        setDebugMode(debug);
        try {
            const response = await fetch(`/api/generate_session/${USER_ID}`);
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
            fetchProgress();
        } catch (error) { console.error("Failed to submit session", error); }
    };

    const advanceQuestion = (wasCorrect, requeue = false) => {
        // block duplicate fires from mashed Enter / racing submit+listener
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

    // Reveals the outcome of a submitted answer (translation included) instead
    // of advancing straight away -- advancing now happens via the Next button.
    const revealAnswer = (correct, answerGiven) => {
        advancingRef.current = false;
        if (!correct) setLastUserAnswer(answerGiven);
        setAnswerState(correct ? 'correct' : 'incorrect');
        setUserAnswer("");
    };

    const handleNext = () => {
        if (advancingRef.current) return;
        if (answerState === 'incorrect') advanceQuestion(false, true);
        else if (answerState === 'correct') advanceQuestion(true);
    };

    const handleSubmit = async (e) => {
        e.preventDefault();
        if (!currentQuestionObj) return;
        if (advancingRef.current) return;

        // blank input is never correct -- fall through to the wrong path
        // rather than matching an answer variant that also cleans to empty
        if (!userAnswer || !userAnswer.trim()) { revealAnswer(false, "(no answer)"); return; }
        const question_type = currentQuestionObj.question_type;
        const expectedVariants = currentQuestionObj.answer.split(',').map(v => clean(v.trim()));
        if (expectedVariants.some(v => v === clean(userAnswer))) { revealAnswer(true); return; }

        // for listening sentence, compare by pinyin to handle homophones like 他/她
        if (question_type === "listening sentence") {
            try {
                const res = await fetch('/api/grade_english_to_chinese', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ user_answer: userAnswer, expected_answer: currentQuestionObj.answer }),
                });
                const { is_correct } = await res.json();
                if (is_correct) { revealAnswer(true); return; }
            } catch (err) { console.error("Chinese grading failed", err); }
        }

        if (TRANSLATE_TO_ENGLISH_TYPES.has(question_type)) {
            try {
                const res = await fetch('/api/grade_chinese_to_english', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        user_answer: userAnswer,
                        expected_answer: currentQuestionObj.answer,
                        question: currentQuestionObj.question,   // the Chinese
                    }),
                });
                const { is_correct } = await res.json();
                if (is_correct) { revealAnswer(true); return; }
            } catch (err) { console.error("Grading failed", err); }
        }

        revealAnswer(false, userAnswer);
    };

    // ── Recording ──────────────────────────────────────────────────

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
                        hanzi: currentQuestionObj.question,          // <-- add this
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

    // ── Render ──────────────────────────────────────────────────────

    if (isSessionStarted) {
        // still fetching
        if (isLoading) return <div className="website-page"><Header /><div>Loading...</div></div>;

        // fetch finished but the session came back empty -- nothing new to
        // learn and nothing due right now. Show a real message with a way out
        // instead of an indistinguishable "Loading..." that never resolves.
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
                        onSubmit={handleSubmit}
                        onNext={handleNext}
                        onMarkCorrect={() => advanceQuestion(true)}
                        onPlayAudio={playAudio}
                      />
                }
            </div>
        );
    }

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
                        <SessionControls
                            onStartSession={() => startSession(false)}
                            onDebug={() => startSession(true)}
                            disabled={isLoading}
                        />
                    )}
                    <UnitCenter
                        progress={progress}
                        selectedUnit={selectedUnit}
                        onStartSession={startSession}
                    />
                </div>
            </div>
        </div>
    );
}