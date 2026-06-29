import { useState, useEffect, useRef } from 'react';

const hasChinese = (str) => /[\u4e00-\u9fff]/.test(str);

/**
 * Wraps a string so every Chinese character is clickable.
 * When clicked, checks the question's tags to see if the character
 * belongs to a longer vocab word, then looks up that word.
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

    const findWordForChar = (char) => {
        // find the longest tag that contains this character
        const matches = tags
            .filter(tag => hasChinese(tag) && tag.includes(char))
            .sort((a, b) => b.length - a.length); // longest first
        return matches[0] || char;
    };

    const handleClick = async (e, char) => {
        e.stopPropagation();
        const word = findWordForChar(char);
        const rect = e.target.getBoundingClientRect();
        const containerRect = containerRef.current.getBoundingClientRect();
        const x = rect.left - containerRect.left;
        const y = rect.bottom - containerRect.top + 4;

        // if same word clicked again, close
        if (popup && popup.word === word) {
            setPopup(null);
            return;
        }

        setPopup({ word, pinyin: '...', english: null, x, y });

        try {
            const res = await fetch(`/api/lookup/${encodeURIComponent(word)}`);
            const data = await res.json();
            setPopup({ word, pinyin: data.pinyin || '—', english: data.english || null, x, y });
        } catch {
            setPopup({ word, pinyin: '—', english: null, x, y });
        }
    };

    // split text into chinese/non-chinese segments
    const segments = [];
    let current = '';
    let currentIsChinese = false;

    for (const char of text) {
        const isCh = hasChinese(char);
        if (isCh !== currentIsChinese && current) {
            segments.push({ text: current, isChinese: currentIsChinese });
            current = '';
        }
        currentIsChinese = isCh;
        current += char;
    }
    if (current) segments.push({ text: current, isChinese: currentIsChinese });

    return (
        <span ref={containerRef} style={{ position: 'relative', display: 'inline' }}>
            {segments.map((seg, i) =>
                seg.isChinese
                    ? [...seg.text].map((char, j) => (
                        <span
                            key={`${i}-${j}`}
                            onClick={(e) => handleClick(e, char)}
                            style={{ cursor: 'pointer' }}
                        >
                            {char}
                        </span>
                    ))
                    : <span key={i}>{seg.text}</span>
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