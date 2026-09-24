import { useEffect } from 'react';
import GrammarTip from './GrammarTips';

// ── VISIBLE DEBUG PANEL ── remove once working ──
function GrammarTipDebugPanel({ questionObj }) {
    const labelStyle = { marginTop: 8, display: 'block' };
    const valueStyle = { color: '#2ecc71', whiteSpace: 'pre-wrap', wordBreak: 'break-all' };
    return (
        <div style={{ background: '#1a1a2e', border: '1px solid #e74c3c', borderRadius: 6, padding: 12, marginBottom: 16, fontSize: 12, fontFamily: 'monospace', color: '#e74c3c' }}>
            <strong>DEBUG — raw question keys:</strong>
            <div style={{ color: '#f39c12', marginTop: 4 }}>
                {Object.keys(questionObj).join(', ')}
            </div>
            <strong style={labelStyle}>grammar_tip (singular):</strong>
            <div style={valueStyle}>{JSON.stringify(questionObj.grammar_tip, null, 2)}</div>
            <strong style={labelStyle}>grammar_tips (plural):</strong>
            <div style={valueStyle}>{JSON.stringify(questionObj.grammar_tips, null, 2)}</div>
        </div>
    );
}

function GrammarTipList({ questionObj }) {
    // Support both field names until we confirm which the API uses
    const tips = questionObj.grammar_tips ?? questionObj.grammar_tip;
    if (!tips || (Array.isArray(tips) && tips.length === 0)) {
        return <p style={{ opacity: 0.6 }}>No grammar tips for this question.</p>;
    }
    // Normalise: could be a single object, array of objects, or legacy string
    const tipArray = Array.isArray(tips) ? tips : [tips];
    return tipArray.map((tip, i) => (
        <div key={i} className="grammar-tip-entry">
            <GrammarTip tip={tip} tipIndex={i} />
            {i < tipArray.length - 1 && <hr />}
        </div>
    ));
}

export default function GrammarTipSidebar({ questionObj, onClose }) {
    // Debug: log grammar_tips shape whenever the sidebar is showing a question
    useEffect(() => {
        console.log('[GrammarTip debug] grammar_tips for current question:', questionObj.grammar_tips);
    }, [questionObj]);

    return (
        <div className="grammar-tip-sidebar">
            <div className="grammar-tip-header">
                <h3>Grammar Tips</h3>
                <button type="button" onClick={onClose}>✕</button>
            </div>
            <div className="grammar-tip-content">
                <GrammarTipDebugPanel questionObj={questionObj} />
                <GrammarTipList questionObj={questionObj} />
            </div>
        </div>
    );
}