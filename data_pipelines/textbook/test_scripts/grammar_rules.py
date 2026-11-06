"""
data_pipelines/textbook/test_scripts/grammar_rules.py

TRIAL (used by try_grammar_rule.py): grammar detection rules written by AI,
kept in a Markdown file you can read and edit (test_scripts/grammar_rules.md),
and applied by code.

THE RULES FILE
  One section per grammar point:

      ## 1-10
      **Point:** ... (what the AI understood the point to be)
      **Covers:** ...
      **Does not cover:** ...

      ```rule
      match: 都/AD
      match: 一起|一块儿/AD
      unless: 什么|谁|哪儿|哪里 ... 都
      ```

      **Notes:** ...

  Only the ```rule block is read by the code; the rest is for people. A
  sentence HAS the point when at least one `match:` line matches it and NO
  `unless:` line does. Leaving good sentences out is fine; letting a wrong
  one in is not, so `unless:` lines are applied to the whole sentence.

PATTERN SYNTAX (one pattern per line, items separated by spaces)
  都          the word 都 (a whole word as tagging split it, not a character)
  都/AD       the word 都 tagged AD
  /AD         any word tagged AD
  都|全/AD    either word, tagged AD;  /AD|VV  any word tagged AD or VV
  _           any one word
  ...         any number of words, including none
  ^  $        start / end of a clause
  item?       optional item (都/AD? -- 都 or nothing)
  A pattern never crosses punctuation: it must match inside one clause.
"""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

CTB_TAGS = {
    "AD", "AS", "BA", "CC", "CD", "CS", "DEC", "DEG", "DER", "DEV", "DT", "ETC", "FW", "IJ", "JJ",
    "LB", "LC", "M", "MSP", "NN", "NOI", "NR", "NT", "OD", "ON", "P", "PN", "PU", "SB", "SP", "VA",
    "VC", "VE", "VV", "URL", "EM", "IC",
}
TAG_LEGEND = (
    "AD adverb, AS aspect particle (了 过 着), BA 把, CC coordinating conjunction (和 跟 还是), "
    "CD number, CS subordinating conjunction (如果 虽然), DEC/DEG 的, DER 得, DEV 地, DT determiner "
    "(这 那 每 各), IJ interjection, JJ noun modifier, LB/SB 被, LC localizer (上 里 前), "
    "M measure word, NN noun, NR proper noun, NT time noun, OD ordinal, P preposition, PN pronoun, "
    "SP sentence-final particle (吗 呢 吧 了), VA adjective as predicate, VC 是, VE 有, VV other verb"
)
CLAUSE_SEP = " ‖ "


class RuleError(ValueError):
    pass


@dataclass
class Item:
    kind: str                       # word | any | gap | start | end
    words: frozenset | None = None
    tags: frozenset | None = None
    optional: bool = False

    def accepts(self, tok: tuple[str, str]) -> bool:
        w, t = tok
        if self.kind == "any":
            return True
        return (self.words is None or w in self.words) and (self.tags is None or (t or "") in self.tags)


def parse_item(tok: str) -> Item:
    if tok == "...":
        return Item("gap")
    if tok in ("^", "$"):
        return Item("start" if tok == "^" else "end")
    optional = tok.endswith("?") and len(tok) > 1
    if optional:
        tok = tok[:-1]
    if tok == "_":
        return Item("any", optional=optional)
    word_part, slash, tag_part = tok.partition("/")
    words = frozenset(w for w in word_part.split("|") if w) if word_part else None
    tags = frozenset(t.upper() for t in tag_part.split("|") if t) if slash else None
    if not words and not tags:
        raise RuleError(f"empty item {tok!r}")
    if tags:
        unknown = tags - CTB_TAGS
        if unknown:
            raise RuleError(f"unknown tag(s) {', '.join(sorted(unknown))} in {tok!r}")
    return Item("word", words, tags, optional)


def parse_pattern(text: str) -> list[Item]:
    items = [parse_item(t) for t in text.split()]
    if not items:
        raise RuleError("empty pattern")
    return items


