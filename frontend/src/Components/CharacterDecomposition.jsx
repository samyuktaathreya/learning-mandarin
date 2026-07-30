// CharacterDecomposition.jsx
//
// Renders a visual tree for an IDS (Ideographic Description Sequence) string,
// e.g. "⿰讠{hkcs-20bd1-v01}" or "⿱艹⿰木木".
//
// Usage:
//   <CharacterDecomposition data={decompositionData} />
//
// `data` is the array your /api/characters/decompose endpoint already returns,
// each item shaped like: { char, ids_raw, components: [...] }
// (components is no longer used for rendering — ids_raw is parsed directly —
// but it's fine to keep sending it from the API for other purposes.)

// ── IDC operator table ──────────────────────────────────────────
// arity = how many sub-components the operator combines
// layout = how we'll arrange them visually
const IDC_OPERATORS = {
    '⿰': { arity: 2, layout: 'row' },          // left-right
    '⿱': { arity: 2, layout: 'column' },        // above-below
    '⿲': { arity: 3, layout: 'row' },           // left-middle-right
    '⿳': { arity: 3, layout: 'column' },        // above-middle-below
    '⿴': { arity: 2, layout: 'surround-full' }, // full surround
    '⿵': { arity: 2, layout: 'surround-top' },  // surround from above
    '⿶': { arity: 2, layout: 'surround-bottom' },
    '⿷': { arity: 2, layout: 'surround-left' },
    '⿸': { arity: 2, layout: 'surround-top-left' },
    '⿹': { arity: 2, layout: 'surround-top-right' },
    '⿺': { arity: 2, layout: 'surround-bottom-left' },
    '⿻': { arity: 2, layout: 'overlay' },
};

// ── Parser: turns an IDS string into a tree ─────────────────────
// Node shapes:
//   { type: 'compound', operator, layout, children: [Node, ...] }
//   { type: 'atom', char }
//   { type: 'placeholder', code }   // unencoded/private-use component, e.g. hkcs-821f-v01
function parseIDS(str) {
    let i = 0;

    function parseNode() {
        if (i >= str.length) return { type: 'placeholder', code: '?' };

        const ch = str[i];
        const op = IDC_OPERATORS[ch];

        if (op) {
            i += 1;
            const children = [];
            for (let k = 0; k < op.arity; k++) {
                children.push(parseNode());
            }
            return { type: 'compound', operator: ch, layout: op.layout, children };
        }

        if (ch === '{') {
            const end = str.indexOf('}', i);
            if (end === -1) {
                // malformed — bail out gracefully
                const code = str.slice(i + 1);
                i = str.length;
                return { type: 'placeholder', code };
            }
            const code = str.slice(i + 1, end);
            i = end + 1;
            return { type: 'placeholder', code };
        }

        // Regular atomic character. Use codePointAt to correctly handle
        // characters outside the BMP (surrogate pairs), common in CJK ext-B+.
        const codePoint = str.codePointAt(i);
        const charStr = String.fromCodePoint(codePoint);
        i += charStr.length;
        return { type: 'atom', char: charStr };
    }

    try {
        return parseNode();
    } catch (e) {
        return { type: 'placeholder', code: str };
    }
}

