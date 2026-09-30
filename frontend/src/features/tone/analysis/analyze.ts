import { ERROR_MESSAGES, TONE_CONFIG } from '../config';
import type {
  AnalyzeInput,
  PitchFrame,
  Tone,
  ToneErrorCode,
  ToneFailure,
  ToneFeatures,
  ToneResult,
  ToneSuccess,
} from '../types';
import { extractFeatures, scoreTones } from './classify';
import { clamp, fillGaps, hzToSt, median, medianFilter, resample, stToHz } from './math';
import { estimateNoiseFloor, estimateSpeechLevel, isClipping } from './noise';
import { correctOctaves, fallbackRange, speakerRangeToSt, toChaoLevel, type StRange } from './normalize';
import { buildHint, CREAK_HINTS, scoreAttempt } from './score';
import { classifyFrames, findVoicedSegment, findVoiceRun, frameHopMs, type FrameKind } from './segment';

/** above this share of creak, a take is reported as voiceQuality 'creaky' */
const CREAKY_REPORT_SHARE = 0.35;

/**
 * Trustworthy pitch values (Hz) from one take's syllable, octave-corrected.
 * Used by calibration to learn the speaker's range.
 */
export function voicedPitchesHz(
  frames: AnalyzeInput['frames'],
  preRollMs: number = TONE_CONFIG.preRollMs,
): number[] {
  const floor = estimateNoiseFloor(frames, preRollMs);
  const kinds = classifyFrames(frames, floor);
  const seg = findVoicedSegment(frames, floor, kinds);
  if (!seg) return [];
  const st = correctOctaves(
    frames
      .slice(seg.startIdx, seg.endIdx + 1)
      .map((f, j) => (kinds[seg.startIdx + j] === 'pitched' ? hzToSt(f.hz!) : null)),
  );
  return st.filter((v): v is number => v !== null).map((v) => 55 * 2 ** (v / 12));
}

const fail =(expectedTone: AnalyzeInput['expectedTone'], error: ToneErrorCode): ToneFailure => ({
  ok: false,
  expectedTone,
  error,
  message: ERROR_MESSAGES[error],
});

/**
 * Full pipeline: frames from one recording → tone result.
 * Pure and synchronous (well under 1 ms), so it runs on the main thread
 * the moment recording stops.
 */
export function analyzeTone({
  frames,
  expectedTone,
  speakerRange = null,
  preRollMs = TONE_CONFIG.preRollMs,
}: AnalyzeInput): ToneResult {
  const q = TONE_CONFIG.quality;
  if (frames.length < 5) return fail(expectedTone, 'NO_VOICE');

  // 1. recording quality
  const floor = estimateNoiseFloor(frames, preRollMs);
  const speech = estimateSpeechLevel(frames.filter((f) => f.t >= preRollMs));
  const snr = speech - floor;

  if (snr < q.minVoiceAboveFloorDb) {
    return fail(expectedTone, floor > q.noisyFloorDbfs ? 'TOO_NOISY' : 'NO_VOICE');
  }
  if (isClipping(frames)) return fail(expectedTone, 'CLIPPING');
  if (snr < q.minSnrDb) {
    return fail(expectedTone, floor > q.noisyFloorDbfs ? 'TOO_NOISY' : 'TOO_QUIET');
  }

  const calibrated = speakerRange !== null;
  const stRange = calibrated ? speakerRangeToSt(speakerRange) : null;

  // 2. label frames (clean pitch / creak / quiet) and find the syllable.
  // If the pitch-based path can't find a usable syllable, the take may have
  // been mostly raspy: grade that as tone 3 before giving up.
  const kinds = classifyFrames(frames, floor, stRange);
  const voiceRun = findVoiceRun(kinds, frameHopMs(frames), Math.max(0, frames.findIndex((f) => f.t >= preRollMs)));
  if (voiceRun) {
    const unstable = kinds.slice(voiceRun.startIdx, voiceRun.endIdx + 1).filter((k) => k === 'unstable').length;
    if (unstable / (voiceRun.endIdx - voiceRun.startIdx + 1) > q.maxUnstableShare) return fail(expectedTone, 'UNCLEAR');
  }
  const orCreaky = (error: ToneErrorCode): ToneResult =>
    gradeCreakyTake(frames, kinds, floor, preRollMs, expectedTone, stRange) ?? fail(expectedTone, error);

  const seg = findVoicedSegment(frames, floor, kinds);
  if (!seg) return orCreaky('UNCLEAR');
  if (seg.endMs - seg.startMs < q.minDurationMs[expectedTone]) return orCreaky('TOO_SHORT');

  const segFrames = frames.slice(seg.startIdx, seg.endIdx + 1);
  const tracked = (seg.pitchedCount + seg.creakCount) / segFrames.length;
  if (tracked < q.minTrackedFraction) return orCreaky('UNCLEAR');

  // 3. pitch → semitones, fix octave errors, check stability
  const rawSt = correctOctaves(
    segFrames.map((f, j) => (kinds[seg.startIdx + j] === 'pitched' ? hzToSt(f.hz!) : null)),
  );
  // jitter = how far each frame sits off the line through its neighbours
  const pitchedSt = rawSt.filter((v): v is number => v !== null);
  const jitter =
    pitchedSt.length >= 3
      ? median(pitchedSt.slice(1, -1).map((v, i) => Math.abs(v - (pitchedSt[i] + pitchedSt[i + 2]) / 2)))
      : 0;
  if (jitter > q.maxJitterSt) return orCreaky('UNCLEAR');

  // 4. smooth, trim the edges, resample to a fixed length
  const { trimFraction, points, medianWindow } = TONE_CONFIG.contour;
  const smooth = medianFilter(fillGaps(rawSt), medianWindow);
  const trim = Math.floor(smooth.length * trimFraction);
  const contourSt = resample(smooth.slice(trim, smooth.length - trim || undefined), points);

  // 5. classify
  const creakRatio = seg.creakCount / segFrames.length;
  const features = extractFeatures(contourSt, creakRatio, stRange);
  const toneScores = scoreTones(features);
  const { detectedTone, isCorrect, score } = scoreAttempt(expectedTone, toneScores);

  const drawRange = stRange ?? fallbackRange(contourSt);
  return {
    ok: true,
    expectedTone,
    detectedTone,
    isCorrect,
    score,
    toneScores,
    hint: buildHint(expectedTone, detectedTone, isCorrect, features),
    contour: contourSt.map((st, i) => ({
      x: i / (points - 1),
      level: toChaoLevel(st, drawRange),
    })),
    segment: { startMs: seg.startMs, endMs: seg.endMs },
    calibrated,
    voiceQuality: creakRatio >= CREAKY_REPORT_SHARE ? 'creaky' : 'clear',
    features,
  };
}

