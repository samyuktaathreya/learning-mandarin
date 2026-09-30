import { useToneRecorder } from '../hooks/useToneRecorder';
import { TONE_NAMES, withToneMark } from '../pinyin';
import { PitchCanvas } from './PitchCanvas';
import styles from './ToneRecorder.module.css';

const BUTTON_LABEL = {
  idle: 'Record',
  starting: 'Starting mic…',
  preroll: 'Get ready…',
  listening: 'Stop',
  done: 'Try again',
  error: 'Try again',
};

export function ToneRecorder({
  syllable,
  expectedTone,
  speakerRange = null,
  onResult,
  onRequestCalibration,
  className,
}) {
  const rec = useToneRecorder({ expectedTone, speakerRange, onResult });
  const { status, result, micError } = rec;

  const onClick = () => (status === 'listening' ? rec.stop() : rec.start());

  return (
    <section className={[styles.root, className].filter(Boolean).join(' ')}>
      <div className={styles.header}>
        <span className={styles.syllable} lang="zh-Latn-pinyin">
          {withToneMark(syllable, expectedTone)}
        </span>
        <span className={styles.toneName}>{TONE_NAMES[expectedTone]}</span>
      </div>

      <div className={styles.graph}>
        <PitchCanvas expectedTone={expectedTone} status={status} liveRef={rec.liveRef} result={result} />
        {status === 'preroll' && <span className={styles.phase}>Get ready…</span>}
        {status === 'listening' && <span className={`${styles.phase} ${styles.phaseLive}`}>Speak now</span>}
      </div>

      <div className={styles.feedback} aria-live="polite">
        {micError && <span className={styles.error}>{micError}</span>}

        {result && !result.ok && <span className={styles.error}>{result.message}</span>}

        {result?.ok && (
          <>
            <span className={`${styles.verdict} ${result.isCorrect ? styles.good : styles.off}`}>
              {result.isCorrect
                ? `✓ Nice — that's tone ${expectedTone}`
                : `That sounded like tone ${result.detectedTone}`}
              <span className={styles.score}>{result.score}/100</span>
            </span>
            {result.hint && <span className={styles.hint}>{result.hint}</span>}
            {result.isCorrect && expectedTone === 3 && result.voiceQuality === 'creaky' && (
              <span className={styles.note}>Creaky voice counts — that's how tone 3 often sounds.</span>
            )}
            {!result.calibrated && (
              <span className={styles.note}>
                Judged on shape only.{' '}
                {onRequestCalibration ? (
                  <button type="button" className={styles.linkButton} onClick={onRequestCalibration}>
                    Calibrate your voice
                  </button>
                ) : (
                  'Calibrate your voice'
                )}{' '}
                for more accurate feedback.
              </span>
            )}
          </>
        )}

        {status === 'idle' && !micError && (
          <span className={styles.note}>Press record, wait for “Speak now”, then say the syllable.</span>
        )}
      </div>

      <button
        type="button"
        className={styles.button}
        onClick={onClick}
        disabled={status === 'starting' || status === 'preroll'}
      >
        {BUTTON_LABEL[status]}
      </button>
    </section>
  );
}