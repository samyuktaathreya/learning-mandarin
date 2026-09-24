import { API_BASE_URL } from '../config';
import { apiFetch } from './client';
import {
    GRADE_ENGLISH_TO_CHINESE_TYPES,
    TRANSLATE_TO_ENGLISH_TYPES,
} from '../utils/questionHelpers';

const postJson = (path, body, method = 'POST') =>
    apiFetch(`${API_BASE_URL}${path}`, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });

export const fetchProgress = async () => {
    const res = await apiFetch(`${API_BASE_URL}/api/progress/`);
    return res.json();
};

// Returns the session data, or null if the server responded with an error.
export const generateSession = async (skipReview = false) => {
    const url = `${API_BASE_URL}/api/generate_session/` + (skipReview ? '?skip_review=true' : '');
    const res = await apiFetch(url);
    if (!res.ok) return null;
    return res.json();
};

export const submitSession = (answerLog, isUnitTest) =>
    postJson('/api/submit_session/', {
        list_of_question_data: answerLog.map(e => e.question_data),
        is_correct: answerLog.map(e => e.is_correct),
        is_unit_test: isUnitTest,
    }, 'PATCH');

export const transcribeAudio = async (base64Audio, questionObj) => {
    const res = await postJson('/api/transcribe', {
        audio: base64Audio,
        expected: questionObj.answer,
        hanzi: questionObj.question,
        question_type: questionObj.question_type,
    });
    return res.json();
};

const gradeEnglishToChinese = async (questionObj, userAnswer) => {
    const res = await postJson('/api/grade_english_to_chinese', {
        user_answer: userAnswer,
        expected_answer: questionObj.answer,
        question_type: questionObj.question_type,
        question: questionObj.question,
    });
    const { is_correct } = await res.json();
    return is_correct;
};

const gradeChineseToEnglish = async (questionObj, userAnswer) => {
    const res = await postJson('/api/grade_chinese_to_english', {
        user_answer: userAnswer,
        question: questionObj.question,
    });
    const { is_correct } = await res.json();
    return is_correct;
};

// Asks the server to grade answers that didn't match exactly.
// Returns true only if a server grader accepted the answer; errors count as "not accepted".
export const checkAnswerRemotely = async (questionObj, userAnswer) => {
    const { question_type } = questionObj;

    if (GRADE_ENGLISH_TO_CHINESE_TYPES.has(question_type)) {
        try {
            if (await gradeEnglishToChinese(questionObj, userAnswer)) return true;
        } catch (err) { console.error("Chinese grading failed", err); }
    }

    if (TRANSLATE_TO_ENGLISH_TYPES.has(question_type)) {
        try {
            if (await gradeChineseToEnglish(questionObj, userAnswer)) return true;
        } catch (err) { console.error("Grading failed", err); }
    }

    return false;
};