import { isSpeakingQuestion, isListeningType } from '../../utils/questionHelpers';

// False if the user's settings have turned this question type off.
export const isQuestionEnabled = (questionType, settings) => {
    if (settings.disableSpeaking && isSpeakingQuestion(questionType)) return false;
    if (settings.disableListening && isListeningType(questionType)) return false;
    return true;
};