def _match_at(items, toks, i, j) -> bool:
    if i == len(items):
        return True
    it = items[i]
    if it.kind == "start":
        return j == 0 and _match_at(items, toks, i + 1, j)
    if it.kind == "end":
        return j == len(toks) and _match_at(items, toks, i + 1, j)
    if it.kind == "gap":
        return any(_match_at(items, toks, i + 1, k) for k in range(j, len(toks) + 1))
    if it.optional and _match_at(items, toks, i + 1, j):
        return True
    return j < len(toks) and it.accepts(toks[j]) and _match_at(items, toks, i + 1, j + 1)


def pattern_matches(items: list[Item], clauses: list[list[tuple[str, str]]]) -> bool:
    return any(_match_at(items, toks, 0, start)
               for toks in clauses for start in range(len(toks) + 1))


@dataclass
class Rule:
    code: str
    match: list = field(default_factory=list)     # [(line text, items)]
    unless: list = field(default_factory=list)

    def explain(self, clauses) -> tuple[bool, list[str], list[str]]:
        """(found, match lines that fired, unless lines that fired)"""
        m = [t for t, items in self.match if pattern_matches(items, clauses)]
        u = [t for t, items in self.unless if pattern_matches(items, clauses)]
        return bool(m) and not u, m, u

    def found(self, clauses) -> bool:
        return self.explain(clauses)[0]


_SECTION_RE = re.compile(r"^## +(\S+)", re.M)
_BLOCK_RE = re.compile(r"```rule\s*\n(.*?)```", re.S)


