import { useState, useRef, useEffect } from 'react';
import { API_BASE_URL } from '../../shared/config';
import { apiFetch } from '../../shared/api/client';
import { playAudio } from '../../features/audio';

/**
 * Pinyin dictation, two modes:
 *   typing:  hear an HSK word, type its numbered pinyin (lv3xing2). Words
 *            from the chosen level and the one above.
 *   tapping: hear one syllable, tap the character that makes it, out of 4.
 *            Characters the user has only seen inside words (期 from 星期),
 *            from the chosen level and the one below.
 *
 * Missed words/characters are kept here and sent with every request so the
 * server can bring them back; one leaves the list once it's answered correctly.
 */

const LEVELS = [
  { value: 1, label: 'HSK 1' },
  { value: 2, label: 'HSK 2' },
  { value: 3, label: 'HSK 3' },
  { value: 4, label: 'HSK 4' },
  { value: 5, label: 'HSK 5' },
  { value: 6, label: 'HSK 6' },
  { value: 7, label: 'HSK 7-9' },
];

const MODES = [
  { value: 'typing', label: '⌨️ Typing', hint: 'Hear a word, type its pinyin with tone numbers.' },
  { value: 'tapping', label: '👆 Tapping', hint: 'Hear a syllable, tap the character that makes it.' },
];

const RECENT_KEEP = 5; // how many recent items to send, so the server doesn't repeat them right away