// ── Renderer ─────────────────────────────────────────────────────
function IDSNode({ node, depth = 0 }) {
    if (node.type === 'atom') {
        return <div className="ids-atom">{node.char}</div>;
    }

    if (node.type === 'placeholder') {
        return (
            <div className="ids-placeholder" title={`Unencoded component: ${node.code}`}>
                <span className="ids-placeholder-glyph">?</span>
                <span className="ids-placeholder-code">{node.code}</span>
            </div>
        );
    }

    // compound node
    const { layout, children } = node;

    if (layout === 'row' || layout === 'column') {
        return (
            <div className={`ids-compound ids-${layout}`}>
                {children.map((child, idx) => (
                    <IDSNode key={idx} node={child} depth={depth + 1} />
                ))}
            </div>
        );
    }

    if (layout === 'overlay') {
        return (
            <div className="ids-compound ids-overlay">
                {children.map((child, idx) => (
                    <div className="ids-overlay-layer" key={idx}>
                        <IDSNode node={child} depth={depth + 1} />
                    </div>
                ))}
            </div>
        );
    }

    // Surround layouts: children[0] is the "outer/enclosing" shape,
    // children[1] is the "inner" shape. We approximate these with an
    // outer wrapper and an absolutely-positioned inner element, since true
    // surround shapes (e.g. 广, 门, 走) can't be perfectly represented with
    // simple boxes — this is a schematic, not a font renderer.
    const surroundClass = `ids-surround ids-${layout}`;
    return (
        <div className={surroundClass}>
            <div className="ids-surround-outer">
                <IDSNode node={children[0]} depth={depth + 1} />
            </div>
            <div className="ids-surround-inner">
                <IDSNode node={children[1]} depth={depth + 1} />
            </div>
        </div>
    );
}

function DecompositionCard({ item }) {
    const tree = parseIDS(item.ids_raw || '');

    // If the IDS string is just the character itself (no IDC operator, no
    // sub-components), there's nothing to decompose — showing the tree and
    // raw IDS underneath would just repeat the same glyph three times.
    const isAtomic = tree.type === 'atom' && tree.char === item.char;

    if (isAtomic) {
        return (
            <div className="decomposition-card decomposition-card--atomic">
                <div className="decomposition-main-char">{item.char}</div>
                <div className="decomposition-atomic-note">Basic component</div>
            </div>
        );
    }

    return (
        <div className="decomposition-card">
            <div className="decomposition-main-char">{item.char}</div>
            <div className="decomposition-tree-wrap">
                <IDSNode node={tree} />
            </div>
            <div className="decomposition-ids-raw">{item.ids_raw}</div>
        </div>
    );
}

// Extract direct children (leaves) from an IDS tree for display as an equation.
// Returns objects so placeholders can be styled/tooltipped differently from
// real characters, instead of dumping raw "{hkcs-xxxx}" text into the equation.
function extractDirectChildren(node, depth = 0) {
    if (node.type === 'atom') {
        return [{ display: node.char, isPlaceholder: false }];
    }
    if (node.type === 'placeholder') {
        console.debug("[decomposition] unencoded placeholder component:", node.code);
        return [{ display: '?', isPlaceholder: true, code: node.code }];
    }
    if (node.type === 'compound') {
        return node.children.flatMap(child => extractDirectChildren(child, depth + 1));
    }
    return [];
}

function EquationLine({ item }) {
    const tree = parseIDS(item.ids_raw || '');
    const isAtomic = tree.type === 'atom' && tree.char === item.char;

    if (isAtomic) {
        return (
            <div className="decomposition-equation">
                <span className="decomposition-equation-char">{item.char}</span>
                <span className="decomposition-equation-label">(basic component)</span>
            </div>
        );
    }

    const children = extractDirectChildren(tree);

    return (
        <div className="decomposition-equation">
            <span className="decomposition-equation-char">{item.char}</span>
            <span className="decomposition-equation-operator">=</span>
            {children.map((child, idx) => (
                <span key={idx}>
                    {child.isPlaceholder ? (
                        <span
                            className="decomposition-equation-component decomposition-equation-placeholder"
                            title={`Unencoded component: ${child.code}`}
                        >
                            {child.display}
                        </span>
                    ) : (
                        <span className="decomposition-equation-component">{child.display}</span>
                    )}
                    {idx < children.length - 1 && <span className="decomposition-equation-operator">+</span>}
                </span>
            ))}
        </div>
    );
}

export default function CharacterDecomposition({ data }) {
    if (!data || data.length === 0) return null;

    return (
        <div className="decomposition-section">
            <h3>Character Breakdown</h3>
            {data.map((item, idx) => (
                <EquationLine key={idx} item={item} />
            ))}
        </div>
    );
}