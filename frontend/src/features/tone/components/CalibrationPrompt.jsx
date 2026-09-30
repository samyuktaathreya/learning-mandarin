import { useRef, useState } from 'react';
import { voicedPitchesHz } from '../analysis/analyze';
import { estimateSpeakerRange } from '../analysis/normalize';
import { logToneEvent } from '../debug/toneLogger';
import { useToneRecorder } from '../hooks/useToneRecorder';
import { withToneMark } from '../pinyin';
import { PitchCanvas } from './PitchCanvas';
import styles from './ToneRecorder.module.css';
import '../../../App.css';

/**
 * Props:
 *  - onComplete: (range) => void
 *  - onCancel?: () => void
 *  - className?: string
 */

const STEPS = [1, 2, 3, 4];
/** below this, the four tones were too similar to learn a range from */
const MIN_RANGE_ST = 4;

/**
 * One-time setup: the user says mā má mǎ mà, and we learn the top and
 * bottom of their voice from all four takes combined.
 */
export function CalibrationPrompt({ onComplete, onCancel, className }) {
  const [step, setStep] = useState(0);
  const [problem, setProblem] = useState(null);
  const pitches = useRef([]);
  const tone = STEPS[step];

  const handleResult = (result, frames) => {
    if (!result.ok) return setProblem(result.message);
    const hz = voicedPitchesHz(frames);
    if (hz.length < 10) return setProblem('That was a bit short — try again.');
    setProblem(null);
    pitches.current[step] = hz;

    if (step < STEPS.length - 1) return setStep(step + 1);

    const range = estimateSpeakerRange(pitches.current.flat());
    logToneEvent('CALIBRATION', {
      range,
      spanSt: range ? 12 * Math.log2(range.highHz / range.lowHz) : null,
      pitchesPerTake: pitches.current.map((p) => p.length).join(' / '),
    });
    if (!range || 12 * Math.log2(range.highHz / range.lowHz) < MIN_RANGE_ST) {
      pitches.current = [];
      setStep(0);
      return setProblem('The four tones sounded very similar. Exaggerate them — high, rising, low, falling — and try again.');
    }
    onComplete(range);
  };

  const rec = useToneRecorder({
    expectedTone: tone,
    speakerRange: null,
    onResult: handleResult,
    debugLabel: `calibration ${withToneMark('ma', tone)}`,
  });
  const { status } = rec;

  return (
    <section className={[styles.root, className].filter(Boolean).join(' ')}>
      <div className={styles.header}>
        <span className={styles.syllable} lang="zh-Latn-pinyin">
          {withToneMark('ma', tone)}
        </span>
        <span className={styles.toneName}>Voice setup · {step + 1} of 4</span>
      </div>

      <div className={styles.steps} aria-hidden>
        {STEPS.map((t, i) => (
          <span
            key={t}
            className={[styles.step, i === step && styles.stepCurrent, i < step && styles.stepDone]
              .filter(Boolean)
              .join(' ')}
          >
            {withToneMark('ma', t)}
          </span>
        ))}
      </div>

      <div className={styles.graph}>
        <PitchCanvas expectedTone={tone} status={status} liveRef={rec.liveRef} result={null} className="pitch-canvas--compact" />
        {status === 'preroll' && <span className={styles.phase}>Get ready…</span>}
        {status === 'listening' && <span className={`${styles.phase} ${styles.phaseLive}`}>Speak now</span>}
      </div>

      <div className={styles.feedback} aria-live="polite">
        {rec.micError && <span className={styles.error}>{rec.micError}</span>}
        {problem && <span className={styles.error}>{problem}</span>}
        {!problem && !rec.micError && (
          <span className={styles.note}>
            Say each one clearly, using your whole range. This lets us judge how high or low your tones are.
          </span>
        )}
      </div>

      <div className="calibration-actions">
        <button
          type="button"
          className={styles.button}
          onClick={() => (status === 'listening' ? rec.stop() : rec.start())}
          disabled={status === 'starting' || status === 'preroll'}
        >
          {status === 'listening' ? 'Stop' : status === 'starting' || status === 'preroll' ? 'Get ready…' : `Record ${withToneMark('ma', tone)}`}
        </button>
        {onCancel && (
          <button type="button" className={styles.linkButton} onClick={onCancel}>
            Skip for now
          </button>
        )}
      </div>
    </section>
  );
}