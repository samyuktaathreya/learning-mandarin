/** Mandarin lexical tones (neutral tone is out of scope for now). */
export type Tone = 1 | 2 | 3 | 4;

/**
 * One analysis frame, produced every ~10 ms while recording.
 * The audio layer fills these in; the analysis layer only reads them.
 */
export interface PitchFrame {
  /** ms since recording started */
  t: number;
  /** detected fundamental frequency, or null if the detector returned nothing */
  hz: number | null;
  /** McLeod clarity, 0–1. How periodic (voice-like) the frame is. */
  clarity: number;
  /** RMS level of the frame in dBFS (0 = full scale, silence ≈ -90) */
  rmsDb: number;
  /** largest absolute sample value in the frame, 0–1 (used for clipping detection) */
  peak: number;
}

/** A user's comfortable pitch range, from calibration or past attempts. */
export interface SpeakerRange {
  lowHz: number;
  highHz: number;
}

export type ToneErrorCode =
  | 'TOO_NOISY'
  | 'NO_VOICE'
  | 'TOO_SHORT'
  | 'TOO_QUIET'
  | 'CLIPPING'
  | 'UNCLEAR';

/** A point on the time-normalized contour, ready to draw. */
export interface ContourPoint {
  /** 0 (start of syllable) → 1 (end) */
  x: number;
  /** pitch on the Chao scale, 1 (lowest) → 5 (highest); may slightly exceed the range */
  level: number;
}

/** Shape measurements the classifier works from. All pitch values are in semitones. */
export interface ToneFeatures {
  start: number;
  end: number;
  min: number;
  max: number;
  /** where the lowest point falls, 0–1 */
  minPos: number;
  span: number;
  /** end − start (positive = rose) */
  rise: number;
  /** start − end (positive = fell) */
  fall: number;
  /** how far pitch dropped before the lowest point */
  dip: number;
  /** how far pitch came back up after the lowest point */
  recover: number;
  /** drop in the first half minus drop in the second half (positive = fall levels off) */
  deceleration: number;
  /** fraction of the syllable that was creaky / untrackable at low pitch */
  creakRatio: number;
  /** Chao level at the start and on average; null when the speaker is uncalibrated */
  startLevel: number | null;
  meanLevel: number | null;
  /** Chao level of the lowest point; null when uncalibrated */
  minLevel: number | null;
}

export interface ToneSuccess {
  ok: true;
  expectedTone: Tone;
  detectedTone: Tone;
  isCorrect: boolean;
  /** 0–100: how well the attempt matched the expected tone */
  score: number;
  /** 0–1 plausibility of each tone given the contour's shape */
  toneScores: Record<Tone, number>;
  /** one-line coaching tip, null when the attempt was correct */
  hint: string | null;
  /** time-normalized contour for the "you vs. target" overlay */
  contour: ContourPoint[];
  /** where the syllable was found in the recording */
  segment: { startMs: number; endMs: number };
  /** false when no speaker range was available and a fallback was used */
  calibrated: boolean;
  /** 'creaky' when a large part of the syllable was raspy (counts toward tone 3) */
  voiceQuality: 'clear' | 'creaky';
  features: ToneFeatures;
}

export interface ToneFailure {
  ok: false;
  expectedTone: Tone;
  error: ToneErrorCode;
  message: string;
}

export type ToneResult = ToneSuccess | ToneFailure;

export interface AnalyzeInput {
  frames: PitchFrame[];
  expectedTone: Tone;
  /** omit or null if the user hasn't calibrated yet */
  speakerRange?: SpeakerRange | null;
  /** leading ms recorded before the user was prompted, used to measure background noise */
  preRollMs?: number;
  /** if given, filled with intermediate values and the reason the pipeline stopped (for debug logging) */
  trace?: ToneTrace;
}

/** Intermediate values from one analyzeTone run. Every field is optional: it stops where the pipeline stopped. */
export type ToneTrace = Record<string, unknown>;