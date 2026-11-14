import styles from './PronunciationHighlight.module.css';

// The target text, each character colored by how it was pronounced
// (status from scoreCharacters in speakingFeedback.js).
export default function PronunciationHighlight({ characters }) {
    if (!characters?.some((c) => c.status)) return null;
    return (
        <p className={styles.text}>
            {characters.map((c, i) => (
                <span key={i} className={c.status ? styles[c.status] : undefined}>
                    {c.char}
                </span>
            ))}
        </p>
    );
}
