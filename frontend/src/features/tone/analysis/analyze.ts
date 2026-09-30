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
  ToneTrace,
} from '../types';
import { extractFeatures, scoreTones } from './classify';
import { clamp, fillGaps, hzToSt, median, medianFilter, resample, stToHz } from './math';
import { estimateNoiseFloor, estimateSpeechLevel, isClipping } from './noise';
import { correctOctaves, fallbackRange, speakerRangeToSt, toChaoLevel, type StRange } from './normalize';
import { buildHint, CREAK_HINTS, scoreAttempt } from './score';
import { classifyFrames, findVoicedSegment, findVoiceRun, frameHopMs, type FrameKind } from './segment';

/** above this share of creak, a take is reported as voiceQuality 'creaky' */
const CREAKY_REPORT_SHARE = 0.35;
const TAIL_MIN_CLARITY = 0.45;
const TAIL_MAX_STEP_ST = 2.5; // per frame; a fast tone 4 drops ~2 st/frame

/**
 * Fill untracked frames (creak, dropouts) with readings that continue smoothly
 * from the clean pitch around them. In creak the tracker still gets some frames
 * right, or reads a harmonic (x2, x3). A reading (or hz/2, hz/3) is kept only if
 * it sits between the pitch on either side, with slack, and within a plausible
 * step of the last kept value. After the last clean frame it's treated as a
 * falling tail.
 */
function recoverGaps(
  segFrames: PitchFrame[],
  st: (number | null)[],
  floorDb: number,
  stRange: StRange | null,
): (number | null)[] {
  const out = [...st];
  const known = st.flatMap((v, i) => (v === null ? [] : [i]));
  if (!known.length) return out;
  // median of up to 3 clean values beside the gap, so one octave error can't throw it off
  const ref = (idxs: number[]) => median(idxs.map((k) => st[k] as number));

  const fill = (from: number, to: number, fromIdx: number, left: number, right: number | null) => {
    const lo = right === null ? (stRange ? stRange.lowSt - 3 : left - 12) : Math.min(left, right) - 3;
    const hi = right === null ? left + 1 : Math.max(left, right) + 3;
    let prevSt = left;
    let prevIdx = fromIdx;
    for (let i = from; i <= to; i++) {
      const f = segFrames[i];
      if (f.hz === null || f.clarity < TAIL_MIN_CLARITY) continue;
      if (f.rmsDb < floorDb + TONE_CONFIG.gate.minLevelAboveFloorDb) continue;
      const maxJump = Math.min(TAIL_MAX_STEP_ST * (i - prevIdx), 9);
      let best: number | null = null;
      for (const k of [1, 2, 3]) {
        const c = hzToSt(f.hz / k);
        if (c < lo || c > hi || Math.abs(c - prevSt) > maxJump) continue;
        if (best === null || Math.abs(c - prevSt) < Math.abs(best - prevSt)) best = c;
      }
      if (best !== null) {
        out[i] = best;
        prevSt = best;
        prevIdx = i;
      }
    }
  };

  for (let j = 0; j + 1 < known.length; j++) {
    const a = known[j];
    const b = known[j + 1];
    if (b - a <= 1) continue;
    fill(a + 1, b - 1, a, ref(known.slice(Math.max(0, j - 2), j + 1)), ref(known.slice(j + 1, j + 4)));
  }
  const last = known[known.length - 1];
  fill(last + 1, out.length - 1, last, ref(known.slice(-3)), null);
  return out;
}

/** drop leading/trailing nulls so fillGaps never extends a flat line */
function trimNulls<T>(xs: (T | null)[]): (T | null)[] {
  let s = 0;
  let e = xs.length - 1;
  while (s <= e && xs[s] === null) s++;
  while (e >= s && xs[e] === null) e--;
  return xs.slice(s, e + 1);
}

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
  trace: tr = {},
}: AnalyzeInput): ToneResult {
  const q = TONE_CONFIG.quality;
  /** fail, recording which check stopped the pipeline */
  const bail = (reason: string, error: ToneErrorCode): ToneFailure => {
    tr.exit = reason;
    return fail(expectedTone, error);
  };
  if (frames.length < 5) return bail(`only ${frames.length} frames`, 'NO_VOICE');

  // 1. recording quality
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

  const calibrated = speakerRange !== null;
  const stRange = calibrated ? speakerRangeToSt(speakerRange) : null;
  tr.stRange = stRange;

  // 2. label frames (clean pitch / creak / quiet) and find the syllable.
  // If the pitch-based path can't find a usable syllable, the take may have
  // been mostly raspy: grade that as tone 3 before giving up.
  const kinds = classifyFrames(frames, floor, stRange);
  tr.kinds = kinds;
  const voiceRun = findVoiceRun(kinds, frameHopMs(frames), Math.max(0, frames.findIndex((f) => f.t >= preRollMs)));
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
    const creaky = gradeCreakyTake(frames, kinds, floor, preRollMs, expectedTone, stRange, tr);
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
  const pitchedOnly = segFrames.map((f, j) => (kinds[seg.startIdx + j] === 'pitched' ? hzToSt(f.hz!) : null));
  const rawSt = correctOctaves(pitchedOnly);

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
  const recoveredSt = correctOctaves(recoverGaps(segFrames, pitchedOnly, floor, stRange));
  const smooth = medianFilter(fillGaps(trimNulls(recoveredSt)), medianWindow);
  const trim = Math.floor(smooth.length * trimFraction);
  const contourSt = resample(smooth.slice(trim, smooth.length - trim || undefined), points);

  // 5. classify
  const creakRatio = seg.creakCount / segFrames.length;
  const features = extractFeatures(contourSt, creakRatio, stRange);
  const toneScores = scoreTones(features);
  const { detectedTone, isCorrect, score } = scoreAttempt(expectedTone, toneScores);
  Object.assign(tr, { contourSt, exit: 'graded on pitch contour' });

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
  tr: ToneTrace = {},
): ToneSuccess | null {
  const c = TONE_CONFIG.creak;
  const reject = (reason: string) => {
    tr.creakRejected = reason;
    return null;
  };
  const hop = frameHopMs(frames);
  const fromIdx = Math.max(0, frames.findIndex((f) => f.t >= preRollMs));
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