import { useState, useRef } from 'react';
import { API_BASE_URL } from '../config';

/**
 * SPIKE: Pronunciation Assessment test page.
 *
 * Goal: find out whether Azure's zh-CN accuracy_score penalizes TONE errors
 * (not just wrong consonants). Record the same character three ways:
 *
 *   1. Correct        (e.g. jǐ  for 几)
 *   2. Wrong tone     (e.g. jī  for 几)   <- does the score drop?
 *   3. Wrong initial  (e.g. zhǐ for 几)   <- should definitely drop
 *
 * If (2) drops the score, assessment handles tone and we can retire tones_match.
 * If (2) scores high, we keep tones_match and use assessment only for segmental
 * accuracy (which still fixes the ji/zhi problem).
 *
 * Throwaway. Delete once the question is answered.
 */

const PRESETS = ['几', '岁', '是', '四', '十'];

export default function TestPronounciation() {
    const [reference, setReference] = useState('几');
    const [isRecording, setIsRecording] = useState(false);
    const [isAssessing, setIsAssessing] = useState(false);
    const [result, setResult] = useState(null);
    const [history, setHistory] = useState([]);
    const [label, setLabel] = useState('correct');

    const mediaRecorderRef = useRef(null);
    const audioChunksRef = useRef([]);

    const startRecording = async () => {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            audioChunksRef.current = [];
            const mr = new MediaRecorder(stream);
            mediaRecorderRef.current = mr;
            mr.ondataavailable = (e) => { if (e.data.size > 0) audioChunksRef.current.push(e.data); };
            mr.onstop = async () => {
                stream.getTracks().forEach(t => t.stop());
                const blob = new Blob(audioChunksRef.current, { type: 'audio/webm' });
                await sendToServer(blob);
            };
            mr.start();
            setIsRecording(true);
            setResult(null);
        } catch (err) {
            console.error('Microphone access denied', err);
        }
    };

    const stopRecording = () => {
        mediaRecorderRef.current?.stop();
        setIsRecording(false);
        setIsAssessing(true);
    };

    const sendToServer = async (blob) => {
        const reader = new FileReader();
        reader.onloadend = async () => {
            const base64 = reader.result.split(',')[1];
            try {
                const res = await fetch(`${API_BASE_URL}/api/test/pronunciation`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ audio: base64, reference }),
                });
                const data = await res.json();
                setResult(data);
                setHistory(h => [...h, { label, reference, data, at: new Date().toLocaleTimeString() }]);
            } catch (err) {
                console.error('Assessment failed', err);
                setResult({ error: String(err) });
            } finally {
                setIsAssessing(false);
            }
        };
        reader.readAsDataURL(blob);
    };

    const scoreColor = (s) => {
        if (s == null) return '#888';
        if (s >= 80) return 'green';
        if (s >= 60) return 'orange';
        return 'red';
    };

    return (
        <div style={{ padding: 24, fontFamily: 'system-ui, sans-serif', maxWidth: 900 }}>
            <h1>Pronunciation Assessment Spike</h1>
            <p style={{ color: '#666' }}>
                Record the same character 3 ways and compare accuracy scores.
                Watch whether a <strong>wrong tone</strong> lowers the score.
            </p>

            <section style={{ margin: '24px 0', padding: 16, border: '1px solid #ddd', borderRadius: 8 }}>
                <label>
                    <strong>Reference text (what you're supposed to say):</strong><br />
                    <input
                        value={reference}
                        onChange={e => setReference(e.target.value)}
                        style={{ fontSize: 32, width: 160, padding: 8, marginTop: 8 }}
                    />
                </label>
                <div style={{ marginTop: 8 }}>
                    {PRESETS.map(p => (
                        <button key={p} onClick={() => setReference(p)}
                            style={{ fontSize: 20, marginRight: 8, padding: '4px 12px' }}>
                            {p}
                        </button>
                    ))}
                </div>
            </section>

            <section style={{ margin: '24px 0' }}>
                <strong>What are you about to say?</strong>
                <div style={{ marginTop: 8 }}>
                    {['correct', 'wrong tone', 'wrong initial'].map(l => (
                        <label key={l} style={{ marginRight: 16 }}>
                            <input type="radio" name="label" value={l}
                                checked={label === l} onChange={() => setLabel(l)} />
                            {' '}{l}
                        </label>
                    ))}
                </div>
            </section>

            <section style={{ margin: '24px 0' }}>
                <button
                    onClick={isRecording ? stopRecording : startRecording}
                    disabled={isAssessing}
                    style={{
                        fontSize: 20, padding: '12px 24px',
                        background: isRecording ? '#c00' : '#eee',
                        color: isRecording ? '#fff' : '#000',
                        border: '1px solid #999', borderRadius: 6, cursor: 'pointer',
                    }}
                >
                    {isRecording ? '⏹ Stop' : '🎙 Record'}
                </button>
                {isAssessing && <span style={{ marginLeft: 16 }}>Assessing...</span>}
            </section>

            {result && (
                <section style={{ margin: '24px 0', padding: 16, border: '2px solid #333', borderRadius: 8 }}>
                    <h2>Result</h2>
                    {result.error && <p style={{ color: 'red' }}>Error: {result.error}</p>}
                    {result.typed_wrapper_error && (
                        <p style={{ color: 'orange' }}>
                            Typed wrapper failed: {result.typed_wrapper_error} — see raw JSON below.
                        </p>
                    )}

                    <p>
                        Reference: <strong style={{ fontSize: 24 }}>{result.reference_text}</strong>
                        {'  '}→ Azure heard: <strong style={{ fontSize: 24 }}>{result.recognized_text || '(nothing)'}</strong>
                    </p>

                    {result.scores && (
                        <table style={{ borderCollapse: 'collapse', marginTop: 12 }}>
                            <tbody>
                                {Object.entries(result.scores).map(([k, v]) => (
                                    <tr key={k}>
                                        <td style={{ padding: '4px 16px 4px 0' }}>{k}</td>
                                        <td style={{ padding: 4, fontWeight: 'bold', color: scoreColor(v) }}>
                                            {v == null ? '(null)' : v}
                                        </td>
                                    </tr>
                                ))}
                                <tr>
                                    <td style={{ padding: '4px 16px 4px 0' }}>verdict</td>
                                    <td style={{ padding: 4, fontWeight: 'bold' }}>{result.verdict}</td>
                                </tr>
                            </tbody>
                        </table>
                    )}

                    {result.words?.length > 0 && (
                        <div style={{ marginTop: 16 }}>
                            <strong>Per-phoneme breakdown</strong>
                            {result.words.map((w, i) => (
                                <div key={i} style={{ marginTop: 8 }}>
                                    <div>
                                        word <strong>{w.word}</strong> — accuracy{' '}
                                        <span style={{ color: scoreColor(w.accuracy) }}>{w.accuracy}</span>
                                        {' '}({w.error_type})
                                    </div>
                                    <div style={{ marginLeft: 16 }}>
                                        {w.phonemes.map((p, j) => (
                                            <span key={j} style={{ marginRight: 12 }}>
                                                {p.phoneme}:{' '}
                                                <span style={{ color: scoreColor(p.accuracy) }}>{p.accuracy}</span>
                                            </span>
                                        ))}
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}

                    <details style={{ marginTop: 16 }}>
                        <summary style={{ cursor: 'pointer' }}>Raw JSON (look here for tone fields)</summary>
                        <pre style={{ background: '#f5f5f5', padding: 12, overflow: 'auto', fontSize: 12 }}>
                            {JSON.stringify(result.raw, null, 2)}
                        </pre>
                    </details>
                </section>
            )}

            {history.length > 0 && (
                <section style={{ margin: '24px 0' }}>
                    <h2>Comparison</h2>
                    <p style={{ color: '#666' }}>
                        This is the answer. If "wrong tone" scores near "correct", tone is NOT assessed.
                    </p>
                    <table style={{ borderCollapse: 'collapse', width: '100%' }}>
                        <thead>
                            <tr style={{ borderBottom: '2px solid #333' }}>
                                <th style={{ textAlign: 'left', padding: 8 }}>Attempt</th>
                                <th style={{ textAlign: 'left', padding: 8 }}>Ref</th>
                                <th style={{ textAlign: 'left', padding: 8 }}>Heard</th>
                                <th style={{ textAlign: 'left', padding: 8 }}>Accuracy</th>
                                <th style={{ textAlign: 'left', padding: 8 }}>Pron</th>
                                <th style={{ textAlign: 'left', padding: 8 }}>Time</th>
                            </tr>
                        </thead>
                        <tbody>
                            {history.map((h, i) => (
                                <tr key={i} style={{ borderBottom: '1px solid #ddd' }}>
                                    <td style={{ padding: 8 }}><strong>{h.label}</strong></td>
                                    <td style={{ padding: 8, fontSize: 20 }}>{h.reference}</td>
                                    <td style={{ padding: 8, fontSize: 20 }}>{h.data.recognized_text || '—'}</td>
                                    <td style={{ padding: 8, color: scoreColor(h.data.scores?.accuracy) }}>
                                        {h.data.scores?.accuracy ?? '—'}
                                    </td>
                                    <td style={{ padding: 8, color: scoreColor(h.data.scores?.pronunciation) }}>
                                        {h.data.scores?.pronunciation ?? '—'}
                                    </td>
                                    <td style={{ padding: 8, color: '#888' }}>{h.at}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                    <button onClick={() => setHistory([])} style={{ marginTop: 12 }}>Clear history</button>
                </section>
            )}
        </div>
    );
}