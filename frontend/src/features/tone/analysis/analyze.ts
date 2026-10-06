import { ERROR_MESSAGES, TONE_CONFIG } from '../config';
import type {
  AnalyzeInput,
  AnalyzeSequenceInput,
  PitchFrame,
  Tone,
  ToneErrorCode,
  ToneFailure,
  ToneFeatures,
  ToneResult,
  ToneSequenceFailure,
  ToneSequenceResult,
  ToneSuccess,
  ToneTrace,
} from '../types';
import { extractFeatures, scoreTones } from './classify';
import { clamp, fillGaps, hzToSt, median, medianFilter, resample, stToHz } from './math';
import { estimateNoiseFloor, estimateSpeechLevel, isClipping } from './noise';
import { correctOctaves, fallbackRange, speakerRangeToSt, toChaoLevel, type StRange } from './normalize';
import { buildHint, CREAK_HINTS, scoreAttempt } from './score';
import { classifyFrames, findVoicedSegment, findVoiceRun, frameHopMs, splitSyllables, type FrameKind } from './segment';

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

/** Recording-level problems, checked once per take. Returns the noise floor, or the error. */
function checkTake(
  frames: PitchFrame[],
  preRollMs: number,
  tr: ToneTrace = {},
): { floor: number } | { error: ToneErrorCode } {
  const q = TONE_CONFIG.quality;
  /** fail, recording which check stopped the pipeline */
  const bail = (reason: string, error: ToneErrorCode) => {
    tr.exit = reason;
    return { error };
  };
  if (frames.length < 5) return bail(`only ${frames.length} frames`, 'NO_VOICE');

  const floor = estimateNoiseFloor(frames, preRollMs);
  const speech = estimateSpeechLevel(frames.filter((f) => f.t >= preRollMs));
  const snr = speech - floor;
  Object.assign(tr, { floorDb: floor, speechDb: speech, snrDb: snr });

  if (snr < q.minVoiceAboveFloorDb) {
    return bail(`snr < minVoiceAboveFloorDb (${q.minVoiceAboveFloorDb})`, floor > q.noisyFloorDbfs ? 'TOO_NOISY' : 'NO_VOICE');
  }
  if (isClipping(frames)) return bail('clipping', 'CLIPPING');
  if (snr < q.minSnrDb) {
    return bail(`snr < minSnrDb (${q.minSnrDb})`, floor > q.noisyFloorDbfs ? 'TOO_NOISY' : 'TOO_QUIET');
  }
  return { floor };
}

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
  trace: tr = {},
}: AnalyzeInput): ToneResult {
  // 1. recording quality
  const take = checkTake(frames, preRollMs, tr);
  if ('error' in take) return fail(expectedTone, take.error);

  const stRange = speakerRange ? speakerRangeToSt(speakerRange) : null;
  const kinds = classifyFrames(frames, take.floor, stRange);
  Object.assign(tr, { stRange, kinds });
  const fromIdx = Math.max(0, frames.findIndex((f) => f.t >= preRollMs));
  return gradeSyllable(frames, kinds, take.floor, fromIdx, expectedTone, stRange, null, tr);
}

/**
 * Several syllables in one take ("cao3 mei2"): find the whole utterance,
 * split it into syllables, and grade each one on its own with the same
 * pipeline as analyzeTone. Neutral-tone syllables (5) are split off but not
 * graded. If any graded syllable can't be judged, the whole take fails with
 * that syllable's error, so the learner retries rather than getting half a verdict.
 */
