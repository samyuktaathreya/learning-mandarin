import '../App.css';
import { usePinyinSound } from '../hooks/usePinyinSound';
import { useEscapeKey } from '../hooks/useEscapeKey';
import { formatStats, getPopupTitle } from '../utils/pinyinHelpers';

export default function PinyinSoundPopup({ item, onClose }) {
    const { source, status, playing, play } = usePinyinSound(item);
    useEscapeKey(onClose);

    return (
        <div className="pinyin-popup-backdrop" onClick={onClose}>
            <div
                className="pinyin-popup"
                role="dialog"
                aria-modal="true"
                aria-label={getPopupTitle(item)}
                onClick={(e) => e.stopPropagation()}
            >
                <div className="pinyin-popup-header">
                    <span className="pinyin-popup-title">{getPopupTitle(item)}</span>
                    <button type="button" className="pinyin-popup-close" onClick={onClose} aria-label="Close">
                        ✕
                    </button>
                </div>

                {status === 'loading' && <p className="pinyin-popup-muted">Loading…</p>}
                {status === 'error' && <p className="pinyin-popup-muted">Couldn't load this sound.</p>}

                {source && (
                    <>
                        <div className="pinyin-popup-example">
                            <span className="pinyin-popup-character">{source.character}</span>
                            <span className="pinyin-popup-example-pinyin">{source.displayPinyin}</span>
                        </div>

                        <div className="pinyin-popup-actions">
                            <button
                                type="button"
                                className="pinyin-popup-play"
                                onClick={() => play(false)}
                                disabled={playing !== null}
                            >
                                {playing === 'normal' ? 'Playing…' : '🔊 Play'}
                            </button>
                            <button
                                type="button"
                                className="pinyin-popup-play pinyin-popup-play--slow"
                                onClick={() => play(true)}
                                disabled={playing !== null}
                            >
                                {playing === 'slow' ? 'Playing…' : '🐢 Slow'}
                            </button>
                        </div>

                        <div className="pinyin-popup-section">
                            <h4 className="pinyin-popup-label">How to pronounce</h4>
                            <p className={source.description ? 'pinyin-popup-text' : 'pinyin-popup-muted'}>
                                {source.description ?? 'Pronunciation guide coming soon.'}
                            </p>
                        </div>
                    </>
                )}

                <div className="pinyin-popup-section">
                    <h4 className="pinyin-popup-label">Your progress</h4>
                    <p className="pinyin-popup-text">{formatStats(item)}</p>
                    <progress className="pinyin-card-bar pinyin-popup-bar" value={item.percent} max={100} />
                    {item.mastered && <p className="pinyin-popup-mastered">Mastered</p>}
                </div>
            </div>
        </div>
    );
}