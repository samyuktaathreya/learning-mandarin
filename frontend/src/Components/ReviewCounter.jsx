/**
 * Small fixed-corner readout of review load. Reads the two word-counts that
 * /api/progress now returns:
 *   - review_due_word_count           words due right now
 *   - review_due_tomorrow_word_count  words that will be due after +1 day
 *
 * The tomorrow number is an "if you stop now" projection -- it doesn't subtract
 * reviews you might still do today, so it's an upper bound. Copy says
 * "waiting" rather than a hard promise for that reason.
 */
export default function ReviewCounter({ progress }) {
    if (!progress) return null;

    const dueNow = progress.review_due_word_count ?? 0;
    const dueTomorrow = progress.review_due_tomorrow_word_count ?? 0;

    return (
        <div
            className="review-counter"
            style={{
                position: 'fixed', bottom: '1rem', right: '1rem',
                background: '#111', color: '#fff', padding: '0.6rem 0.9rem',
                borderRadius: 8, fontFamily: 'monospace', fontSize: '0.85rem',
                lineHeight: 1.4, zIndex: 500, minWidth: 150,
            }}
        >
            <div style={{ fontWeight: 'bold' }}>
                {dueNow > 0 ? `${dueNow} to review now` : 'No review due'}
            </div>
            <div style={{ opacity: 0.7 }}>
                {dueTomorrow} waiting tomorrow
            </div>
        </div>
    );
}