export function analyzeTones({
  frames,
  expectedTones,
  speakerRange = null,
  preRollMs = TONE_CONFIG.preRollMs,
  trace: tr = {},
}: AnalyzeSequenceInput): ToneSequenceResult {
  const failSeq = (error: ToneErrorCode, syllableIndex: number | null = null, message = ERROR_MESSAGES[error]): ToneSequenceFailure =>
    ({ ok: false, expectedTones, error, message, syllableIndex });

  const take = checkTake(frames, preRollMs, tr);
  if ('error' in take) return failSeq(take.error);

  const stRange = speakerRange ? speakerRangeToSt(speakerRange) : null;
  const kinds = classifyFrames(frames, take.floor, stRange);
  Object.assign(tr, { stRange, kinds });
  const hop = frameHopMs(frames);
  const fromIdx = Math.max(0, frames.findIndex((f) => f.t >= preRollMs));

  // 2. the whole utterance: voice of any kind, pauses between syllables included
  const cfg = TONE_CONFIG.sequence;
  const utterance = findVoiceRun(kinds, hop, fromIdx, cfg.silenceMs);
  tr.utterance = utterance;
  if (!utterance) {
    tr.exit = 'no utterance';
    return failSeq('NO_VOICE');
  }
  // clean pitch only: an unvoiced consonant reads as creak and would pad this out
  if (utterance.pitchedCount * hop < expectedTones.length * cfg.minSyllableMs) {
    tr.exit = `pitched ${utterance.pitchedCount * hop}ms < ${expectedTones.length} × minSyllableMs (${cfg.minSyllableMs})`;
    return failSeq('MISSING_SYLLABLE');
  }

  // uncalibrated: draw every syllable against one range taken from the whole
  // utterance, so their heights stay comparable on the graph
  const pitchedSt = frames.flatMap((f, i) =>
    i >= utterance.startIdx && i <= utterance.endIdx && kinds[i] === 'pitched' ? [hzToSt(f.hz!)] : [],
  );
  const drawRange = stRange ?? (pitchedSt.length ? fallbackRange(pitchedSt) : null);

  // 3. split and grade each syllable on its own frames
  const windows = splitSyllables(frames, kinds, utterance.startIdx, utterance.endIdx, expectedTones.length);
  const syllableTraces: ToneTrace[] = [];
  Object.assign(tr, { windows, syllables: syllableTraces });
  const syllables: (ToneSuccess | null)[] = [];
  for (let i = 0; i < windows.length; i++) {
    const tone = expectedTones[i];
    if (tone === 5) {
      syllables.push(null);
      continue;
    }
    const [a, b] = windows[i];
    const syllableTrace: ToneTrace = {};
    syllableTraces[i] = syllableTrace;
    const r = gradeSyllable(frames.slice(a, b + 1), kinds.slice(a, b + 1), take.floor, 0, tone, stRange, drawRange, syllableTrace);
    if (!r.ok) {
      tr.exit = `syllable ${i + 1}: ${syllableTrace.exit ?? r.error}`;
      const which = expectedTones.length === 2 ? (i === 0 ? 'First syllable' : 'Second syllable') : `Syllable ${i + 1}`;
      return failSeq(r.error, i, `${which}: ${r.message}`);
    }
    syllables.push(r);
  }

  const graded = syllables.filter((r): r is ToneSuccess => r !== null);
  tr.exit = 'graded every syllable';
  return {
    ok: true,
    expectedTones,
    syllables,
    isCorrect: graded.every((r) => r.isCorrect),
    score: graded.length ? Math.round(graded.reduce((sum, r) => sum + r.score, 0) / graded.length) : 0,
    segment: { startMs: frames[utterance.startIdx].t, endMs: frames[utterance.endIdx].t + hop },
    calibrated: stRange !== null,
  };
}

/**
 * Grade one syllable. `frames`/`kinds` are either a whole take (with
 * `fromIdx` past the pre-roll) or one syllable's slice of it (`fromIdx` 0).
 * `drawRange` overrides the range the contour is drawn against when uncalibrated.
 */
