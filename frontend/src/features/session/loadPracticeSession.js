import { generateSession } from './api/practice';
import { isQuestionEnabled } from '../settings';

// Fetches a new session from the server, dropping question types the user's
// settings have turned off. Returns null if the server responded with an error.
export const loadPracticeSession = async (skipReview, settings) => {
    const data = await generateSession(skipReview);
    if (!data) return null;
    return {
        questions: data.question_set.filter(q => isQuestionEnabled(q.question_type, settings)),
        sessionType: data.session_type,
    };
};
