import { useState } from 'react';
import { PinyinSoundCard, PinyinSoundPopup } from '../../pinyin';
import { usePinyinProgress } from '../hooks/usePinyinProgress';

export default function PinyinProgress() {
    const { sections, summary, graduated, loading, error, reload } = usePinyinProgress();
    const [selected, setSelected] = useState(null);

    if (loading) return <div className="pinyin-progress pinyin-progress--status">Loading…</div>;

    if (error) {
        return (
            <div className="pinyin-progress pinyin-progress--status">
                <p>Couldn't load your pinyin progress.</p>
                <button type="button" className="pinyin-retry" onClick={reload}>Try again</button>
            </div>
        );
    }

    if (sections.length === 0) {
        return (
            <div className="pinyin-progress pinyin-progress--status">
                Start the pinyin lessons to unlock sounds here.
            </div>
        );
    }

    return (
        <div className="pinyin-progress">
            <p className="pinyin-progress-summary">
                {graduated ? 'Pinyin complete!' : `${summary.mastered} of ${summary.total} sounds mastered`}
            </p>

            {sections.map((section) => (
                <section key={section.key} className="pinyin-section">
                    <h3 className="pinyin-section-title">{section.title}</h3>
                    <p className="pinyin-section-subtitle">{section.subtitle}</p>
                    <div className={`pinyin-grid pinyin-grid--${section.key}`}>
                        {section.items.map((item) => (
                            <PinyinSoundCard key={item.tag} item={item} onSelect={setSelected} />
                        ))}
                    </div>
                </section>
            ))}

            {selected && <PinyinSoundPopup item={selected} onClose={() => setSelected(null)} />}
        </div>
    );
}