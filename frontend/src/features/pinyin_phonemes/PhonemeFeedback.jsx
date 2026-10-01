import { displayFinal, displaySyllable } from './displayPinyin';
import styles from './PhonemeFeedback.module.css';

/** One line of feedback for one syllable. */
function syllableMessage(s) {
  const expected = displaySyllable(s.expected_initial, s.expected_final);
  if (s.initial_ok && s.final_ok) return `✓ Sounds right: ${expected}`;
  if (s.heard == null) return `Couldn't make out "${expected}" — try again a little louder`;

  const heard = displaySyllable(s.heard_initial, s.heard_final);
  if (!s.initial_ok && s.final_ok) {
    return s.heard_initial
      ? `Your "${s.expected_initial}" sounded like "${s.heard_initial}" (heard ${heard})`
      : `Missing the "${s.expected_initial}" at the start (heard ${heard})`;
  }
  if (s.initial_ok && !s.final_ok) {
    return `Your "${displayFinal(s.expected_final)}" sounded like "${displayFinal(s.heard_final)}" (heard ${heard})`;
  }
  return `That sounded like "${heard}" instead of "${expected}"`;
}

/**
 * Props:
 *  - status: usePhonemeCheck().status
 *  - result: usePhonemeCheck().result
 */
export function PhonemeFeedback({ status, result, className }) {
  if (status === 'idle') return null;

  return (
    <div className={[styles.root, className].filter(Boolean).join(' ')} aria-live="polite">
      {(status === 'recording' || status === 'checking') && (
        <span className={styles.note}>Checking your sounds…</span>
      )}

      {status === 'error' && (
        <span className={styles.note}>Couldn't check your sounds this time — graded on tone only.</span>
      )}

      {status === 'done' &&
        result.syllables.map((s, i) => {
          const ok = s.initial_ok && s.final_ok;
          return (
            <span key={i} className={`${styles.line} ${ok ? styles.good : styles.off}`}>
              {syllableMessage(s)}
            </span>
          );
        })}
    </div>
  );
}
