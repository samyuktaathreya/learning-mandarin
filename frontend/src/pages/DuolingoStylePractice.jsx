import { useState, useEffect } from 'react';
import Header from '../Components/Header';
import ChineseIMEInput from '../Components/ChineseIMEInput';

const USER_ID = 1;

const clean = (str) => {
    return str
        .toLowerCase()
        .replace(/[.,\/#!$%\^&\*;:{}=\-_`~()。？！、，：；""'']/g, "")  // added Chinese punctuation
        .replace(/\bim\b/g, "i am")
        .replace(/\byoure\b/g, "you are")
        .replace(/\bhes\b/g, "he is")
        .replace(/\bshes\b/g, "she is")
        .replace(/\ba\b|\ban\b|\bthe\b/g, "")
        .replace(/\s+/g, " ")
        .trim();
};

const needsIME = (question_type) => [
    "translate english sentence to chinese",
    "translate english word to chinese",
    "fill in the blank"
].includes(question_type);

const hasChinese = (str) => /[\u4e00-\u9fff]/.test(str);

const questionTypeToInstruction = (question_type) => {
    switch (question_type) {
        case "fill in the blank":                       return "Fill in the blank:";
        case "listening vocab":                         return "Type the pinyin (with tones) for what you hear:";
        case "listening sentence":                      return "Translate what you hear to English:";
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

const isSpeakingQuestion = (question_type) =>
    question_type === "speaking vocab" || question_type === "speaking sentence";

const isListeningQuestion = (question_type) =>
    question_type === "listening vocab" || question_type === "listening sentence";

const playAudio = async (text) => {
    try {
        const response = await fetch('/api/audio', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text }),
        });
        const { audio } = await response.json();
        const audioEl = new Audio(`data:audio/mpeg;base64,${audio}`);
        audioEl.play();
    } catch (error) {
        console.error("Failed to play audio", error);
    }
};

export default function DuolingoStyleQuestions() {
    const [questions, setQuestions] = useState([]);
    const [currentIndex, setCurrentIndex] = useState(0);
    const [userAnswer, setUserAnswer] = useState("");
    const [isWrong, setIsWrong] = useState(false);
    const [isSessionStarted, setIsSessionStarted] = useState(false);
    const [isLoading, setIsLoading] = useState(false);
    const [score, setScore] = useState(0);
    const [answerLog, setAnswerLog] = useState([]);
    const [sessionType, setSessionType] = useState("practice_session");

    const currentQuestionObj = questions[currentIndex] ?? null;

    // Auto-play audio when question changes
    useEffect(() => {
        if (!currentQuestionObj) return;

        const { question, question_type } = currentQuestionObj;
        const shouldAutoPlay =
            question_type !== "fill in the blank" &&
            (hasChinese(question) || isListeningQuestion(question_type));

        if (shouldAutoPlay) {
            playAudio(question);
        }
    }, [currentIndex, questions]);

    const startSession = async () => {
        setIsLoading(true);
        try {
            const response = await fetch(`/api/generate_session/${USER_ID}`);
            const data = await response.json();
            setQuestions(data.question_set);
            setSessionType(data.session_type);
            setCurrentIndex(0);
            setScore(0);
            setAnswerLog([]);
            setUserAnswer("");
            setIsWrong(false);
            setIsSessionStarted(true);
        } catch (error) {
            console.error("Failed to load questions", error);
        } finally {
            setIsLoading(false);
        }
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
        } catch (error) {
            console.error("Failed to submit session", error);
        }
    };

    const handleNewQuestion = (wasCorrect) => {
        const log = [
            ...answerLog,
            { question_data: currentQuestionObj, is_correct: wasCorrect }
        ];
        setAnswerLog(log);
        if (wasCorrect) setScore(s => s + 1);

        const nextIndex = currentIndex + 1;
        if (nextIndex >= questions.length) {
            submitSession(log);
        }
        setCurrentIndex(nextIndex);
        setUserAnswer("");
        setIsWrong(false);
    };

    const handleSubmit = (e) => {
        e.preventDefault();
        if (!currentQuestionObj) return;

        if (isSpeakingQuestion(currentQuestionObj.question_type)) {
            handleNewQuestion(true);
            return;
        }

        if (clean(userAnswer) === clean(currentQuestionObj.answer)) {
            handleNewQuestion(true);
        } else {
            setIsWrong(true);
        }
    };

    const handleSkip = () => handleNewQuestion(false);

    // ── Sub-components ──────────────────────────────────────────────

    function Menu() {
        return (
            <div>
                <button onClick={startSession}>Start Session</button>
            </div>
        );
    }

    function LoadingSpinner() {
        return <div>Loading questions...</div>;
    }

    function Question() {
        const speaking = isSpeakingQuestion(currentQuestionObj.question_type);
        const showReplayButton =
            currentQuestionObj.question_type !== "fill in the blank" &&
            (hasChinese(currentQuestionObj.question) || isListeningQuestion(currentQuestionObj.question_type));

        return (
            <div>
                <p>Question {currentIndex + 1} of {questions.length}</p>
                {sessionType === "unit_test" && <p>Unit Test</p>}
                <h2>{questionTypeToInstruction(currentQuestionObj.question_type)}</h2>
                <h1>{currentQuestionObj.question}</h1>

                {showReplayButton && (
                    <button type="button" onClick={() => playAudio(currentQuestionObj.question)}>
                        🔊 Replay
                    </button>
                )}

                <form onSubmit={handleSubmit}>
                    {!speaking && (
                        needsIME(currentQuestionObj.question_type)
                            ? <ChineseIMEInput
                                value={userAnswer}
                                onChange={(val) => { setUserAnswer(val); setIsWrong(false); }}
                                autoFocus
                            />
                            : <input
                                value={userAnswer}
                                onChange={(e) => { setUserAnswer(e.target.value); setIsWrong(false); }}
                                autoFocus
                            />
                    )}
                    <button type="submit">
                        {speaking ? "Done" : "Submit"}
                    </button>
                </form>

                {isWrong && (
                    <div>
                        <p style={{ color: "red" }}>Wrong! The answer was: {currentQuestionObj.answer}</p>
                    </div>
                )}

                <button type="button" onClick={handleSkip}>Skip</button>
            </div>
        );
    }

    function Results() {
        return (
            <div>
                <h1>Session Complete!</h1>
                <h2>Score: {score} / {questions.length}</h2>
                <p>
                    {sessionType === "unit_test"
                        ? "Unit test results submitted."
                        : "Practice session results saved."}
                </p>
                <button onClick={() => {
                    setIsSessionStarted(false);
                    setQuestions([]);
                }}>
                    Back to Menu
                </button>
            </div>
        );
    }

    // ── Render ──────────────────────────────────────────────────────

    const renderContent = () => {
        if (!isSessionStarted)                    return <Menu />;
        if (isLoading || questions.length === 0)  return <LoadingSpinner />;
        if (currentIndex >= questions.length)     return <Results />;
        return <Question />;
    };

    return (
        <div className="website-page">
            <Header />
            {renderContent()}
        </div>
    );
}