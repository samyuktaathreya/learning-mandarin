import { useState, useEffect, useRef } from 'react';
import Header from '../../../shared/components/Header';
import Question from './Question';
import SpeakingQuestion from './SpeakingQuestion';
import Results from './Results';
import GrammarTipsSidebar from './GrammarTipsSidebar';
import { playAudio, preloadAudio, stopCurrentAudio, clearAudioCache, clearServerAudio } from '../../audio';
import { submitSession, checkAnswerRemotely } from '../api/practice';
import useSpeechRecorder from '../hooks/useSpeechRecorder';
import {
    isSpeakingQuestion,
    hasChinese,
    isListeningType,
    isExactMatch,
    gradePinyinAnswer,
    isSingleSyllableAnswer,
    getQuestionAudioMode,
    PINYIN_QUESTION_TYPES,
    PINYIN_PAIR_QUESTION_TYPES,
} from '../questionHelpers';
import { useSettings, isQuestionEnabled } from '../../settings';

// One practice session, from the first question to the results screen.
// Mount it with a fresh `key` per session so all per-session state starts clean.
//   session:    { questions, sessionType, debug } (from loadPracticeSession)
//   onExit:     called when the user leaves the session
//   onFinished: called after the answers have been submitted to the server
export default function PracticeSession({ session, onExit, onFinished }) {
    const { sessionType, debug: debugMode } = session;
    const [questions, setQuestions] = useState(session.questions);
    const [currentIndex, setCurrentIndex] = useState(0);
    const [userAnswer, setUserAnswer] = useState("");
    const [score, setScore] = useState(0);
    const [answerLog, setAnswerLog] = useState([]);
    const [lastUserAnswer, setLastUserAnswer] = useState("");
    const [answerState, setAnswerState] = useState(null);
    const [isGrading, setIsGrading] = useState(false);
    const [isGrammarTipOpen, setIsGrammarTipOpen] = useState(false);

    const advancingRef = useRef(false);
    const gradingRef = useRef(false);
    const questionTokenRef = useRef(0);
    const currentAudioRef = useRef(null);
    // Per-syllable results for the current two-syllable question ([bool, bool]),
    // sent with the answer so the backend can credit each syllable on its own.
    const syllableCorrectRef = useRef(null);

    const currentQuestionObj = questions[currentIndex] ?? null;
    const isSingleSyllable = currentQuestionObj ? isSingleSyllableAnswer(currentQuestionObj.answer) : false;

    const { settings } = useSettings();
    const {
        isRecording, isTranscribing, transcriptionResult, recordingURL,
        startRecording, stopRecording, resetRecording,
    } = useSpeechRecorder({
        questionObj: currentQuestionObj,
        isSingleSyllable,
        onTranscribed: () => { advancingRef.current = false; },
    });

    useEffect(() => { console.log('currentQuestionObj:', currentQuestionObj); }, [currentQuestionObj]);

    // Reset per-question state and handle the question's audio whenever the question changes.
    useEffect(() => {
        advancingRef.current = false;
        gradingRef.current = false;
        syllableCorrectRef.current = null;
        questionTokenRef.current += 1;
        stopCurrentAudio(currentAudioRef); // stop audio from the previous question immediately

        if (!currentQuestionObj) return;

        setIsGrading(false);
        resetRecording();
        setAnswerState(null);
        setIsGrammarTipOpen(false);
        setLastUserAnswer("");

        if (debugMode) {
            const timer = setTimeout(() => advanceQuestion(true), 300);
            return () => clearTimeout(timer);
        }

        const audioMode = getQuestionAudioMode(currentQuestionObj, sessionType);
        const isPinyinQuestion = PINYIN_QUESTION_TYPES.has(currentQuestionObj.question_type);
        if (audioMode === 'autoplay') {
            const slow = false;
            playAudio(currentQuestionObj.audio_text ?? currentQuestionObj.question, slow, currentAudioRef, questionTokenRef, questionTokenRef.current, currentQuestionObj.audio_pinyin, isPinyinQuestion);
        } else if (audioMode === 'preload') {
            // Review-session case: don't autoplay, but fetch now so revealAnswer's
            // playAudio call later is instant instead of waiting on the network.
            preloadAudio(currentQuestionObj.audio_text ?? currentQuestionObj.question, false, currentQuestionObj.audio_pinyin, isPinyinQuestion);
        }
    }, [currentIndex, questions]);

    // Toggling a setting mid-session drops the now-disabled questions that haven't been answered yet.
    useEffect(() => {
        if (currentIndex >= questions.length) return;
        const kept = questions.filter((q, i) => i < currentIndex || isQuestionEnabled(q.question_type, settings));
        if (kept.length === questions.length) return;
        setQuestions(kept);
        if (currentIndex >= kept.length) finishSession(answerLog);
    }, [settings]);

    // Enter advances once an answer has been revealed or a speaking answer transcribed.
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

    const finishSession = async (finalAnswerLog) => {
        try {
            await submitSession(finalAnswerLog, sessionType === "unit_test");
            await clearServerAudio();
            clearAudioCache();
            onFinished?.();
        } catch (error) { console.error("Failed to submit session", error); }
    };

    // syllableCorrect: optional [bool, ...] for a two-syllable question; falls
    // back to anything recorded when the answer was graded.
    const advanceQuestion = (wasCorrect, requeue = false, syllableCorrect = syllableCorrectRef.current) => {
        if (advancingRef.current) return;
        advancingRef.current = true;

        resetRecording();
        const questionData = syllableCorrect
            ? { ...currentQuestionObj, syllable_correct: syllableCorrect }
            : currentQuestionObj;
        const log = [...answerLog, { question_data: questionData, is_correct: wasCorrect }];
        setAnswerLog(log);
        if (wasCorrect) setScore(s => s + 1);
        if (requeue && !wasCorrect) setQuestions(prev => [...prev, currentQuestionObj]);

        const nextIndex = currentIndex + 1;
        if (nextIndex >= questions.length && !requeue) finishSession(log);
        setCurrentIndex(nextIndex);
        setUserAnswer("");
        setLastUserAnswer("");
        setAnswerState(null);
    };

    const revealAnswer = (correct, answerGiven) => {
        advancingRef.current = false;
        if (!correct) setLastUserAnswer(answerGiven);
        setAnswerState(correct ? 'correct' : 'incorrect');
        setUserAnswer("");

        if (sessionType === 'review_session' &&
            !isListeningType(currentQuestionObj.question_type) &&
            hasChinese(currentQuestionObj.question)) {
            playAudio(currentQuestionObj.audio_text ?? currentQuestionObj.question, false, currentAudioRef, questionTokenRef, questionTokenRef.current, currentQuestionObj.audio_pinyin, PINYIN_QUESTION_TYPES.has(currentQuestionObj.question_type));
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

        if (PINYIN_PAIR_QUESTION_TYPES.has(currentQuestionObj.question_type)) {
            const { isCorrect, syllableCorrect } = gradePinyinAnswer(userAnswer, currentQuestionObj);
            syllableCorrectRef.current = syllableCorrect;
            revealAnswer(isCorrect, userAnswer);
            return;
        }

        const questionAtSubmit = currentQuestionObj;
        const tokenAtSubmit = questionTokenRef.current;
        const answerAtSubmit = userAnswer;

        if (isExactMatch(answerAtSubmit, questionAtSubmit)) { revealAnswer(true); return; }

        gradingRef.current = true;
        setIsGrading(true);
        try {
            const correct = await checkAnswerRemotely(questionAtSubmit, answerAtSubmit);
            if (questionTokenRef.current !== tokenAtSubmit) return; // user moved on meanwhile
            revealAnswer(correct, answerAtSubmit);
        } finally {
            gradingRef.current = false;
            setIsGrading(false);
        }
    };

    if (questions.length === 0) return (
        <div className="website-page">
            <Header />
            <div className="session-view">
                <h2>All caught up</h2>
                <p>Nothing to practice here right now — check back later for review.</p>
                <button onClick={onExit}>Back</button>
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
                onBack={onExit}
            />
        </div>
    );

    const sharedQuestionProps = {
        currentQuestionObj,
        currentIndex,
        totalQuestions: questions.length,
        sessionType,
        onMarkCorrect: () => advanceQuestion(true),
        onPlayAudio: (text, slow, pinyin, preferRecording = false) =>
            playAudio(text, slow, undefined, undefined, undefined, pinyin, preferRecording),
        onPreloadAudio: (text, slow = false, pinyin = null, preferRecording = false) =>
            preloadAudio(text, slow, pinyin, preferRecording),
        onToggleGrammar: () => setIsGrammarTipOpen(!isGrammarTipOpen),
    };

    return (
        <div className="website-page">
            <Header />
            <div className="session-layout-wrapper">
                <div className="session-main-content">
                    {sessionType === "review_session" && (
                        <div className="session-banner" style={{ textAlign: 'center', opacity: 0.7, fontSize: '0.85rem' }}>
                            Review session · {questions.length - currentIndex} left
                        </div>
                    )}
                    {isSpeakingQuestion(currentQuestionObj.question_type)
                        ? <SpeakingQuestion
                            {...sharedQuestionProps}
                            isSingleSyllable={isSingleSyllable}
                            isRecording={isRecording}
                            isTranscribing={isTranscribing}
                            transcriptionResult={transcriptionResult}
                            recordingURL={recordingURL}
                            onStartRecording={startRecording}
                            onStopRecording={stopRecording}
                            onAdvanceQuestion={advanceQuestion}
                            onTryAgain={resetRecording}
                          />
                        : <Question
                            {...sharedQuestionProps}
                            debugMode={debugMode}
                            userAnswer={userAnswer}
                            setUserAnswer={setUserAnswer}
                            answerState={answerState}
                            lastUserAnswer={lastUserAnswer}
                            isGrading={isGrading}
                            onSubmit={handleSubmit}
                            onNext={handleNext}
                          />
                    }
                </div>

                {isGrammarTipOpen && (
                    <GrammarTipsSidebar
                        questionObj={currentQuestionObj}
                        onClose={() => setIsGrammarTipOpen(false)}
                    />
                )}
            </div>
        </div>
    );
}