import { useState, useEffect, useRef } from 'react';
import { API_BASE_URL } from '../../../shared/config';
const hasChinese = (str) => /[\u4e00-\u9fff]/.test(str);
import { apiFetch } from '../../../shared/api/client';
import { playAudio } from '../../audio';

// "to go forward; to advance; to enter" -> ['to go forward', 'to advance', 'to enter']
const splitMeanings = (english) =>
    (english || '').split(';').map(m => m.trim()).filter(Boolean);

/**
 * Wraps a string so every Chinese word is clickable with a dotted underline.
 * Clicking a word opens a popup showing context-aware primary definitions
 * and a collapsible dropdown for alternative meanings.
 */
export function ClickableText({ text, tags = [], isUnitTest, unitId, hskLevel = 1 }) {
    const [popup, setPopup] = useState(null); // { word, pinyin, english, unit, other_definitions, x, y }
    const [showAllMeanings, setShowAllMeanings] = useState(false);
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

        setShowAllMeanings(false);
        setPopup({ word, pinyin: '...', english: null, unit: null, other_definitions: [], x, y });

        let url = `${API_BASE_URL}/api/lookup/${encodeURIComponent(word)}`;
        const queryParams = new URLSearchParams();
        if (unitId != null) queryParams.append('unit', unitId);
        if (hskLevel != null) queryParams.append('hsk_level', hskLevel);

        const queryString = queryParams.toString();
        if (queryString) {
            url += `?${queryString}`;
        }

        apiFetch(url)
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
                    {/* Only inline elements in here: ClickableText is often inside a <p>,
                        which can't contain <div>/<ul>. Layout comes from App.css. */}
                    {/* Header */}
                    <span className="vocab-popup-header">
                        <span className="vocab-popup-title">
                            {popup.word}
                            <button
                                type="button"
                                className="vocab-popup-listen"
                                onClick={() => playAudio(popup.word)}
                                aria-label={`Listen to ${popup.word}`}
                                title="Listen"
                            >
                                🔊
                            </button>
                        </span>
                        <span className="vocab-popup-close" onClick={() => setPopup(null)}>
                            ✕
                        </span>
                    </span>

                    {(() => {
                        const meanings = splitMeanings(popup.english);
                        const others = popup.other_definitions || [];
                        const hasMore = meanings.length > 1 || others.length > 0;
                        const shown = showAllMeanings ? meanings : meanings.slice(0, 1);
                        return (
                            <>
                                {/* Primary Sense */}
                                <span className="vocab-primary-def">
                                    <span className="vocab-pinyin">{popup.pinyin}</span>
                                    {shown.length > 0 && <span> — {shown.join('; ')}</span>}
                                    {popup.unit != null && (
                                        <span className="vocab-unit-tag">[Unit {popup.unit}]</span>
                                    )}
                                </span>

                                {/* Alternative readings, shown with the rest of the meanings */}
                                {showAllMeanings && others.length > 0 && (
                                    <span className="vocab-others-list">
                                        {others.map((def, idx) => (
                                            <span key={idx} className="vocab-others-item">
                                                <span>
                                                    <strong className="vocab-pinyin">{def.pinyin}</strong> — {def.english}
                                                </span>
                                                <span className="vocab-unit-tag">
                                                    {def.unit ? `[Unit ${def.unit}]` : '[reference]'}
                                                </span>
                                            </span>
                                        ))}
                                    </span>
                                )}

                                {hasMore && (
                                    <button
                                        type="button"
                                        className="vocab-others-toggle"
                                        onClick={() => setShowAllMeanings(v => !v)}
                                    >
                                        {showAllMeanings ? 'Hide additional meanings' : 'View additional meanings'}
                                    </button>
                                )}
                            </>
                        );
                    })()}
                </span>
            )}
        </span>
    );
}