def parse_rule_block(code: str, section: str) -> Rule:
    blocks = _BLOCK_RE.findall(section)
    if len(blocks) > 1:
        raise RuleError(f"{code}: expected one ```rule block, found {len(blocks)}")
    # no fence: take the match:/unless: lines wherever they are
    body = blocks[0] if blocks else "\n".join(
        l for l in section.splitlines() if re.match(r"\s*(match|unless)\s*:", l, re.I))
    rule = Rule(code)
    for n, line in enumerate(body.splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        kind, colon, pattern = line.partition(":")
        kind = kind.strip().lower()
        if not colon or kind not in ("match", "unless"):
            raise RuleError(f"{code}, rule line {n}: must start with 'match:' or 'unless:' -- {line!r}")
        try:
            items = parse_pattern(pattern)
        except RuleError as e:
            raise RuleError(f"{code}, rule line {n}: {e}")
        getattr(rule, kind).append((pattern.strip(), items))
    if not rule.match:
        raise RuleError(f"{code}: no 'match:' line")
    return rule


def normalize_section(section: str) -> str:
    """Puts loose match:/unless: lines (no ```rule fence) into one fence,
    where the first of them was."""
    if _BLOCK_RE.search(section):
        return section
    lines = section.splitlines()
    idx = [k for k, l in enumerate(lines) if re.match(r"\s*(match|unless)\s*:", l, re.I)]
    if not idx:
        return section
    rule = ["```rule"] + [lines[k].strip() for k in idx] + ["```"]
    keep = [l for k, l in enumerate(lines) if k not in set(idx)]
    keep[idx[0]:idx[0]] = rule
    return "\n".join(keep) + "\n"


def split_sections(text: str) -> tuple[str, dict]:
    """(text before the first section, {code: section text incl. its ## line})"""
    starts = [(m.start(), m.group(1)) for m in _SECTION_RE.finditer(text)]
    head = text[:starts[0][0]] if starts else text
    out = {}
    for k, (pos, code) in enumerate(starts):
        end = starts[k + 1][0] if k + 1 < len(starts) else len(text)
        out[code] = text[pos:end].rstrip() + "\n"
    return head, out


FILE_HEAD = (
    "# Grammar detection rules (trial)\n\n"
    "Written by AI (try_grammar_rule.py write), checked against the pipeline's sentences "
    "(try_grammar_rule.py validate), and safe to edit by hand. Only the ```rule block of each "
    "section is read by the code. Syntax: see test_scripts/grammar_rules.py.\n\n"
)


def load_rules(path: Path) -> tuple[dict, dict, dict]:
    """({code: Rule}, {code: section text}, {code: error})"""
    if not path.exists():
        return {}, {}, {}
    _, sections = split_sections(path.read_text(encoding="utf-8"))
    rules, errors = {}, {}
    for code, sec in sections.items():
        try:
            rules[code] = parse_rule_block(code, sec)
        except RuleError as e:
            errors[code] = str(e)
    return rules, sections, errors


def save_section(path: Path, code: str, section: str):
    """Writes/replaces one section, keeping every other one as it is."""
    head, sections = split_sections(path.read_text(encoding="utf-8")) if path.exists() else (FILE_HEAD, {})
    sections[code] = section.rstrip() + "\n"
    path.write_text(head.rstrip() + "\n\n" + "\n".join(sections[c] for c in sections), encoding="utf-8")


def render(clauses) -> str:
    """我们/PN 都/AD 是/VC 学生/NN ‖ ..."""
    return CLAUSE_SEP.join(" ".join(f"{w}/{t or '?'}" for w, t in toks) for toks in clauses)


# --------------------------------- PROMPTS ---------------------------------

SYNTAX_HELP = """RULE SYNTAX. A ```rule block holds lines `match: <pattern>` and `unless: <pattern>`.
A sentence has the point when at least one match: line matches and NO unless: line matches
(unless: lines are checked against the whole sentence). A pattern is items separated by spaces:
  都          the word 都 (a whole word as tokenized, not a character inside a longer word)
  都/AD       the word 都 tagged AD
  /AD         any word tagged AD
  都|全/AD    either word, tagged AD;   /AD|VV  any word tagged AD or VV
  _           any one word
  ...         any number of words, including none
  ^  $        start / end of a clause
  item?       optional item
A pattern only matches inside ONE clause (between punctuation; shown as ‖ below).
Use only these tags: """ + TAG_LEGEND


def _tagged_list(items) -> str:
    return "\n".join(f"  {h}\n      {r}" for h, r in items) or "  (none)"


def writer_prompt(code: str, context: str, positives, negatives, unlabeled) -> str:
    """positives / negatives / unlabeled: [(hanzi, tagged)]"""
    return f"""You are writing a detection rule for one grammar point of the official HSK grammar list,
for a Mandarin course. Code will apply your rule to tokenized, POS-tagged sentences (Penn Chinese
Treebank tags) to find sentences that demonstrate this point.

{context}

PRECISION FIRST: a sentence the rule wrongly matches will be taught as an example of this point,
which is bad. Missing some good sentences is fine. When a use is borderline, exclude it.

{SYNTAX_HELP}

LABELED EXAMPLES OF THIS POINT (written by people), as the course's tagger splits and tags them.
The rule must MATCH these:
{_tagged_list(positives)}

LABELED COUNTER-EXAMPLES (other grammar points that use the same words). The rule must NOT match
these:
{_tagged_list(negatives)}

UNLABELED SENTENCES FROM THE COURSE containing the words, to show how the tagger handles real
sentences (tags can be wrong -- don't rely on a rare tag):
{_tagged_list(unlabeled)}

Write the rule as ONE Markdown section, exactly in this shape, and nothing else:

## {code}
**Point:** <one line: what this point is>
**Covers:** <the uses that count>
**Does not cover:** <the uses that don't, and why>

```rule
match: ...
unless: ...
```

**Notes:** <anything a person editing the rule should know>
"""


def revise_prompt(code: str, context: str, section: str, ex_missed, ex_wrong, chk_wrong_in, chk_missed) -> str:
    def lines(items, why=False):
        if why:
            return "\n".join(f"  {h}\n      {r}\n      checker: {w}" for h, r, w in items) or "  (none)"
        return _tagged_list(items)
    return f"""You wrote this detection rule for HSK grammar point {code}.

{context}

YOUR RULE:
{section}

1. LABELED EXAMPLES (written by people -- these labels are RIGHT) that the rule gets wrong:
   Examples of {code} the rule does NOT match:
{lines(ex_missed)}
   Counter-examples (other points) the rule DOES match -- these matter most:
{lines(ex_wrong)}

2. COURSE SENTENCES where an AI checker disagrees with the rule (the checker CAN BE WRONG; use your
   own judgement and the definition above):
   Rule matched, checker says it is not {code}:
{lines(chk_wrong_in, True)}
   Rule did not match, checker says it is {code}:
{lines(chk_missed, True)}

Fix the rule. PRECISION FIRST: a wrong match is much worse than a miss, so never loosen the rule in
a way that would let other wrong sentences in.

{SYNTAX_HELP}

Reply with the complete revised section in the same shape (## {code}, **Point:**, **Covers:**,
**Does not cover:**, the rule lines inside a ```rule fence, **Notes:**), and nothing else.
"""


CHECK_VERSION = 3


def check_prompt(code: str, context: str, hanzi: str, tagged: str, english: list[str] | None) -> str:
    eng = ""
    if english:
        opts = "\n".join(f"  {i}. {e}" for i, e in enumerate(english))
        eng = (f"\n\nENGLISH TRANSLATIONS:\n{opts}\nAlso pick the translation that is accurate and complete "
               "(nothing added, nothing important left out -- including the meaning of the grammar point). "
               "null if none of them is.")
    eng_field = ', "english": <number or null>' if english else ""
    return f"""You are checking an example sentence for a Mandarin course.

{context}

SENTENCE: {hanzi}
WORDS: {tagged}

Does this sentence demonstrate grammar point {code} as defined above -- the word used the way the
examples of this point use it? Answer false if the word is only used in one of the "NOT this point"
ways, is part of a longer word or fixed expression, or if you are not sure. The sentence must also
be natural, correct Mandarin; answer false if it is not.{eng}

Respond with ONLY a JSON object: {{"uses_point": true or false, "reason": "<short>"{eng_field}}}"""


def check_batch_prompt(code: str, context: str, items: list[dict]) -> str:
    """items: [{"n", "hanzi", "tagged", "english": [..] or None}] -- several
    sentences in one call, so the definition is sent once."""
    blocks = []
    for it in items:
        b = f"[{it['n']}] {it['hanzi']}\n    words: {it['tagged']}"
        if it.get("english"):
            b += "\n    translations: " + " | ".join(f"({i}) {e}" for i, e in enumerate(it["english"]))
        blocks.append(b)
    has_eng = any(it.get("english") for it in items)
    eng_rule = ("\nFor sentences with translations, also give \"english\": the number of the translation that is "
                "accurate and complete (nothing added, nothing important left out -- including the meaning of "
                "the grammar point), or null if none is.") if has_eng else ""
    eng_field = ', "english": <number or null>' if has_eng else ""
    return f"""You are checking example sentences for a Mandarin course.

{context}

SENTENCES:
{chr(10).join(blocks)}

For EACH sentence, judge it on its own: does it demonstrate grammar point {code} as defined above
-- the word used the way the examples of this point use it? Answer false if the word is only used
in one of the "NOT this point" ways, is part of a longer word or fixed expression, or if you are
not sure. The sentence must also be natural, correct Mandarin; answer false if it is not.{eng_rule}

Respond with ONLY a JSON array, one object per sentence, in any order:
[{{"n": <sentence number>, "uses_point": true or false, "reason": "<at most 12 words>"{eng_field}}}, ...]"""


def extract_json_array(text: str) -> list:
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        raise ValueError("no JSON array in the answer")
    return json.loads(m.group(0))


SOURCES_PROMPT = """You are matching grammar reference pages to one point of the official HSK grammar list.

GRAMMAR POINT {code} (HSK {level}): {label}
{official}
Below are pages from the AllSet Learning Chinese Grammar Wiki that use the same word(s) ({markers}).
Put each page in one group:
  "is"     -- the page teaches this grammar point (or one of the uses this point lists)
  "not"    -- the page teaches a DIFFERENT grammar point that uses the same word(s), so its example
              sentences are counter-examples for this point
  "unsure" -- related but neither: overlaps this point only partly, or you can't tell

PAGES:
{pages}

Respond with ONLY a JSON object mapping every page id to its group, e.g.
{{"ASG9YK09": "is", "ASGT1CIR": "not"}}"""


def extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in the answer")
    return json.loads(m.group(0))


def extract_section(code: str, text: str) -> str:
    """The `## code` section of a model answer (drops any chatter or a
    ```markdown fence around it)."""
    m = re.search(rf"^#+[^\n]*?{re.escape(code)}\b[^\n]*$", text, re.M)   # "## 1-10", "# Rule for 1-10"
    if not m:
        raise RuleError(f"answer has no '## {code}' section")
    lines = [f"## {code}"] + text[m.end():].strip().splitlines()
    fences = [k for k, line in enumerate(lines) if line.strip().startswith("```")]
    if len(fences) % 2 == 1:                   # the whole answer was fenced: drop its closing fence
        del lines[fences[-1]]
    return "\n".join(lines).strip() + "\n"