// Renders the grammar tips for one grammar point the sentence uses, as
// dropdowns: one per short tip (usually one per point), each with a nested
// dropdown of example sentences.
//
//   tip: an entry of question.grammar_tips --
//        { grammar_code, label, is_new, unit_number,
//          parts: [{ part_id, title, structure: [lines], text,
//                    examples: [{ hanzi, pinyin_display, english }],
//                    credit: { label, generated_by, sources, license, license_url } }],
//          sections: [{ title, body }]   (flat fallback) }
//   renderText: renders a string that may contain Chinese (clickable words);
//        defaults to plain text.
//   defaultOpen: whether the tip dropdowns start open.

const plain = (text) => text;

// "AllSet Learning: Some page (https://...)" -> { name, url }
const parseSource = (source) => {
    const m = /^(.*) \((https?:\/\/[^)\s]+)\)$/.exec(source);
    return m ? { name: m[1], url: m[2] } : { name: source, url: null };
};

function Credit({ credit }) {
    if (!credit?.label) return null;
    const sources = (credit.sources || []).map(parseSource);
    return (
        <p className="grammar-tip-credit">
            {credit.label}
            {credit.generated_by && <> ({credit.generated_by})</>}
            {sources.length > 0 && <>, based on{' '}
                {sources.map((s, i) => (
                    <span key={i}>
                        {i > 0 && '; '}
                        {s.url
                            ? <a href={s.url} target="_blank" rel="noopener noreferrer">{s.name}</a>
                            : s.name}
                    </span>
                ))}
            </>}
            {credit.license && <>. License:{' '}
                {credit.license_url
                    ? <a href={credit.license_url} target="_blank" rel="noopener noreferrer">{credit.license}</a>
                    : credit.license}
            </>}
        </p>
    );
}

// Word-by-word (literal) translation: each word over its pinyin and gloss.
function Literal({ words }) {
    if (!words?.length) return null;
    return (
        <div className="grammar-tip-example-literal">
            {words.map((w, i) => (
                <span key={i} className="grammar-tip-literal-word">
                    <span className="grammar-tip-literal-hanzi">{w.word}</span>
                    {w.pinyin && <small className="grammar-tip-literal-pinyin">{w.pinyin}</small>}
                    <small className="grammar-tip-literal-gloss">{w.gloss || '?'}</small>
                </span>
            ))}
        </div>
    );
}

function Examples({ examples, renderText }) {
    if (!examples?.length) return null;
    return (
        <details className="grammar-tip-examples">
            <summary>Example sentences ({examples.length})</summary>
            <ul>
                {examples.map((x, i) => (
                    <li key={x.sentence_id ?? i}>
                        <div className="grammar-tip-example-hanzi">{renderText(x.hanzi)}</div>
                        {x.pinyin_display && <div className="grammar-tip-example-pinyin">{x.pinyin_display}</div>}
                        {x.english && <div className="grammar-tip-example-english">{x.english}</div>}
                        <Literal words={x.literal} />
                    </li>
                ))}
            </ul>
        </details>
    );
}

function Part({ part, tip, renderText, defaultOpen }) {
    return (
        <details className="grammar-tip-part" open={defaultOpen}>
            {/* Plain text in the summary: clicking it toggles the dropdown. */}
            <summary>
                <span className="grammar-tip-part-title">{part.title || tip.label}</span>
                {tip.is_new && <span className="grammar-tip-badge">New</span>}
            </summary>
            <div className="grammar-tip-part-body">
                {part.text && <p>{renderText(part.text)}</p>}
                {part.structure?.length > 0 && (
                    <div className="grammar-tip-structure">
                        {part.structure.map((line, i) => <div key={i}>{renderText(line)}</div>)}
                    </div>
                )}
                <Examples examples={part.examples} renderText={renderText} />
                <Credit credit={part.credit} />
            </div>
        </details>
    );
}

// Older responses (no `parts`): show the flat sections in one dropdown.
function Sections({ tip, renderText, defaultOpen }) {
    return (
        <details className="grammar-tip-part" open={defaultOpen}>
            <summary><span className="grammar-tip-part-title">{tip.label}</span></summary>
            <div className="grammar-tip-part-body">
                {tip.sections.map((s, i) => (
                    <div key={i}>
                        {s.title && <h4>{renderText(s.title)}</h4>}
                        <p style={{ whiteSpace: 'pre-wrap' }}>{renderText(s.body)}</p>
                    </div>
                ))}
            </div>
        </details>
    );
}

export default function GrammarTip({ tip, renderText = plain, defaultOpen = false }) {
    if (!tip || typeof tip !== 'object') return null;
    if (Array.isArray(tip.parts) && tip.parts.length > 0) {
        return tip.parts.map((part, i) => (
            <Part key={part.part_id ?? i} part={part} tip={tip} renderText={renderText} defaultOpen={defaultOpen} />
        ));
    }
    if (Array.isArray(tip.sections) && tip.sections.length > 0) {
        return <Sections tip={tip} renderText={renderText} defaultOpen={defaultOpen} />;
    }
    return null;
}
