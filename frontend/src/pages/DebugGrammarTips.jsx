import { useState, useEffect } from 'react';
import unitsData from '../../../app/language-app-data/data/clean/units_output.json';

// Renders one grammar tip: an object shaped like
// { sections: [ { title, body, table: { headers, rows } | null } ] }
function GrammarTip({ tip }) {
  if (!tip || !Array.isArray(tip.sections)) return null;

  return (
    <>
      {tip.sections.map((section, i) => (
        <div key={i} style={{ marginBottom: '20px' }}>
          <h3 style={{ margin: '0 0 8px 0' }}>{section.title}</h3>
          <p style={{ whiteSpace: 'pre-wrap', margin: '0 0 12px 0' }}>{section.body}</p>

          {section.table && (
            <table style={{ borderCollapse: 'collapse', width: '100%', margin: '10px 0' }}>
              <thead>
                <tr>
                  {section.table.headers.map((h, hIdx) => (
                    <th
                      key={hIdx}
                      style={{
                        border: '1px solid #ccc',
                        padding: '10px',
                        backgroundColor: '#f0f0f0',
                        textAlign: 'left',
                      }}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {section.table.rows.map((row, rIdx) => (
                  <tr key={rIdx}>
                    {row.map((cell, cIdx) => (
                      <td key={cIdx} style={{ border: '1px solid #ccc', padding: '10px' }}>
                        {cell}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ))}
    </>
  );
}

export default function DebugGrammarTips() {
  const [tips, setTips] = useState([]);

  useEffect(() => {
    const tipsList = [];
    const seenTitles = new Set();

    Object.keys(unitsData).forEach(unitId => {
      const unit = unitsData[unitId];
      if (!unit.sentences) return;

      unit.sentences.forEach(sentence => {
        const grammarTips = sentence.grammar_tip;
        if (!grammarTips) return;

        // grammar_tip is now a list of structured tip objects (one per
        // distinct grammar point matched to this sentence)
        grammarTips.forEach(tip => {
          if (!tip || !Array.isArray(tip.sections)) return;

          // De-dupe on the combined section titles so the same tip
          // attached to multiple sentences only shows once in this debug view
          const dedupeKey = tip.sections.map(s => s.title).join('|');
          if (seenTitles.has(dedupeKey)) return;
          seenTitles.add(dedupeKey);

          tipsList.push({
            unit: unitId,
            hanzi: sentence.hanzi,
            tip,
          });
        });
      });
    });

    setTips(tipsList);
  }, []);

  return (
    <div style={{ padding: '20px', maxWidth: '800px', margin: '0 auto', fontFamily: 'sans-serif' }}>
      <h1>Grammar Tips Debugger</h1>
      <p>Found {tips.length} unique grammar tips.</p>

      {tips.map((item, idx) => (
        <div
          key={idx}
          style={{
            border: '1px solid #ddd',
            margin: '20px 0',
            padding: '20px',
            borderRadius: '8px',
            backgroundColor: '#f9f9f9',
          }}
        >
          <div style={{ marginBottom: '15px', color: '#555' }}>
            <strong>Unit {item.unit}</strong> | Trigger Sentence: {item.hanzi}
          </div>

          <div style={{ backgroundColor: 'white', padding: '15px', border: '1px solid #eee', overflowX: 'auto' }}>
            <GrammarTip tip={item.tip} />
          </div>
        </div>
      ))}
    </div>
  );
}