const postJson = async (path, body) => {
  const res = await apiFetch(`${API_BASE_URL}${path}`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(data?.detail || `Request failed (${res.status})`);
  return data;
};

export default function PinyinPractice() {
  const [mode, setMode] = useState('typing');
  const [level, setLevel] = useState(null);
  const [word, setWord] = useState(null);         // typing mode
  const [question, setQuestion] = useState(null); // tapping mode
  const [answer, setAnswer] = useState('');
  const [result, setResult] = useState(null);     // typing: response from /check; tapping: { correct, choice }
  const [score, setScore] = useState({ right: 0, total: 0 });
  const [missed, setMissed] = useState([]);       // hanzi (words when typing, characters when tapping)
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const recentRef = useRef([]);
  const inputRef = useRef(null);
  const nextButtonRef = useRef(null);

  const play = (text, pinyin, slow = false) => playAudio(text, slow, null, null, null, pinyin);
  const playCurrent = (slow = false) => {
    if (mode === 'tapping') play(question.character, question.pinyin, slow);
    else play(word.hanzi, word.pinyin, slow);
  };

  const recordAnswer = (hanzi, correct) => {
    setScore(s => ({ right: s.right + (correct ? 1 : 0), total: s.total + 1 }));
    setMissed(m => correct
      ? m.filter(h => h !== hanzi)
      : (m.includes(hanzi) ? m : [...m, hanzi]));
  };

  const loadNext = async (hskLevel = level, missedNow = missed, modeNow = mode) => {
    setLoading(true);
    setError(null);
    try {
      const body = { hsk_level: hskLevel, missed: missedNow, recent: recentRef.current };
      let hanzi;
      if (modeNow === 'tapping') {
        const { question: next } = await postJson('/api/pinyin-practice/tap', body);
        hanzi = next.character;
        setQuestion(next);
        play(next.character, next.pinyin);
      } else {
        const { word: next } = await postJson('/api/pinyin-practice', body);
        hanzi = next.hanzi;
        setWord(next);
        play(next.hanzi, next.pinyin);
      }
      recentRef.current = [...recentRef.current, hanzi].slice(-RECENT_KEEP);
      setAnswer('');
      setResult(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const startLevel = (value) => {
    setLevel(value);
    setScore({ right: 0, total: 0 });
    setMissed([]);
    setWord(null);
    setQuestion(null);
    recentRef.current = [];
    loadNext(value, [], mode);
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!word || result || !answer.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const checked = await postJson('/api/pinyin-practice/check', { word_id: word.id, answer });
      setResult(checked);
      recordAnswer(word.hanzi, checked.correct);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const tap = (choice) => {
    if (!question || result) return;
    const correct = choice === question.character;
    setResult({ correct, choice });
    recordAnswer(question.character, correct);
  };

  // Keyboard flow. Typing: type + Enter to check, Enter again for the next
  // word. Tapping: keys 1-4 pick an option, Enter for the next one.
  useEffect(() => {
    if (result) nextButtonRef.current?.focus();
    else if (word && mode === 'typing') inputRef.current?.focus();
  }, [word, question, result, mode]);

  useEffect(() => {
    if (mode !== 'tapping' || !question || result) return;
    const onKey = (e) => {
      const i = Number(e.key) - 1;
      if (i >= 0 && i < question.options.length) tap(question.options[i]);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  if (level === null) {
    return (
      <div style={styles.page}>
        <h2>Pinyin Practice</h2>

        <div style={styles.modeToggle}>
          {MODES.map(m => (
            <button
              key={m.value}
              onClick={() => setMode(m.value)}
              style={{ ...styles.modeButton, ...(mode === m.value ? styles.modeButtonActive : {}) }}
            >
              {m.label}
            </button>
          ))}
        </div>
        <p style={{ color: '#6b7280' }}>
          {MODES.find(m => m.value === mode).hint}{' '}
          {mode === 'typing'
            ? "Pick your HSK level; you'll also get some words from the level above."
            : "Pick your HSK level; you'll get characters you've seen inside words at your level or the one below."}
        </p>

        <div style={styles.levelGrid}>
          {LEVELS.map(l => (
            <button key={l.value} style={styles.levelButton} onClick={() => startLevel(l.value)}>
              {l.label}
            </button>
          ))}
        </div>
      </div>
    );
  }

  const levelLabel = LEVELS.find(l => l.value === level)?.label;
  const current = mode === 'tapping' ? question : word;

  return (
    <div style={styles.page}>
      <div style={styles.score} title="Correct / answered">
        {score.right} / {score.total}
      </div>

      <h2 style={{ marginBottom: '4px' }}>Pinyin Practice</h2>
      <div style={{ color: '#6b7280', marginBottom: '24px' }}>
        {levelLabel} · {MODES.find(m => m.value === mode).label}
        {' · '}
        <button style={styles.linkButton} onClick={() => setLevel(null)}>
          change
        </button>
        {missed.length > 0 && <> · {missed.length} to review</>}
      </div>

      {error && <p style={{ color: '#b91c1c' }}>{error}</p>}

      {current && (
        <div style={styles.audioRow}>
          <button style={styles.audioButton} onClick={() => playCurrent()} aria-label="Play sound">🔊</button>
          <button style={styles.slowButton} onClick={() => playCurrent(true)}>🐢 slow</button>
        </div>
      )}

      {mode === 'typing' && word && (
        <form onSubmit={submit} style={styles.form}>
          <input
            ref={inputRef}
            value={answer}
            onChange={e => setAnswer(e.target.value)}
            placeholder="e.g. lv3xing2"
            disabled={!!result}
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            style={{
              ...styles.input,
              borderColor: result ? (result.correct ? '#16a34a' : '#dc2626') : '#d1d5db',
            }}
          />
          {!result && (
            <button type="submit" style={styles.primaryButton} disabled={loading || !answer.trim()}>
              Check
            </button>
          )}
        </form>
      )}

      {mode === 'tapping' && question && (
        <div style={styles.optionGrid}>
          {question.options.map((option, i) => {
            let tone = {};
            if (result && option === question.character) tone = styles.optionCorrect;
            else if (result && option === result.choice) tone = styles.optionWrong;
            return (
              <button
                key={option}
                onClick={() => tap(option)}
                disabled={!!result}
                style={{ ...styles.optionButton, ...tone }}
              >
                <span style={styles.optionKey}>{i + 1}</span>
                {option}
                {result && <span style={styles.optionPinyin}>{question.option_pinyin?.[option]}</span>}
              </button>
            );
          })}
        </div>
      )}

      {result && current && (
        <div style={{ ...styles.reveal, background: result.correct ? '#f0fdf4' : '#fef2f2' }}>
          <div style={{ fontWeight: 600, color: result.correct ? '#166534' : '#991b1b' }}>
            {result.correct ? 'Correct!' : 'Not quite'}
          </div>

          {mode === 'tapping' ? (
            <>
              <div style={styles.hanzi}>{question.character}</div>
              <div style={{ fontSize: '1.3em' }}>{question.pinyin_marked}</div>
              <div style={{ color: '#6b7280' }}>{question.pinyin}</div>
              <div style={styles.fromWord}>
                from <strong style={{ fontSize: '1.3em' }}>{question.word.hanzi}</strong>{' '}
                {question.word.pinyin_marked} · {question.word.english}
                <button
                  style={styles.inlineAudio}
                  onClick={() => play(question.word.hanzi, question.word.pinyin)}
                  aria-label="Play word"
                >
                  🔊
                </button>
              </div>
            </>
          ) : (
            <>
              <div style={styles.hanzi}>{result.hanzi}</div>
              <div style={{ fontSize: '1.3em' }}>{result.pinyin_marked}</div>
              <div style={{ color: '#6b7280' }}>{result.pinyin}</div>
              <div style={{ marginTop: '8px' }}>{result.english}</div>
            </>
          )}

          <button
            ref={nextButtonRef}
            style={{ ...styles.primaryButton, marginTop: '16px' }}
            onClick={() => loadNext()}
            disabled={loading}
          >
            Next →
          </button>
        </div>
      )}

      {!current && loading && <p style={{ color: '#6b7280' }}>Loading…</p>}
    </div>
  );
}

const styles = {
  page: { position: 'relative', maxWidth: '480px', margin: '40px auto', padding: '0 16px', textAlign: 'center' },
  score: {
    position: 'absolute', top: 0, right: '16px',
    fontWeight: 700, fontSize: '1.2em', padding: '4px 12px',
    borderRadius: '999px', background: '#f3f4f6',
  },
  modeToggle: { display: 'inline-flex', border: '1px solid #d1d5db', borderRadius: '10px', overflow: 'hidden', margin: '8px 0' },
  modeButton: { padding: '10px 18px', fontSize: '1em', border: 'none', background: 'white', cursor: 'pointer' },
  modeButtonActive: { background: '#2563eb', color: 'white' },
  levelGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(110px, 1fr))', gap: '10px', marginTop: '20px' },
  levelButton: { padding: '14px', fontSize: '1em', borderRadius: '10px', border: '1px solid #d1d5db', background: 'white', cursor: 'pointer' },
  linkButton: { background: 'none', border: 'none', padding: 0, color: '#2563eb', cursor: 'pointer', font: 'inherit' },
  audioRow: { display: 'flex', justifyContent: 'center', alignItems: 'center', gap: '12px', marginBottom: '20px' },
  audioButton: { width: '80px', height: '80px', borderRadius: '50%', fontSize: '2em', border: 'none', background: '#e0e7ff', cursor: 'pointer' },
  slowButton: { padding: '8px 12px', borderRadius: '8px', border: '1px solid #d1d5db', background: 'white', cursor: 'pointer' },
  form: { display: 'flex', gap: '8px', justifyContent: 'center' },
  input: { flex: 1, maxWidth: '280px', padding: '10px 12px', fontSize: '1.1em', borderRadius: '8px', border: '2px solid #d1d5db' },
  primaryButton: { padding: '10px 18px', fontSize: '1em', borderRadius: '8px', border: 'none', background: '#2563eb', color: 'white', cursor: 'pointer' },
  optionGrid: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', maxWidth: '320px', margin: '0 auto' },
  optionButton: {
    position: 'relative', padding: '20px 0', fontSize: '2.4em', borderRadius: '12px',
    border: '2px solid #d1d5db', background: 'white', color: 'inherit', cursor: 'pointer',
  },
  optionKey: { position: 'absolute', top: '6px', left: '10px', fontSize: '0.35em', color: '#9ca3af' },
  optionPinyin: { display: 'block', fontSize: '0.4em', color: '#6b7280', marginTop: '4px' },
  optionCorrect: { borderColor: '#16a34a', background: '#f0fdf4' },
  optionWrong: { borderColor: '#dc2626', background: '#fef2f2' },
  reveal: { marginTop: '20px', padding: '16px', borderRadius: '12px' },
  hanzi: { fontSize: '2.5em', margin: '8px 0 4px' },
  fromWord: { marginTop: '10px' },
  inlineAudio: { marginLeft: '6px', background: 'none', border: 'none', cursor: 'pointer', fontSize: '1em' },
};
