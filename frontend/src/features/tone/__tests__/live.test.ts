import { describe, expect, it } from 'vitest';
import { LiveTracker } from '../analysis/live';
import { analyzeTone, voicedPitchesHz } from '../analysis/analyze';
import { estimateSpeakerRange } from '../analysis/normalize';
import { TONE_CONFIG } from '../config';
import { FEMALE, MALE, synth } from './synth';

function feed(tracker: LiveTracker, frames: ReturnType<typeof synth>) {
  for (const f of frames) {
    tracker.push(f);
    if (tracker.phase === 'finished') break;
  }
  return tracker;
}

describe('LiveTracker', () => {
  it('moves from pre-roll to listening once the noise sample is taken', () => {
    const tr = new LiveTracker(1, null);
    const frames = synth({ shape: [5, 5] });
    frames.slice(0, 29).forEach((f) => tr.push(f));
    expect(tr.phase).toBe('preroll');
    expect(tr.push(frames[30])).toBe(true);
    expect(tr.phase).toBe('listening');
    expect(tr.floorDb).toBeLessThan(-55);
  });

  it('stops ~silenceMs after the voice ends, and the frames it kept analyze correctly', () => {
    const frames = synth({ shape: [3, 2.7, 3.5, 5], trailingSilenceMs: 2000 });
    const tr = feed(new LiveTracker(2, MALE), frames);
    expect(tr.finishReason).toBe('silence');
    const voiceEnd = 300 + 60 + 450; // pre-roll + consonant + vowel
    const stoppedAt = tr.frames.at(-1)!.t;
    expect(stoppedAt - voiceEnd).toBeGreaterThanOrEqual(TONE_CONFIG.endpoint.silenceMs - 20);
    expect(stoppedAt - voiceEnd).toBeLessThanOrEqual(TONE_CONFIG.endpoint.silenceMs + 30);

    const r = analyzeTone({ frames: tr.frames, expectedTone: 2, speakerRange: MALE });
    expect(r.ok && r.isCorrect).toBe(true);
  });

  it('does not cut off a tone 3 that goes creaky at the end', () => {
    const frames = synth({ shape: [2.2, 1.4, 1.1, 1], creakTailMs: 200, trailingSilenceMs: 2000 });
    const tr = feed(new LiveTracker(3, MALE), frames);
    expect(tr.finishReason).toBe('silence');
    expect(tr.frames.at(-1)!.t).toBeGreaterThan(300 + 60 + 450 + 200);
  });

  it('gives up when nobody speaks', () => {
    const frames = synth({ shape: [5], voicedMs: 0, consonantMs: 0, trailingSilenceMs: 6000 });
    const tr = feed(new LiveTracker(1, null), frames);
    expect(tr.finishReason).toBe('noVoice');
    expect(analyzeTone({ frames: tr.frames, expectedTone: 1 }).ok).toBe(false);
  });

  it('uncalibrated: anchors the live trace so it starts where the target starts', () => {
    const tr = feed(new LiveTracker(4, null), synth({ shape: [5, 1], range: FEMALE, trailingSilenceMs: 1000 }));
    expect(tr.range).not.toBeNull();
    const first = tr.points[2].st;
    const level = 1 + (4 * (first - tr.range!.lowSt)) / (tr.range!.highSt - tr.range!.lowSt);
    expect(level).toBeGreaterThan(4.3);
  });
});

describe('calibration from four takes', () => {
  it('recovers the speaker range from mā má mǎ mà', () => {
    for (const range of [MALE, FEMALE]) {
      const takes = [[5, 5], [3, 2.8, 5], [2, 1, 1, 3.5], [5, 1]].map((shape, i) => synth({ shape, range, seed: i + 1 }));
      const est = estimateSpeakerRange(takes.flatMap((f) => voicedPitchesHz(f)))!;
      // within ~1.5 semitones of the true range ends
      expect(Math.abs(12 * Math.log2(est.lowHz / range.lowHz))).toBeLessThan(1.5);
      expect(Math.abs(12 * Math.log2(est.highHz / range.highHz))).toBeLessThan(1.5);
    }
  });
});
