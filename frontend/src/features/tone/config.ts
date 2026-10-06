import type { Tone, ToneErrorCode } from './types';

/**
 * Every tunable number in the tone pipeline lives here, so tuning against
 * real recordings never means hunting through the analysis code.
 *
 * Soft thresholds are written as { center, width } and fed to a sigmoid:
 * at `center` the score is 0.5, and it moves most of the way to 0 or 1
 * within about ±2×width. Wider = more forgiving.
 */
export const TONE_CONFIG = {
  /** detector settings (used by the audio layer) */
  detector: {
    windowSize: 2048,
    hopMs: 10,
    minHz: 60,
    maxHz: 500,
  },

  /** ms recorded before the "speak now" prompt, used as the noise sample */
  preRollMs: 300,

  /** band-pass applied before pitch detection */
  audio: {
    highpassHz: 70,
    lowpassHz: 1500,
  },

  /** when to stop listening automatically */
  endpoint: {
    /** stop this long after the voice goes quiet (the main source of feedback delay) */
    silenceMs: 350,
    /** need at least this much voice before silence can end the take (a fast tone 4 can be ~80 ms) */
    minVoiceMs: 70,
    /** hard cap on one take, measured from "speak now" */
    maxDurationMs: 3000,
    /** give up if nothing is heard for this long */
    noVoiceTimeoutMs: 4000,
  },

  /** live display */
  display: {
    /** width of the live time axis */
    windowMs: 1500,
    /** how long the target band is drawn during live recording */
    targetMs: 550,
    /** …per syllable, in a multi-syllable take (syllables in a word are shorter) */
    sequenceTargetMs: 320,
    /** frames used to anchor an uncalibrated live trace */
    anchorFrames: 5,
  },

  /** a frame counts as voice only if it passes all of these */
  gate: {
    minClarity: 0.8,
    minLevelAboveFloorDb: 10,
    /** quieter bar for creaky frames: energy present but pitch untrackable */
    creakLevelAboveFloorDb: 6,
  },

  segment: {
    /** pitch gaps shorter than this are bridged unconditionally */
    bridgeGapMs: 50,
    /** longer gaps are bridged only when they are low-pitched creak (tone 3) */
    creakGapMs: 400,
    creakTailMaxMs: 400,
    /** a "pitched" frame that jumps more than this from both neighbours (and off the local slope) is treated as creak */
    irregularSt: 1.5,
    /** fastest real pitch movement per 10 ms frame (a very fast tone 4 is ~2) */
    maxStepSt: 3,
    /** …and only counts as creak if it reads at least this far below the clean voice (else "unstable") */
    creakBelowVoiceSt: 3,
    /** runs of clean pitch shorter than this (frames) are treated as chance agreement inside creak */
    minRunFrames: 4,
    /** "low pitch" = at or below this percentile of the attempt's pitch */
    lowPitchPercentile: 40,
  },

  quality: {
    minSnrDb: 12,
    /** a noise floor louder than this is blamed on the room, not the speaker */
    noisyFloorDbfs: -50,
    /** loudest part must be at least this far above the floor to count as speech at all */
    minVoiceAboveFloorDb: 6,
    clipPeak: 0.99,
    clipMinFrames: 3,
    /** shortest clear voice we'll grade, per expected tone. Tone 4 is naturally the shortest tone. */
    minDurationMs: { 1: 150, 2: 150, 3: 150, 4: 80 } as Record<Tone, number>,
    /**
     * median distance of each frame from the straight line through its neighbours;
     * above this the tracker is struggling. (Measured against the local slope, so a
     * fast, steep tone 4 isn't mistaken for jitter.)
     */
    maxJitterSt: 1.0,
    /** fraction of the segment that must be pitched or creak */
    minTrackedFraction: 0.7,
    /** more than this share of erratic mid-height readings = tracker can't follow the voice */
    maxUnstableShare: 0.35,
  },

  /**
   * Raspy / creaky tone 3. When the pitch-based analysis can't find a usable
   * syllable, a take that is mostly creak is graded as tone 3.
   */
  creak: {
    minDurationMs: 200,
    /** at least this share of the voiced stretch must be creak */
    minShare: 0.5,
    /** confident pitch readings inside the creak must be this low (Hz) when uncalibrated… */
    maxHzUncalibrated: 110,
    /** …or at most this far above the bottom of a calibrated range (semitones) */
    maxStAboveLow: 2,
    /** clean pitch around the creak must sit at or below this Chao level (calibrated) */
    maxPitchedLevel: 3,
    /**
     * Creak is sharp, repeating pulses, so the detector reads it with HIGH clarity
     * on test audio, 31–38% of creak frames were clean pulses vs 0% for breath.
     * At least `minPeriodicShare` of creak frames must be that periodic.
     */
    minPeriodicClarity: 0.8,
    minPeriodicShare: 0.2,
    /**
     * On noise, the detector often reports ~22 Hz: the longest period a 2048-sample
     * window can hold (sampleRate / windowSize), not a real pitch. Ignore readings below this.
     */
    minCreakHz: 28,
  },

  contour: {
    /** trimmed from each end, where onset/offset glitches live */
    trimFraction: 0.12,
    points: 20,
    medianWindow: 5,
    /** a jump this large between adjacent frames (10 ms apart) is treated as an octave error */
    octaveJumpSt: 7,
  },

  range: {
    /** with no calibration, assume the attempt's median is mid-range with this span */
    fallbackSpanSt: 12,
    minSpanSt: 6,
  },

  classify: {
    t1: {
      flatSpan: { center: 2.0, width: 0.5 },
      highLevel: { center: 3.0, width: 0.4 },
    },
    t2: {
      rise: { center: 2.0, width: 0.7 },
      /** penalize only when the low point is BOTH late and deep (that's tone 3) */
      lateMin: { center: 0.45, width: 0.07 },
      deepDip: { center: 2.0, width: 0.5 },
    },
    t3: {
      midMinStart: { center: 0.3, width: 0.06 },
      midMinEnd: { center: 0.92, width: 0.03 },
      dip: { center: 1.0, width: 0.5 },
      recover: { center: 1.5, width: 0.5 },
      /** the low-falling "21" variant */
      fall: { center: 1.5, width: 0.5 },
      /** falls much bigger than this look like tone 4 instead */
      modestFall: { center: 5.0, width: 0.7 },
      deceleration: { center: 1.0, width: 0.5 },
      creak: { center: 0.12, width: 0.04 },
      /** creak plus a rise at the end is still tone 3 (the 214 rise); only a big rise looks like tone 2 */
      creakRise: { center: 4.5, width: 1.0 },
      creakFall: { center: 6.0, width: 1.0 },
      /** calibrated: creak only counts toward tone 3 if the pitch got low */
      creakLowMin: { center: 2.5, width: 0.4 },
      lowLevel: { center: 2.5, width: 0.35 },
      highStart: { center: 3.5, width: 0.4 },
    },
    t4: {
      fall: { center: 3.0, width: 0.8 },
      lateRecover: { center: 2.5, width: 0.6 },
      highStart: { center: 3.0, width: 0.4 },
    },
  },

  /** multi-syllable takes ("cao3 mei2") */
  sequence: {
    /**
     * Learners often pause between syllables, so a multi-syllable take waits
     * longer for silence before stopping. Gaps up to this long are also kept
     * inside the utterance rather than splitting it into two takes.
     */
    silenceMs: 600,
    maxDurationMs: 4000,
    /** each syllable needs at least this much voice, or one was probably left out */
    minSyllableMs: 120,
    /**
     * A boundary is searched for within ±this fraction of one syllable's share
     * around where it would fall if every syllable took the same time.
     */
    searchWindow: 0.4,
    /** a break in clean pitch at least this long marks a boundary (e.g. the "c" of "cao") */
    minBreakMs: 30,
    /** otherwise an energy dip at least this deep marks one (e.g. the "m" of "mama") */
    minValleyDb: 3,
    /** frames of RMS smoothing when looking for that dip */
    valleySmoothFrames: 5,
  },

  scoring: {
    /** if the expected tone is the top pick, it passes when it scores at least this */
    passThreshold: 0.4,
    /** leniency: pass even if another tone edged ahead by less than this… */
    leniencyMargin: 0.1,
    /** …as long as the expected tone still scored this well */
    leniencyMinScore: 0.6,
  },
} as const;

