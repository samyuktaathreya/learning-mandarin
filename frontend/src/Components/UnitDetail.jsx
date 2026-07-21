import { useState, useEffect } from 'react';

const USER_ID = 1;

// 5-cell strength bar from a 0..1 strength value.
function bar(strength) {
    if (strength === null || strength === undefined) return '·····';
    const filled = Math.round(strength * 5);
    return '█'.repeat(filled) + '░'.repeat(5 - filled);
}

function FacetCell({ label, facet }) {
    const eligible = facet.is_review_eligible;
    const due = facet.is_due;
    return (
        <div
            className="facet-cell"
            style={{
                fontFamily: 'monospace', fontSize: '0.8rem',
                color: due ? '#c0392b' : (eligible ? '#111' : '#999'),
            }}
            title={eligible
                ? `strength ${facet.strength}, stability ${facet.stability}d`
                : 'still learning (not review-eligible)'}
        >
            {label} {bar(facet.strength)}{' '}
            {eligible
                ? (due ? 'DUE' : `${facet.strength}`)
                : `learning (${facet.correct_count}/3)`}
        </div>
    );
}

/**
 * Detail panel for one unit. Fetches /api/unit_detail/{user}/{unit}, which is
 * only unlocked for the current unit and graduated units. For a locked unit
 * the backend returns {locked:true} and we show a lock message (the sidebar
 * also gates clicks, but this is a safety net).
 *
 * This is the review-debugging window: it shows BOTH facets per word so their
 * divergence (strong character, weak pinyin, or vice versa) is visible, plus
 * decayed strength and due state.
 */
export default function UnitDetail({ unit }) {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        fetch(`/api/unit_detail/${USER_ID}/${unit}`)
            .then(r => r.json())
            .then(d => { if (!cancelled) { setData(d); setLoading(false); } })
            .catch(() => { if (!cancelled) setLoading(false); });
        return () => { cancelled = true; };
    }, [unit]);

    if (loading) return <div className="unit-detail">Loading unit {unit}…</div>;
    if (!data) return <div className="unit-detail">Couldn’t load unit {unit}.</div>;
    if (data.locked) return <div className="unit-detail">🔒 Unit {unit} is locked.</div>;

    const dueCount = data.words.filter(
        w => w.character.is_due || w.pinyin.is_due
    ).length;

    return (
        <div className="unit-detail">
            <div style={{ marginBottom: '0.75rem', opacity: 0.8 }}>
                {data.is_current ? 'Current unit' : 'Graduated'} ·{' '}
                {dueCount > 0 ? `${dueCount} word(s) due` : 'nothing due'}
            </div>

            <div className="unit-detail-list" style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                {data.words.map(w => (
                    <div
                        key={w.tag}
                        className="unit-detail-word"
                        style={{
                            display: 'grid',
                            gridTemplateColumns: '3rem 3rem 1fr 1fr',
                            alignItems: 'center', gap: '0.5rem',
                            paddingBottom: '0.4rem', borderBottom: '1px solid #eee',
                        }}
                    >
                        <span style={{ fontSize: '1.1rem' }}>{w.tag}</span>
                        <span className="word-tier-badge" style={{ fontSize: '0.75rem', opacity: 0.7 }}>
                            T{w.tier}
                        </span>
                        <FacetCell label="char" facet={w.character} />
                        <FacetCell label="pin " facet={w.pinyin} />
                    </div>
                ))}
            </div>
        </div>
    );
}