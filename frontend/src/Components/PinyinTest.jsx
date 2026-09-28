// src/pages/PinyinTest.jsx
import { useState } from 'react';
import { API_BASE_URL } from '../config';
import { apiFetch } from '../api/client';

/**
 * Standalone test harness for the pinyin feature, isolated from the main
 * session flow. Not meant to be the real UI -- just a button to confirm
 * /api/pinyin/session returns what pinyin_engine.py is supposed to produce,
 * before this gets wired into the actual textbook session components.
 */
export function PinyinTest() {
    const [questions, setQuestions] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);

    const handleGenerate = async () => {
        setLoading(true);
        setError(null);
        try {
            const res = await apiFetch(`${API_BASE_URL}/api/pinyin/session?num_questions=10`);
            if (!res.ok) {
                throw new Error(`Request failed: ${res.status}`);
            }
            const data = await res.json();
            setQuestions(data.question_set || []);
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    return (
        <div style={{ padding: '2rem', fontFamily: 'sans-serif' }}>
            <h2>Pinyin Question Generator (test)</h2>
            <button onClick={handleGenerate} disabled={loading}>
                {loading ? 'Generating...' : 'Generate Pinyin Questions'}
            </button>

            {error && <p style={{ color: 'red' }}>Error: {error}</p>}

            {questions && (
                <ul style={{ marginTop: '1rem' }}>
                    {questions.map((q, i) => (
                        <li key={i} style={{ marginBottom: '0.5rem' }}>
                            <strong>{q.question_type}</strong> — syllable: {q.syllable}, tone: {q.tone}, target_tag: {q.target_tag}
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
}