function gradeSyllable(
  frames: PitchFrame[],
  kinds: FrameKind[],
  floor: number,
  fromIdx: number,
  expectedTone: Tone,
  stRange: StRange | null,
  drawRangeOverride: StRange | null,
  tr: ToneTrace = {},
): ToneResult {
  const q = TONE_CONFIG.quality;
  /** fail, recording which check stopped the pipeline */
  const bail = (reason: string, error: ToneErrorCode): ToneFailure => {
    tr.exit = reason;
    return fail(expectedTone, error);
  };

  // 2. find the syllable. If the pitch-based path can't find a usable
  // syllable, the take may have been mostly raspy: grade that as tone 3
  // before giving up.
  const voiceRun = findVoiceRun(kinds, frameHopMs(frames), fromIdx);
  tr.voiceRun = voiceRun;
  if (voiceRun) {
    const unstable = kinds.slice(voiceRun.startIdx, voiceRun.endIdx + 1).filter((k) => k === 'unstable').length;
    tr.unstableShare = unstable / (voiceRun.endIdx - voiceRun.startIdx + 1);
    if (unstable / (voiceRun.endIdx - voiceRun.startIdx + 1) > q.maxUnstableShare) {
      return bail(`unstableShare > maxUnstableShare (${q.maxUnstableShare})`, 'UNCLEAR');
    }
  }
  const orCreaky = (reason: string, error: ToneErrorCode): ToneResult => {
    tr.pitchPathExit = reason;
    const creaky = gradeCreakyTake(frames, kinds, floor, fromIdx, expectedTone, stRange, tr);
    if (creaky) tr.exit = 'graded as creaky take';
    return creaky ?? bail(`${reason}; creaky fallback rejected`, error);
  };

  const seg = findVoicedSegment(frames, floor, kinds);
  tr.segment = seg;
  if (!seg) return orCreaky('no voiced segment', 'UNCLEAR');
  if (seg.endMs - seg.startMs < q.minDurationMs[expectedTone]) {
    return orCreaky(`segment ${seg.endMs - seg.startMs}ms < minDurationMs (${q.minDurationMs[expectedTone]})`, 'TOO_SHORT');
  }

  const segFrames = frames.slice(seg.startIdx, seg.endIdx + 1);
  const tracked = (seg.pitchedCount + seg.creakCount) / segFrames.length;
  tr.trackedFraction = tracked;
  if (tracked < q.minTrackedFraction) return orCreaky(`trackedFraction < minTrackedFraction (${q.minTrackedFraction})`, 'UNCLEAR');

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
  Object.assign(tr, { rawSt, jitterSt: jitter });
  if (jitter > q.maxJitterSt) return orCreaky(`jitter > maxJitterSt (${q.maxJitterSt})`, 'UNCLEAR');

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
  Object.assign(tr, { contourSt, exit: 'graded on pitch contour' });

  const drawRange = stRange ?? drawRangeOverride ?? fallbackRange(contourSt);
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
    calibrated: stRange !== null,
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
  fromIdx: number,
  expectedTone: Tone,
  stRange: StRange | null,
  tr: ToneTrace = {},
): ToneSuccess | null {
  const c = TONE_CONFIG.creak;
  const reject = (reason: string) => {
    tr.creakRejected = reason;
    return null;
  };
  const hop = frameHopMs(frames);
  const run = findVoiceRun(kinds, hop, fromIdx);
  if (!run) return reject('no voice run');
  if (run.durationMs < c.minDurationMs) return reject(`voice run ${run.durationMs}ms < minDurationMs (${c.minDurationMs})`);

  const runFrames = frames.slice(run.startIdx, run.endIdx + 1);
  const runKinds = kinds.slice(run.startIdx, run.endIdx + 1);
  const share = run.creakCount / runFrames.length;
  tr.creakShare = share;
  if (share < c.minShare) return reject(`creak share < minShare (${c.minShare})`);

  // clearly voiced, not a breath or rustle near the noise floor
  const medianDb = median(runFrames.map((f) => f.rmsDb));
  if (medianDb < floor + TONE_CONFIG.gate.minLevelAboveFloorDb) {
    return reject(`median level ${medianDb.toFixed(1)}dB < floor + ${TONE_CONFIG.gate.minLevelAboveFloorDb}`);
  }

  // creak is slow, sharp pulsing: the detector reads it as clearly periodic and
  // LOW. Breath or whisper noise is aperiodic (low clarity, window-edge readings).
  const creakFrames = runFrames.filter((_, j) => runKinds[j] === 'creak');
  const periodicHz = creakFrames
    .filter((f) => f.hz !== null && f.hz >= c.minCreakHz && f.clarity >= c.minPeriodicClarity)
    .map((f) => f.hz!);
  tr.creakPeriodicShare = creakFrames.length ? periodicHz.length / creakFrames.length : 0;
  if (periodicHz.length < c.minPeriodicShare * creakFrames.length) {
    return reject(`periodic creak share < minPeriodicShare (${c.minPeriodicShare})`);
  }
  const maxHz = stRange ? stToHz(stRange.lowSt + c.maxStAboveLow) : c.maxHzUncalibrated;
  tr.creakMedianHz = median(periodicHz);
  if (median(periodicHz) > maxHz) return reject(`creak median Hz > max (${maxHz.toFixed(1)})`);

  // any clean pitch around it must also be low (only checkable when calibrated)
  const pitched = runFrames.flatMap((f, j) => (runKinds[j] === 'pitched' ? [{ j, st: hzToSt(f.hz!) }] : []));
  if (stRange && pitched.length >= 3 && toChaoLevel(median(pitched.map((p) => p.st)), stRange) > c.maxPitchedLevel) {
    return reject(`surrounding clean pitch above Chao ${c.maxPitchedLevel}`);
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