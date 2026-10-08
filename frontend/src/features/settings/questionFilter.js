// Deep import on purpose: questionHelpers is a dependency-free leaf, and going
// through session's index would make settings <-> session a circular import.
import { isSpeakingQuestion, isListeningType } from '../session/questionHelpers';

// False if the user's settings have turned this question type off.
export const isQuestionEnabled = (questionType, settings) => {
    if (settings.disableSpeaking && isSpeakingQuestion(questionType)) return false;
    if (settings.disableListening && isListeningType(questionType)) return false;
    return true;
};
