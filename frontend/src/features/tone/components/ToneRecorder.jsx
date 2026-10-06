import { useToneRecorder } from '../hooks/useToneRecorder';
import { TONE_NAMES, TONE_SHORT_NAMES, withToneMark } from '../pinyin';
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

/**
 * One syllable: pass `syllable` and `expectedTone`.
 * A word: pass `syllables`, e.g. [{ syllable: 'cao', tone: 3 }, { syllable: 'mei', tone: 2 }].
 * Tone 5 (neutral) is shown but not graded. `spokenTone` grades a syllable
 * against a different tone than the written one (3-3 sandhi: written ni3 hao3,
 * said ni2 hao3). Word results come back as a ToneSequenceResult.
 */
export function ToneRecorder({
  syllable,
  expectedTone,
  syllables,
  speakerRange = null,
  onResult,
  onRequestCalibration,
  debugContext,
  className,
}) {
  const parts = syllables?.length ? syllables : [{ syllable, tone: expectedTone }];
  const multi = parts.length > 1;
  const spokenTones = parts.map((p) => p.spokenTone ?? p.tone);

  const debug = { debugLabel: parts.map((p) => p.syllable).join(' '), debugContext };
  const rec = useToneRecorder(
    multi
      ? { expectedTones: spokenTones, speakerRange, onResult, ...debug }
      : { expectedTone: spokenTones[0], speakerRange, onResult, ...debug },
  );
  const { status, result, micError } = rec;

  const onClick = () => (status === 'listening' ? rec.stop() : rec.start());
  const sandhi = parts.filter((p) => p.spokenTone != null && p.spokenTone !== p.tone);

  return (
    <section className={[styles.root, className].filter(Boolean).join(' ')}>
      <div className={styles.header}>
        <span className={styles.syllable} lang="zh-Latn-pinyin">
          {parts.map((p) => withToneMark(p.syllable, p.tone)).join(multi ? ' ' : '')}
        </span>
        <span className={styles.toneName}>
          {multi ? spokenTones.map((t) => TONE_SHORT_NAMES[t]).join(' · ') : TONE_NAMES[spokenTones[0]]}
        </span>
      </div>

      {sandhi.length > 0 && (
        <span className={styles.note}>
          Said as{' '}
          <span lang="zh-Latn-pinyin">{parts.map((p) => withToneMark(p.syllable, p.spokenTone ?? p.tone)).join(' ')}</span>
          {' '}— two 3rd tones in a row: the first one rises.
        </span>
      )}

      <div className={styles.graph}>
        <PitchCanvas
          expectedTone={spokenTones[0]}
          expectedTones={multi ? spokenTones : undefined}
          status={status}
          liveRef={rec.liveRef}
          result={result}
        />
        {status === 'preroll' && <span className={styles.phase}>Get ready…</span>}
        {status === 'listening' && <span className={`${styles.phase} ${styles.phaseLive}`}>Speak now</span>}
      </div>

      <div className={styles.feedback} aria-live="polite">
        {micError && <span className={styles.error}>{micError}</span>}

        {result && !result.ok && <span className={styles.error}>{result.message}</span>}

        {result?.ok && !multi && (
          <>
            <span className={`${styles.verdict} ${result.isCorrect ? styles.good : styles.off}`}>
              {result.isCorrect
                ? `✓ Nice — that's tone ${result.expectedTone}`
                : `That sounded like tone ${result.detectedTone}`}
              <span className={styles.score}>{result.score}/100</span>
            </span>
            {result.hint && <span className={styles.hint}>{result.hint}</span>}
            {result.isCorrect && result.expectedTone === 3 && result.voiceQuality === 'creaky' && (
              <span className={styles.note}>Creaky voice counts — that's how tone 3 often sounds.</span>
            )}
          </>
        )}

        {result?.ok && multi && (
          <>
            <span className={`${styles.verdict} ${result.isCorrect ? styles.good : styles.off}`}>
              {result.isCorrect ? '✓ Nice — every tone was right' : 'Not quite — check the tones below'}
              <span className={styles.score}>{result.score}/100</span>
            </span>
            <ul className={styles.syllableResults}>
              {result.syllables.map((r, i) => (
                <li key={i} className={styles.syllableResult}>
                  <span lang="zh-Latn-pinyin">{withToneMark(parts[i].syllable, parts[i].tone)}</span>
                  {r === null ? (
                    <span className={styles.note}>neutral tone, not graded</span>
                  ) : r.isCorrect ? (
                    <span className={styles.good}>✓ tone {r.expectedTone}</span>
                  ) : (
                    <span>
                      <span className={styles.off}>sounded like tone {r.detectedTone}</span>
                      {r.hint && <span className={styles.hint}> — {r.hint}</span>}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </>
        )}

        {result?.ok && !result.calibrated && (
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

        {status === 'idle' && !micError && (
          <span className={styles.note}>
            Press record, wait for “Speak now”, then say the {multi ? 'whole word' : 'syllable'}.
          </span>
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
