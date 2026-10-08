// Renders one structured grammar tip: { sections: [{ title, body, table }] }
// Logs to console if the shape looks wrong so it's obvious in devtools
// why a tip isn't showing content.

const cellStyle = { border: '1px solid #ccc', padding: '8px' };
const headerCellStyle = { ...cellStyle, backgroundColor: '#f0f0f0', textAlign: 'left' };
const errorStyle = { color: '#c0392b' };

function GrammarTable({ table }) {
    return (
        <table style={{ borderCollapse: 'collapse', width: '100%', margin: '8px 0' }}>
            <thead>
                <tr>
                    {table.headers.map((h, hIdx) => (
                        <th key={hIdx} style={headerCellStyle}>{h}</th>
                    ))}
                </tr>
            </thead>
            <tbody>
                {table.rows.map((row, rIdx) => (
                    <tr key={rIdx}>
                        {row.map((cell, cIdx) => (
                            <td key={cIdx} style={cellStyle}>{cell}</td>
                        ))}
                    </tr>
                ))}
            </tbody>
        </table>
    );
}

export default function GrammarTip({ tip, tipIndex }) {
    if (!tip) {
        console.warn(`[GrammarTip debug] tip at index ${tipIndex} is null/undefined`);
        return null;
    }
    if (typeof tip === 'string') {
        console.warn(
            `[GrammarTip debug] tip at index ${tipIndex} is a raw string, not the expected ` +
            `{ sections: [...] } object -- this data is stale (old markdown format). ` +
            `Re-run the pipeline or check the API response shape.`,
            tip
        );
        return <p style={errorStyle}>⚠ Malformed grammar tip data (raw string, see console)</p>;
    }
    if (!Array.isArray(tip.sections)) {
        console.warn(`[GrammarTip debug] tip at index ${tipIndex} has no "sections" array:`, tip);
        return <p style={errorStyle}>⚠ Malformed grammar tip data (see console)</p>;
    }
    if (tip.sections.length === 0) {
        console.warn(`[GrammarTip debug] tip at index ${tipIndex} has an empty "sections" array`);
    }

    return tip.sections.map((section, sIdx) => {
        if (!section || (!section.title && !section.body && !section.table)) {
            console.warn(`[GrammarTip debug] tip ${tipIndex}, section ${sIdx} is empty:`, section);
        }
        return (
            <div key={sIdx} style={{ marginBottom: '16px' }}>
                <h4 style={{ margin: '0 0 6px 0' }}>{section.title}</h4>
                <p style={{ whiteSpace: 'pre-wrap', margin: '0 0 10px 0' }}>{section.body}</p>
                {section.table && <GrammarTable table={section.table} />}
            </div>
        );
    });
}