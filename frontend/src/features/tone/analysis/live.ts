import { TONE_CONFIG, TONE_TARGETS } from '../config';
import type { PitchFrame, SpeakerRange, Tone } from '../types';
import { hzToSt, median } from './math';
import { estimateNoiseFloor } from './noise';
import { speakerRangeToSt, type StRange } from './normalize';
import { hasEnergy, isPitched } from './segment';

export type LivePhase = 'preroll' | 'listening' | 'finished';
export type FinishReason = 'silence' | 'maxDuration' | 'noVoice' | 'manual';

export interface LivePoint {
  /** ms since the take started */
  t: number;
  /** lightly smoothed pitch in semitones */
  st: number;
}

/**
 * Frame-by-frame state for one take: measures the noise floor during pre-roll,
 * gates voiced frames, fixes octave jumps, and decides when to stop.
 * No React and no browser APIs, so it can be unit-tested with synthetic frames.
 */
export class LiveTracker {
  readonly frames: PitchFrame[] = [];
  readonly points: LivePoint[] = [];
  /** times (ms) of creaky frames: voice energy but no clean pitch (drawn as a strip at the bottom) */
  readonly creakTimes: number[] = [];
  phase: LivePhase = 'preroll';
  finishReason: FinishReason | null = null;
  floorDb: number | null = null;
  voiceStartMs: number | null = null;
  /** range used to place the live trace; null until anchored when uncalibrated */
  range: StRange | null;
  readonly calibrated: boolean;

  private voicedMs = 0;
  private lastEnergyMs = 0;
  private recentSt: number[] = [];
  private prevSt: number | null = null;
  private anchorBuffer: number[] = [];

  constructor(
    readonly expectedTone: Tone,
    speakerRange: SpeakerRange | null,
    readonly preRollMs: number = TONE_CONFIG.preRollMs,
  ) {
    this.calibrated = speakerRange !== null;
    this.range = speakerRange ? speakerRangeToSt(speakerRange) : null;
  }

  /** @returns true if the phase changed (so the UI knows to re-render) */
  push(frame: PitchFrame): boolean {
    if (this.phase === 'finished') return false;
    this.frames.push(frame);

    if (this.phase === 'preroll') {
      if (frame.t < this.preRollMs) return false;
      this.floorDb = estimateNoiseFloor(this.frames, this.preRollMs);
      this.lastEnergyMs = frame.t;
      this.phase = 'listening';
      return true;
    }

    const floor = this.floorDb!;
    const hop = TONE_CONFIG.detector.hopMs;
    const ep = TONE_CONFIG.endpoint;

    if (frame.rmsDb >= floor + TONE_CONFIG.gate.creakLevelAboveFloorDb) this.lastEnergyMs = frame.t;

    // "voice" for timing purposes includes creak: a fully raspy take must still
    // start the trace and end the take ~silenceMs after the user stops
    const strong = frame.rmsDb >= floor + TONE_CONFIG.gate.minLevelAboveFloorDb;
    const pitched = isPitched(frame, floor) && !this.isSubharmonic(frame.hz!);
    if (pitched || strong) {
      if (this.voiceStartMs === null) this.voiceStartMs = frame.t;
      this.voicedMs += hop;
    }
    if (pitched) this.addPoint(frame.t, hzToSt(frame.hz!));
    else if (this.voiceStartMs !== null && hasEnergy(frame, floor)) this.creakTimes.push(frame.t);

    const sinceListen = frame.t - this.preRollMs;
    if (this.voicedMs >= ep.minVoiceMs && frame.t - this.lastEnergyMs >= ep.silenceMs) {
      return this.finish('silence');
    }
    if (sinceListen >= ep.maxDurationMs) {
      return this.finish(this.voiceStartMs === null ? 'noVoice' : 'maxDuration');
    }
    if (this.voiceStartMs === null && sinceListen >= ep.noVoiceTimeoutMs) {
      return this.finish('noVoice');
    }
    return false;
  }

  finish(reason: FinishReason): boolean {
    if (this.phase === 'finished') return false;
    this.phase = 'finished';
    this.finishReason = reason;
    return true;
  }

  /** a reading far below the voice so far is a creak subharmonic, not pitch to draw */
  private isSubharmonic(hz: number): boolean {
    if (this.prevSt === null) return false;
    return hzToSt(hz) < this.prevSt - TONE_CONFIG.segment.creakBelowVoiceSt - 3 &&
      Math.abs(hzToSt(hz) - (this.prevSt - 12)) > TONE_CONFIG.segment.irregularSt;
  }

  private addPoint(t: number, st: number) {
    // octave fix against the previous frame
    if (this.prevSt !== null && Math.abs(st - this.prevSt) > TONE_CONFIG.contour.octaveJumpSt) {
      const fixed = [st - 12, st + 12].reduce((a, b) =>
        Math.abs(b - this.prevSt!) < Math.abs(a - this.prevSt!) ? b : a,
      );
      if (Math.abs(fixed - this.prevSt) < Math.abs(st - this.prevSt)) st = fixed;
    }
    this.prevSt = st;

    this.recentSt.push(st);
    if (this.recentSt.length > 3) this.recentSt.shift();
    this.points.push({ t, st: median(this.recentSt) });

    // uncalibrated: once a few frames are in, pin the start of the trace to the
    // start of the target shape, so the user sees their SHAPE against the band
    if (!this.range) {
      this.anchorBuffer.push(st);
      if (this.anchorBuffer.length >= TONE_CONFIG.display.anchorFrames) {
        const anchor = median(this.anchorBuffer);
        const span = TONE_CONFIG.range.fallbackSpanSt;
        const startLevel = TONE_TARGETS[this.expectedTone].points[0];
        const lowSt = anchor - ((startLevel - 1) / 4) * span;
        this.range = { lowSt, highSt: lowSt + span };
      }
    }
  }
}