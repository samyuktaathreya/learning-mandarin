// Renders one grammar tip in the session's grammar sidebar.
//
//   tip:   an entry of question.grammar_tips --
//          { grammar_code, label, is_new, unit_number, sections: [{ title, body, table }] }
//   point: session.grammar_points[tip.grammar_code] (optional) --
//          { explanations: [{ source, title, body: [blocks], credit, generated_by, ... }] }
//   renderText: renders a string that may contain Chinese (clickable words);
//          defaults to plain text.
//
// A point new in this unit is shown from its real explanation pages
// (`point.explanations`, in order). Any other point -- or when the session
// has no grammar_points -- falls back to the server's `sections`, which for
// already-taught points is a one-line reminder.

const plain = (text) => text;

function GrammarTable({ table, renderText }) {
    return (
        <table className="grammar-tip-table">
            <thead>
                <tr>
                    {table.headers.map((h, hIdx) => <th key={hIdx}>{renderText(h)}</th>)}
                </tr>
            </thead>
            <tbody>
                {table.rows.map((row, rIdx) => (
                    <tr key={rIdx}>
                        {row.map((cell, cIdx) => <td key={cIdx}>{renderText(cell)}</td>)}
                    </tr>
                ))}
            </tbody>
        </table>
    );
}

function Lines({ lines, renderText }) {
    return (
        <p>
            {lines.map((line, i) => <span key={i} style={{ display: 'block' }}>{renderText(line)}</span>)}
        </p>
    );
}

// Examples a learner who just met the point can read; if none are marked
// readable, the first three.
const pickExamples = (items = []) => {
    const readable = items.filter(x => x.readable);
    return readable.length > 0 ? readable : items.slice(0, 3);
};

function Examples({ items, renderText }) {
    return (
        <p>
            {pickExamples(items).map((x, i) => (
                <span key={i} style={{ display: 'block' }}>
                    {renderText(x.hanzi)}
                    {x.pinyin_display && <> - {x.pinyin_display}</>}
                    {x.english && <> - {x.english}</>}
                </span>
            ))}
        </p>
    );
}

function Credit({ explanation }) {
    const { credit, source, generated_by } = explanation;
    if (!credit?.label) return null;
    return (
        <p style={{ fontSize: '0.8rem', opacity: 0.7 }}>
            Source:{' '}
            {credit.url
                ? <a href={credit.url} target="_blank" rel="noopener noreferrer">{credit.label}</a>
                : credit.label}
            {credit.license && (
                <> ({credit.license_url
                    ? <a href={credit.license_url} target="_blank" rel="noopener noreferrer">{credit.license}</a>
                    : credit.license})</>
            )}
            {source === 'ai_generated' && generated_by && <> ({generated_by})</>}
        </p>
    );
}

function Block({ block, renderText }) {
    switch (block.type) {
        case 'heading':
            return <h4>{renderText(block.text)}</h4>;
        case 'paragraph':
            return <p>{renderText(block.text)}</p>;
        case 'structure':
            return <Lines lines={block.lines || []} renderText={renderText} />;
        case 'examples':
            return <Examples items={block.items} renderText={renderText} />;
        case 'forms':
            return (
                <Lines
                    lines={(block.items || []).map(x => `${x.ok ? '✓' : '✗'} ${x.hanzi ?? x.text ?? ''}`)}
                    renderText={renderText}
                />
            );
        default:
            return null;
    }
}

function Explanation({ explanation, renderText }) {
    const title = explanation.source === 'ai_generated'
        ? `${explanation.title || ''} (written by AI)`.trim()
        : explanation.title;
    return (
        <div className="grammar-tip-section">
            {title && <h4>{renderText(title)}</h4>}
            {(explanation.body || []).map((block, i) => (
                <Block key={i} block={block} renderText={renderText} />
            ))}
            <Credit explanation={explanation} />
        </div>
    );
}

function Sections({ sections, renderText }) {
    return sections.map((section, sIdx) => (
        <div key={sIdx} className="grammar-tip-section">
            <h4>{renderText(section.title)}</h4>
            <p style={{ whiteSpace: 'pre-wrap' }}>{renderText(section.body)}</p>
            {section.table && <GrammarTable table={section.table} renderText={renderText} />}
        </div>
    ));
}

export default function GrammarTip({ tip, point, renderText = plain }) {
    if (!tip || typeof tip !== 'object') return null;

    const explanations = point?.explanations?.filter(e => Array.isArray(e.body)) ?? [];
    if (tip.is_new && explanations.length > 0) {
        return explanations.map((e, i) => (
            <Explanation key={`${e.source}-${e.position ?? i}`} explanation={e} renderText={renderText} />
        ));
    }
    if (Array.isArray(tip.sections)) {
        return <Sections sections={tip.sections} renderText={renderText} />;
    }
    return null;
}
