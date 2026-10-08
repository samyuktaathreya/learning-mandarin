import { useState } from 'react';
import { createPortal } from 'react-dom';
import PinyinSoundPopup from './PinyinSoundPopup';
import { pinyinToSyllableItem } from '../pinyinHelpers';

/**
 * Renders a string so every pinyin syllable in it ("ma4", "mǎ") is underlined
 * and clickable. Clicking one opens the PinyinSoundPopup for that syllable.
 * Anything that doesn't parse as a toned syllable is rendered as plain text.
 */
export default function ClickablePinyin({ text, isUnitTest }) {
    const [selected, setSelected] = useState(null);

    if (!text || typeof text !== 'string') return text ?? null;
    if (isUnitTest) return <span>{text}</span>;

    // Split on whitespace but keep it, so "ni3 hao3" gives two clickable syllables.
    const parts = text.split(/(\s+)/);

    return (
        <>
            {parts.map((part, idx) => {
                const item = pinyinToSyllableItem(part);
                if (!item) return <span key={idx}>{part}</span>;
                return (
                    <span
                        key={idx}
                        role="button"
                        tabIndex={0}
                        className="clickable-word clickable-pinyin"
                        onClick={() => setSelected(item)}
                        onKeyDown={(e) => {
                            if (e.key === 'Enter' || e.key === ' ') {
                                e.preventDefault();
                                setSelected(item);
                            }
                        }}
                    >
                        {part}
                    </span>
                );
            })}

            {/* Portal: this text sits inside <h1>/<p>/<strong>, where the popup's
                block markup would be invalid and would inherit heading styles. */}
            {selected && createPortal(
                <PinyinSoundPopup
                    item={selected}
                    onClose={() => setSelected(null)}
                    showProgress={false}
                />,
                document.body
            )}
        </>
    );
}