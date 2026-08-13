import { useState, useEffect, useRef } from 'react';

const hasChinese = (str) => /[\u4e00-\u9fff]/.test(str);

/**
 * Wraps a string so every Chinese word is clickable, with a dotted
 * underline spanning the whole word (not per-character) to signal
 * that it's interactive.
 *
 * Props:
 * - text: string
 * - tags: string[]
 * - isUnitTest: boolean
 * - unitId: number | string (optional) - unit context for lookup
 * - hskLevel: number (optional, default 1) - HSK level context for lookup
 */
export function ClickableText({ text, tags = [], isUnitTest, unitId, hskLevel = 1 }) {
    // popup holds word details including multi-sense data
    const [popup, setPopup] = useState(null); // { word, pinyin, english, unit, other_definitions, x, y }
    const containerRef = useRef(null);

    useEffect(() => {
        const handleOutsideClick = () => setPopup(null);
        document.addEventListener('click', handleOutsideClick);
        return () => document.removeEventListener('click', handleOutsideClick);
    }, []);

    if (!text) return null;
    if (isUnitTest) return <span>{text}</span>;

    // Sort tags longest-first so multi-char words are matched before single chars
    const sortedTags = [...tags].filter(hasChinese).sort((a, b) => b.length - a.length);

    const handleClick = (e, word) => {
        e.stopPropagation();
        const rect = e.target.getBoundingClientRect();
        const containerRect = containerRef.current.getBoundingClientRect();
        const x = rect.left - containerRect.left;
        const y = rect.bottom - containerRect.top + 4;

        if (popup && popup.word === word) {
            setPopup(null);
            return;
        }

        // Set initial loading state
        setPopup({ word, pinyin: '...', english: null, unit: null, other_definitions: [], x, y });

        // Build query string with unit context if available
        let url = `/api/lookup/${encodeURIComponent(word)}`;
        const queryParams = new URLSearchParams();
        if (unitId != null) queryParams.append('unit', unitId);
        if (hskLevel != null) queryParams.append('hsk_level', hskLevel);

        const queryString = queryParams.toString();
        if (queryString) {
            url += `?${queryString}`;
        }

        fetch(url)
            .then(res => res.json())
            .then(data => {
                setPopup({
                    word,
                    pinyin: data.pinyin || '—',
                    english: data.english || null,
                    unit: data.unit ?? null,
                    other_definitions: data.other_definitions || [],
                    x,
                    y
                });
            })
            .catch(() => setPopup({ word, pinyin: '—', english: null, unit: null, other_definitions: [], x, y }));
    };

    // Walk through text and chunk into clickable units
    const units = [];
    let i = 0;
    while (i < text.length) {
        const char = text[i];

        if (!hasChinese(char)) {
            let j = i;
            let buf = '';
            while (j < text.length && !hasChinese(text[j])) {
                buf += text[j];
                j++;
            }
            units.push({ text: buf, clickable: false });
            i = j;
            continue;
        }

        let matchedTag = null;
        for (const tag of sortedTags) {
            if (text.startsWith(tag, i)) {
                matchedTag = tag;
                break;
            }
        }

        if (matchedTag) {
            units.push({ text: matchedTag, clickable: true, word: matchedTag });
            i += matchedTag.length;
        } else {
            units.push({ text: char, clickable: true, word: char });
            i += 1;
        }
    }

    return (
        <span ref={containerRef} style={{ position: 'relative', display: 'inline' }}>
            {units.map((unit, i) =>
                unit.clickable
                    ? (
                        <span
                            key={i}
                            onClick={(e) => handleClick(e, unit.word)}
                            style={{
                                cursor: 'pointer',
                                textDecoration: 'underline',
                                textDecorationStyle: 'dotted',
                                textDecorationColor: 'currentColor',
                                textUnderlineOffset: '3px',
                                opacity: 0.95,
                            }}
                        >
                            {unit.text}
                        </span>
                    )
                    : <span key={i}>{unit.text}</span>
            )}

            {popup && (
                <span
                    style={{
                        position: 'absolute',
                        left: popup.x,
                        top: popup.y,
                        background: 'var(--code-bg, #1e1e1e)',
                        border: '1px solid var(--border, #333)',
                        borderRadius: 6,
                        padding: '8px 12px',
                        zIndex: 100,
                        whiteSpace: 'normal',
                        maxWidth: '280px',
                        fontFamily: 'var(--mono, monospace)',
                        fontSize: '0.85rem',
                        boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
                    }}
                    onClick={(e) => e.stopPropagation()}
                >
                    {/* Header bar */}
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                        <strong style={{ fontSize: '1rem' }}>{popup.word}</strong>
                        <span
                            onClick={() => setPopup(null)}
                            style={{ cursor: 'pointer', opacity: 0.6, paddingLeft: 8 }}
                        >
                            ✕
                        </span>
                    </div>

                    {/* Primary Sense Definition */}
                    <div style={{ marginBottom: popup.other_definitions?.length ? 6 : 0 }}>
                        <span style={{ fontWeight: '600' }}>{popup.pinyin}</span>
                        {popup.english && <span> — {popup.english}</span>}
                        {popup.unit != null && (
                            <span style={{ opacity: 0.6, fontSize: '0.75rem', marginLeft: 6 }}>
                                [Unit {popup.unit}]
                            </span>
                        )}
                    </div>

                    {/* Alternative Senses Section */}
                    {popup.other_definitions && popup.other_definitions.length > 0 && (
                        <div style={{ borderTop: '1px solid var(--border, #333)', paddingTop: 6, marginTop: 6 }}>
                            <div style={{ fontSize: '0.75rem', opacity: 0.6, marginBottom: 4, fontWeight: 'bold' }}>
                                Other Meanings:
                            </div>
                            <ul style={{ margin: 0, paddingLeft: 14, listStyleType: 'disc' }}>
                                {popup.other_definitions.map((def, idx) => (
                                    <li key={idx} style={{ marginBottom: 2 }}>
                                        <span><strong>{def.pinyin}</strong> — {def.english}</span>
                                        <span style={{ opacity: 0.6, fontSize: '0.75rem', marginLeft: 4 }}>
                                            {def.unit ? `[Unit ${def.unit}]` : '[reference]'}
                                        </span>
                                    </li>
                                ))}
                            </ul>
                        </div>
                    )}
                </span>
            )}
        </span>
    );
}