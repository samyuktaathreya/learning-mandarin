// Public API of the tone feature. The rest of the app imports only from here.

// UI
export { ToneRecorder, type ToneRecorderProps } from './components/ToneRecorder';
export { CalibrationPrompt, type CalibrationPromptProps } from './components/CalibrationPrompt';
export { PitchCanvas, type PitchCanvasProps } from './components/PitchCanvas';

// hooks (for building your own UI)
export { useToneRecorder, type RecorderStatus, type UseToneRecorderOptions } from './hooks/useToneRecorder';
export { useSpeakerRange } from './hooks/useSpeakerRange';

// analysis (pure functions)
export { analyzeTone, voicedPitchesHz } from './analysis/analyze';
export { estimateSpeakerRange } from './analysis/normalize';

export { withToneMark, TONE_NAMES } from './pinyin';
export { TONE_CONFIG, TONE_TARGETS, ERROR_MESSAGES } from './config';
export type * from './types';