/**
 * A take that is mostly raspy/creaky, so there's no clean contour to judge.
 * Creaky voice at the bottom of the range IS tone 3 in natural speech, so it
 * is graded as tone 3, provided it really is low creak and not a breath,
 * rustle, or unstable higher voice. Returns null when it doesn't qualify.
 */
function gradeCreakyTake(
  frames: PitchFrame[],
  kinds: FrameKind[],
  floor: number,
  preRollMs: number,
  expectedTone: Tone,
  stRange: StRange | null,
): ToneSuccess | null {
  const c = TONE_CONFIG.creak;
  const hop = frameHopMs(frames);
  const fromIdx = Math.max(0, frames.findIndex((f) => f.t >= preRollMs));
  const run = findVoiceRun(kinds, hop, fromIdx);
  if (!run || run.durationMs < c.minDurationMs) return null;

  const runFrames = frames.slice(run.startIdx, run.endIdx + 1);
  const runKinds = kinds.slice(run.startIdx, run.endIdx + 1);
  const share = run.creakCount / runFrames.length;
  if (share < c.minShare) return null;

  // clearly voiced, not a breath or rustle near the noise floor
  if (median(runFrames.map((f) => f.rmsDb)) < floor + TONE_CONFIG.gate.minLevelAboveFloorDb) return null;

  // creak is slow, sharp pulsing: the detector reads it as clearly periodic and
  // LOW. Breath or whisper noise is aperiodic (low clarity, window-edge readings).
  const creakFrames = runFrames.filter((_, j) => runKinds[j] === 'creak');
  const periodicHz = creakFrames
    .filter((f) => f.hz !== null && f.hz >= c.minCreakHz && f.clarity >= c.minPeriodicClarity)
    .map((f) => f.hz!);
  if (periodicHz.length < c.minPeriodicShare * creakFrames.length) return null;
  const maxHz = stRange ? stToHz(stRange.lowSt + c.maxStAboveLow) : c.maxHzUncalibrated;
  if (median(periodicHz) > maxHz) return null;

  // any clean pitch around it must also be low (only checkable when calibrated)
  const pitched = runFrames.flatMap((f, j) => (runKinds[j] === 'pitched' ? [{ j, st: hzToSt(f.hz!) }] : []));
  if (stRange && pitched.length >= 3 && toChaoLevel(median(pitched.map((p) => p.st)), stRange) > c.maxPitchedLevel) {
    return null;
  }

  const strength = 0.8 + 0.15 * clamp((share - c.minShare) / (1 - c.minShare), 0, 1);
  const isCorrect = expectedTone === 3;
  const drawRange = stRange ?? (pitched.length ? fallbackRange(pitched.map((p) => p.st)) : null);
  const n = runFrames.length;

  return {
    ok: true,
    expectedTone,
    detectedTone: 3,
    isCorrect,
    score: isCorrect ? Math.round(100 * strength) : 0,
    toneScores: { 1: 0, 2: 0, 3: strength, 4: 0 },
    hint: isCorrect ? null : CREAK_HINTS[expectedTone],
    // whatever clean pitch there was, placed in time; creak itself has no pitch to draw
    contour:
      drawRange && pitched.length >= 3
        ? pitched.map((p) => ({ x: n > 1 ? p.j / (n - 1) : 0, level: toChaoLevel(p.st, drawRange) }))
        : [],
    segment: { startMs: frames[run.startIdx].t, endMs: frames[run.endIdx].t + hop },
    calibrated: stRange !== null,
    voiceQuality: 'creaky',
    features: creakOnlyFeatures(share),
  };
}

function creakOnlyFeatures(creakRatio: number): ToneFeatures {
  return {
    start: NaN, end: NaN, min: NaN, max: NaN, minPos: NaN, span: NaN, rise: NaN, fall: NaN,
    dip: NaN, recover: NaN, deceleration: NaN, creakRatio,
    startLevel: null, meanLevel: null, minLevel: null,
  };
}