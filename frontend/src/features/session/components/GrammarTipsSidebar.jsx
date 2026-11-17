import GrammarTip from './GrammarTips';
import { ClickableText } from '../../characters';

const hasChinese = (str) => /[一-鿿]/.test(str);

// The session's one grammar panel, shown beside the question (below it on
// narrow screens). Opened by the "Show grammar tip" button in Question /
// SpeakingQuestion via PracticeSession's onToggleGrammar.
export default function GrammarTipSidebar({ questionObj, grammarPoints, isUnitTest, onClose }) {
    const tips = Array.isArray(questionObj.grammar_tips) ? questionObj.grammar_tips : [];

    // Chinese in explanations is clickable for definitions, like the question text.
    const renderText = (text) => {
        if (!text || typeof text !== 'string' || !hasChinese(text)) return text;
        return <ClickableText text={text} tags={questionObj.tags || []} isUnitTest={isUnitTest} />;
    };

    return (
        <aside className="grammar-tip-sidebar">
            <div className="grammar-tip-header">
                <h3>Grammar Tip{tips.length > 1 ? 's' : ''}</h3>
                <button type="button" onClick={onClose} aria-label="Close grammar tips">✕</button>
            </div>
            <div className="grammar-tip-content">
                {tips.length === 0
                    ? <p style={{ opacity: 0.6 }}>No grammar tips for this question.</p>
                    : tips.map((tip, i) => (
                        <div key={tip.grammar_code ?? i} className="grammar-tip-entry">
                            <GrammarTip
                                tip={tip}
                                point={grammarPoints?.[tip.grammar_code]}
                                renderText={renderText}
                            />
                            {i < tips.length - 1 && <hr />}
                        </div>
                    ))}
            </div>
        </aside>
    );
}
