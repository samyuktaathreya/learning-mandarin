import { useState, useEffect, useRef } from 'react';

const hasChinese = (str) => /[\u4e00-\u9fff]/.test(str);

/**
 * Wraps a string so every Chinese word is clickable, with a dotted
 * underline spanning the whole word (not per-character) to signal
 * that it's interactive.
 *
 * Usage: <ClickableText text="你好世界" tags={["你好", "世界"]} isUnitTest={false} />
 */
export function ClickableText({ text, tags = [], isUnitTest }) {
    const [popup, setPopup] = useState(null); // { word, pinyin, english, x, y }
    const containerRef = useRef(null);

    useEffect(() => {
        const handleOutsideClick = () => setPopup(null);
        document.addEventListener('click', handleOutsideClick);
        return () => document.removeEventListener('click', handleOutsideClick);
    }, []);

    if (!text) return null;
    if (isUnitTest) return <span>{text}</span>;

    // sort tags longest-first so multi-char words are matched before single chars
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

        setPopup({ word, pinyin: '...', english: null, x, y });

        fetch(`/api/lookup/${encodeURIComponent(word)}`)
            .then(res => res.json())
            .then(data => setPopup({ word, pinyin: data.pinyin || '—', english: data.english || null, x, y }))
            .catch(() => setPopup({ word, pinyin: '—', english: null, x, y }));
    };

    // walk through the text and group characters into clickable units:
    // - a run of characters matching a tag (longest match wins) becomes one unit
    // - any character not covered by a tag becomes its own single-character unit
    // - non-chinese characters are passed through as plain text
    const units = [];
    let i = 0;
    while (i < text.length) {
        const char = text[i];

        if (!hasChinese(char)) {
            // accumulate consecutive non-chinese chars into one plain unit
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

        // try to match the longest tag starting at position i
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
                        background: 'var(--code-bg)',
                        border: '1px solid var(--border)',
                        borderRadius: 6,
                        padding: '6px 10px',
                        zIndex: 100,
                        whiteSpace: 'nowrap',
                        fontFamily: 'var(--mono)',
                        fontSize: '0.85rem',
                    }}
                    onClick={(e) => e.stopPropagation()}
                >
                    {popup.word} · {popup.pinyin}{popup.english ? ` · ${popup.english}` : ''}
                    <span
                        onClick={() => setPopup(null)}
                        style={{ marginLeft: 8, cursor: 'pointer', opacity: 0.6 }}
                    >
                        ✕
                    </span>
                </span>
            )}
        </span>
    );
}