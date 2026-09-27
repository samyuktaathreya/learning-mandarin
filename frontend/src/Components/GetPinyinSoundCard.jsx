import '../App.css';

export default function PinyinSoundCard({ item, onSelect }) {
    const className = [
        'pinyin-card',
        item.isTone ? 'pinyin-card--tone' : '',
        item.mastered ? 'pinyin-card--mastered' : '',
    ].filter(Boolean).join(' ');

    return (
        <button
            type="button"
            className={className}
            onClick={() => onSelect(item)}
            aria-label={`${item.main}${item.sub ? ` ${item.sub} tone` : ''}, ${item.percent}% toward mastery`}
        >
            <span className="pinyin-card-main">{item.main}</span>
            {item.sub && <span className="pinyin-card-sub">{item.sub}</span>}
            <progress className="pinyin-card-bar" value={item.percent} max={100} />
        </button>
    );
}