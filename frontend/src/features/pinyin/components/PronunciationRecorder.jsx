import { usePronunciationRecorder } from '../hooks/usePronunciationRecorder';
import { toDiacriticPinyin, toScoreTarget } from '../pinyinHelpers';
import { playSrc } from '../../audio';
import styles from './PronunciationRecorder.module.css';

const PART_LABELS = { initial: 'Initial', final: 'Final', tone: 'Tone' };

// The model passes a part when the target gets at least 5% of its probability
// (DEFAULT_THRESHOLD in scorer_core.py), so a pass can still have another sound as the
// top guess. That's a pass, but worth pointing out.
const isBorderline = (part) => part.correct && !part.forgiven && part.heard_unit !== part.target_unit;

// One row per part of the syllable: what was asked for, and what the model heard if it differed.
function PartRow({ name, part, debug }) {
    if (name === 'initial' && part.target_unit === '_') return null; // zero-initial syllable ("an", "wo")
    const show = (v) => (name === 'tone' ? `tone ${v}` : v);
    let verdict = <span className={styles.good}>✓</span>;
    if (!part.correct) verdict = <span className={styles.off}>sounded like {show(part.heard)}</span>;
    else if (isBorderline(part)) verdict = <span className={styles.close}>✓ close, but a bit like {show(part.heard)}</span>;
    return (
        <li className={styles.part}>
            <span className={styles.partLabel}>{PART_LABELS[name]}</span>
            <span className={styles.partTarget}>{show(part.target)}</span>
            {verdict}
            {debug && <span className={styles.score}>p={part.score.toFixed(2)}</span>}
        </li>
    );
}

/**
 * Records the learner and grades each syllable with the pronunciation model
 * (initial, final and tone). Pass `syllables`, e.g. [{ syllable: 'cao', tone: 3 }, { syllable: 'mei', tone: 2 }];
 * a word is said in one take and split into syllables on the server. `spokenTone` grades a syllable
 * against a different tone than the written one (3-3 sandhi: written ni3 hao3, said ni2 hao3).
 */
export default function PronunciationRecorder({ syllables, onResult, debug = false }) {
    const targets = syllables.map((p) => toScoreTarget({ syllable: p.syllable, tone: p.spokenTone ?? p.tone }));
    const rec = usePronunciationRecorder(targets, onResult);
    const { status, responses, word, message, recordingURL } = rec;

    const multi = syllables.length > 1;
    const busy = status === 'starting' || status === 'scoring';
    const sandhi = syllables.filter((p) => p.spokenTone != null && p.spokenTone !== p.tone);

    let buttonLabel = status === 'done' ? '🎙 Try again' : '🎙 Record';
    if (status === 'starting') buttonLabel = 'Starting mic…';
    if (status === 'recording') buttonLabel = '⏹ Stop';
    if (status === 'scoring') buttonLabel = 'Checking…';

    return (
        <section className={styles.root}>
            {sandhi.length > 0 && (
                <span className={styles.note}>
                    Said as{' '}
                    <span lang="zh-Latn-pinyin">
                        {syllables.map((p) => toDiacriticPinyin(p.syllable, p.spokenTone ?? p.tone)).join(' ')}
                    </span>
                    {' '}— two 3rd tones in a row: the first one rises.
                </span>
            )}

            {responses.length > 0 && (
                <ul className={styles.results}>
                    {responses.map((r, i) => {
                        const borderline = Object.values(r.parts).some(isBorderline);
                        return (
                            <li key={i} className={styles.syllable}>
                                <span className={`${styles.verdict} ${r.understood ? styles.good : styles.off}`}>
                                    <span lang="zh-Latn-pinyin">{toDiacriticPinyin(syllables[i].syllable, syllables[i].tone)}</span>
                                    {!r.understood ? ' Not quite' : borderline ? ' ✓ Close enough' : ' ✓ Nice!'}
                                </span>
                                <ul className={styles.parts}>
                                    {Object.keys(PART_LABELS).map((name) => (
                                        <PartRow key={name} name={name} part={r.parts[name]} debug={debug} />
                                    ))}
                                </ul>
                            </li>
                        );
                    })}
                </ul>
            )}

            <div className={styles.feedback} aria-live="polite">
                {message && <span className={styles.error}>{message}</span>}
                {status === 'recording' && <span className={styles.live}>Speak now…</span>}
                {status === 'idle' && !message && (
                    <span className={styles.note}>
                        Press record and say the {multi ? 'whole word' : 'syllable'}.
                    </span>
                )}
            </div>

            <div className={styles.actions}>
                <button
                    type="button"
                    className={styles.button}
                    onClick={status === 'recording' ? rec.stop : rec.start}
                    disabled={busy}
                >
                    {buttonLabel}
                </button>
                {recordingURL && !busy && status !== 'recording' && (
                    <button type="button" className={styles.secondary} onClick={() => playSrc(recordingURL)}>
                        🎧 Hear yourself
                    </button>
                )}
            </div>

            {debug && responses.length > 0 && (
                <details className={styles.raw}>
                    <summary>
                        Raw model output
                        {word && ` · split at ${word.cuts.join(', ')} s${word.split_found ? '' : ' (even split, syllables not found)'}`}
                    </summary>
                    <pre>{JSON.stringify(word ?? responses, null, 2)}</pre>
                </details>
            )}
        </section>
    );
}
