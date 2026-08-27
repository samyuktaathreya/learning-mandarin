import { useState, useEffect, useRef } from 'react';
import '../App.css'
import { API_BASE_URL } from '../config';
const hasChinese = (str) => /[\u4e00-\u9fff]/.test(str);

/**
 * Wraps a string so every Chinese word is clickable with a dotted underline.
 * Clicking a word opens a popup showing context-aware primary definitions
 * and a collapsible dropdown for alternative meanings.
 */
export function ClickableText({ text, tags = [], isUnitTest, unitId, hskLevel = 1 }) {
    const [popup, setPopup] = useState(null); // { word, pinyin, english, unit, other_definitions, x, y }
    const containerRef = useRef(null);

    useEffect(() => {
        const handleOutsideClick = () => setPopup(null);
        document.addEventListener('click', handleOutsideClick);
        return () => document.removeEventListener('click', handleOutsideClick);
    }, []);

    if (!text) return null;
    if (isUnitTest) return <span>{text}</span>;

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

        setPopup({ word, pinyin: '...', english: null, unit: null, other_definitions: [], x, y });

        let url = `${API_BASE_URL}/api/lookup/${encodeURIComponent(word)}`;
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
        <span ref={containerRef} className="clickable-text-container">
            {units.map((unit, idx) =>
                unit.clickable ? (
                    <span
                        key={idx}
                        onClick={(e) => handleClick(e, unit.word)}
                        className="clickable-word"
                    >
                        {unit.text}
                    </span>
                ) : (
                    <span key={idx}>{unit.text}</span>
                )
            )}

            {popup && (
                <span
                    className="vocab-popup"
                    style={{ left: popup.x, top: popup.y }}
                    onClick={(e) => e.stopPropagation()}
                >
                    {/* Header */}
                    <div className="vocab-popup-header">
                        <span className="vocab-popup-title">{popup.word}</span>
                        <span className="vocab-popup-close" onClick={() => setPopup(null)}>
                            ✕
                        </span>
                    </div>

                    {/* Primary Sense */}
                    <div className="vocab-primary-def">
                        <span className="vocab-pinyin">{popup.pinyin}</span>
                        {popup.english && <span> — {popup.english}</span>}
                        {popup.unit != null && (
                            <span className="vocab-unit-tag">[Unit {popup.unit}]</span>
                        )}
                    </div>

                    {/* Collapsible Dropdown for Alternative Senses */}
                    {popup.other_definitions && popup.other_definitions.length > 0 && (
                        <details className="vocab-others-details">
                            <summary className="vocab-others-summary">
                                Other Meanings ({popup.other_definitions.length})
                            </summary>
                            <ul className="vocab-others-list">
                                {popup.other_definitions.map((def, idx) => (
                                    <li key={idx} className="vocab-others-item">
                                        <span>
                                            <strong className="vocab-pinyin">{def.pinyin}</strong> — {def.english}
                                        </span>
                                        <span className="vocab-unit-tag">
                                            {def.unit ? `[Unit ${def.unit}]` : '[reference]'}
                                        </span>
                                    </li>
                                ))}
                            </ul>
                        </details>
                    )}
                </span>
            )}
        </span>
    );
}