export const ERROR_MESSAGES: Record<ToneErrorCode, string> = {
  TOO_NOISY: 'Too much background noise — try somewhere quieter.',
  NO_VOICE: "We didn't hear you — try again.",
  TOO_SHORT: 'Hold the syllable a little longer.',
  TOO_QUIET: 'Speak a little louder or move closer to the mic.',
  CLIPPING: 'Too loud — move back from the mic a bit.',
  UNCLEAR: "Couldn't track your pitch clearly — try again.",
  MISSING_SYLLABLE: "We didn't hear every syllable — say the whole word.",
};

/**
 * Target shapes on the Chao scale, for drawing the tolerance band.
 * Points are evenly spaced in time. Tone 3's band covers both the dip-rise
 * form and the low-fall form, since both are accepted.
 */
export const TONE_TARGETS: Record<
  Tone,
  { points: number[]; tolerance: number; /** alternative accepted shape; the band covers both */ alt?: number[] }
> = {
  1: { points: [5, 5, 5], tolerance: 1.0 },
  2: { points: [3, 2.8, 3.6, 5], tolerance: 0.9 },
  3: { points: [2, 1.2, 1, 1.8, 3.5], alt: [2, 1.5, 1.2, 1, 1], tolerance: 1.0 },
  4: { points: [5, 4, 2.5, 1], tolerance: 0.9 },
};