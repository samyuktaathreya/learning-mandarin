import { useState, useRef, useEffect } from 'react';

// We use a lightweight pinyin->hanzi lookup via a free API
// since pinyin-pro does hanzi->pinyin but not the reverse.
// We'll use the open-source pinyin-to-hanzi approach with fetch to
// a local conversion utility baked in.

// Candidate fetching via free online IME API (no key needed)
const fetchCandidates = async (pinyinInput) => {
    if (!pinyinInput) return [];
    try {
        const res = await fetch(
            `https://inputtools.google.com/request?text=${encodeURIComponent(pinyinInput)}&inputtype=pinyin&app=dicts&num=8&itc=zh-t-i0-pinyin`
        );
        const data = await res.json();
        // Response: [status, [[input, [candidates...], ...], ...]]
        if (data[0] === 'SUCCESS' && data[1]?.[0]?.[1]) {
            return data[1][0][1];
        }
        return [];
    } catch {
        return [];
    }
};

export default function ChineseIMEInput({ value, onChange, autoFocus, placeholder }) {
    const [pinyinBuffer, setPinyinBuffer] = useState(''); // raw pinyin being typed
    const [candidates, setCandidates] = useState([]);
    const [selectedIndex, setSelectedIndex] = useState(0);
    const inputRef = useRef(null);
    const debounceRef = useRef(null);

    // Fetch candidates whenever pinyinBuffer changes
    useEffect(() => {
        if (!pinyinBuffer) {
            setCandidates([]);
            return;
        }
        clearTimeout(debounceRef.current);
        debounceRef.current = setTimeout(async () => {
            const results = await fetchCandidates(pinyinBuffer);
            setCandidates(results);
            setSelectedIndex(0);
        }, 150);
        return () => clearTimeout(debounceRef.current);
    }, [pinyinBuffer]);

    const commitCandidate = (hanzi) => {
        onChange(value + hanzi);
        setPinyinBuffer('');
        setCandidates([]);
        setSelectedIndex(0);
        inputRef.current?.focus();
    };

    const handleKeyDown = (e) => {
        if (!candidates.length && !pinyinBuffer) return;

        // Number keys 1-8 select candidates
        if (candidates.length > 0 && e.key >= '1' && e.key <= '8') {
            const idx = parseInt(e.key) - 1;
            if (candidates[idx]) {
                e.preventDefault();
                commitCandidate(candidates[idx]);
                return;
            }
        }

        // Space or Enter commits the first candidate
        if (candidates.length > 0 && (e.key === ' ' || e.key === 'Enter')) {
            e.preventDefault();
            commitCandidate(candidates[selectedIndex] || candidates[0]);
            return;
        }

        // Escape clears the buffer
        if (e.key === 'Escape') {
            setPinyinBuffer('');
            setCandidates([]);
            return;
        }

        // Backspace: delete from pinyin buffer first, then from committed text
        if (e.key === 'Backspace') {
            if (pinyinBuffer.length > 0) {
                e.preventDefault();
                setPinyinBuffer(prev => prev.slice(0, -1));
                return;
            }
            // let normal backspace handle the committed text
        }

        // Arrow keys to navigate candidates
        if (e.key === 'ArrowRight' && candidates.length > 0) {
            e.preventDefault();
            setSelectedIndex(i => Math.min(i + 1, candidates.length - 1));
            return;
        }
        if (e.key === 'ArrowLeft' && candidates.length > 0) {
            e.preventDefault();
            setSelectedIndex(i => Math.max(i - 1, 0));
            return;
        }
    };

    const handleChange = (e) => {
        const raw = e.target.value;

        // Separate out any newly typed ascii letters into the pinyin buffer
        // The input value is: committed hanzi + pinyinBuffer
        // We only want the part after the committed text
        const committed = value;
        if (raw.startsWith(committed)) {
            const newBuffer = raw.slice(committed.length);
            // Only buffer ascii letters (pinyin), commit everything else
            if (/^[a-zA-Z]*$/.test(newBuffer)) {
                setPinyinBuffer(newBuffer);
            } else {
                // Non-letter typed (e.g. punctuation) — pass through directly
                onChange(raw);
                setPinyinBuffer('');
            }
        } else {
            // User deleted committed text
            onChange(raw);
            setPinyinBuffer('');
        }
    };

    // What the input actually shows: committed hanzi + current pinyin buffer
    const displayValue = value + pinyinBuffer;

    return (
        <div style={{ position: 'relative', display: 'inline-block', width: '100%' }}>
            <input
                ref={inputRef}
                value={displayValue}
                onChange={handleChange}
                onKeyDown={handleKeyDown}
                autoFocus={autoFocus}
                placeholder={placeholder || "Type pinyin..."}
                style={{ width: '100%', fontSize: '1.2rem', padding: '6px 10px' }}
            />

            {/* Candidate bar — only shows when there's a pinyin buffer */}
            {candidates.length > 0 && (
                <div style={{
                    position: 'absolute',
                    top: '100%',
                    left: 0,
                    background: '#1c1c1e',
                    border: '1px solid #3a3a3c',
                    borderRadius: '8px',
                    display: 'flex',
                    alignItems: 'center',
                    padding: '4px 8px',
                    gap: '2px',
                    zIndex: 1000,
                    boxShadow: '0 4px 16px rgba(0,0,0,0.4)',
                    marginTop: '4px',
                    minWidth: '300px',
                }}>
                    {/* Pinyin label */}
                    <span style={{
                        color: '#888',
                        fontSize: '0.85rem',
                        marginRight: '8px',
                        fontStyle: 'italic',
                        whiteSpace: 'nowrap',
                    }}>
                        {pinyinBuffer}
                    </span>

                    {/* Candidates */}
                    {candidates.map((char, i) => (
                        <button
                            key={i}
                            onMouseDown={(e) => {
                                e.preventDefault(); // don't blur input
                                commitCandidate(char);
                            }}
                            style={{
                                background: i === selectedIndex ? '#0a84ff' : 'transparent',
                                color: i === selectedIndex ? '#fff' : '#e0e0e0',
                                border: 'none',
                                borderRadius: '5px',
                                padding: '3px 8px',
                                cursor: 'pointer',
                                fontSize: '1.1rem',
                                display: 'flex',
                                flexDirection: 'column',
                                alignItems: 'center',
                                gap: '1px',
                                minWidth: '32px',
                            }}
                        >
                            <span style={{ fontSize: '0.65rem', color: i === selectedIndex ? '#cce4ff' : '#666' }}>
                                {i + 1}
                            </span>
                            <span>{char}</span>
                        </button>
                    ))}
                </div>
            )}
        </div>
    );
}