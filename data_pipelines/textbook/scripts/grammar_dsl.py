"""
data_pipelines/textbook/scripts/grammar_dsl.py

The grammar rule language: rules are kept in a Markdown file people can read
and edit (data/grammar/grammar_rules.md, exported from the grammar trial in
test_scripts/), and applied by code -- detect_grammar.py links sentences to
grammar points with them. The rules were written by AI and tested against
labeled examples written by people (the AllSet Learning Chinese Grammar Wiki).
No AI runs here.

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
  ‖           a clause break (punctuation): `/VV ... ‖ /PN? 就/AD` = a clause with a
              verb, then the NEXT clause has (a pronoun and) 就. Without ‖ a
              pattern matches inside one clause; `...` and `_` never cross a break.
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
    kind: str                       # word | any | gap | start | end | break
    words: frozenset | None = None
    tags: frozenset | None = None
    optional: bool = False

    def accepts(self, tok: tuple[str, str]) -> bool:
        if tok is BREAK:
            return self.kind == "break"
        if self.kind == "break":
            return False
        w, t = tok
        if self.kind == "any":
            return True
        return (self.words is None or w in self.words) and (self.tags is None or (t or "") in self.tags)


def parse_item(tok: str) -> Item:
    if tok == "...":
        return Item("gap")
    if tok in ("^", "$"):
        return Item("start" if tok == "^" else "end")
    if tok in ("‖", "||"):
        return Item("break")
    optional = tok.endswith("?") and len(tok) > 1
    if optional:
        tok = tok[:-1]
    if tok == "_":
        return Item("any", optional=optional)
    word_part, slash, tag_part = tok.partition("/")
    if re.search(r"[.*+\\()\[\]{}]", word_part):
        raise RuleError(f"{tok!r} looks like a regular expression -- this is not regex syntax: use ... for "
                        "'any words' and _ for 'one word'")
    if word_part and re.fullmatch(r"[A-Za-z|]+", word_part):
        raise RuleError(f"{tok!r}: words must be Chinese -- for a tag, write /{word_part.upper()}")
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


BREAK = ("‖", "PU")      # the clause break between two clauses, as one token


def _match_at(items, toks, i, j) -> bool:
    if i == len(items):
        return True
    it = items[i]
    if it.kind == "start":
        return (j == 0 or toks[j - 1] is BREAK) and _match_at(items, toks, i + 1, j)
    if it.kind == "end":
        return (j == len(toks) or toks[j] is BREAK) and _match_at(items, toks, i + 1, j)
    if it.kind == "gap":                      # any words, but not past the end of the clause
        k = j
        while True:
            if _match_at(items, toks, i + 1, k):
                return True
            if k >= len(toks) or toks[k] is BREAK:
                return False
            k += 1
    if it.optional and _match_at(items, toks, i + 1, j):
        return True
    return j < len(toks) and it.accepts(toks[j]) and _match_at(items, toks, i + 1, j + 1)


def pattern_matches(items: list[Item], clauses: list[list[tuple[str, str]]]) -> bool:
    toks = []
    for c in clauses:
        if toks:
            toks.append(BREAK)
        toks.extend(tuple(t) for t in c)
    return any(_match_at(items, toks, 0, start) for start in range(len(toks) + 1))


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
    "# Grammar detection rules\n\n"
    "Written by AI (test_scripts/try_grammar_rule.py write), tested against people-written examples, "
    "and safe to edit by hand. Only the ```rule block of each section is read by the code. "
    "Syntax: see scripts/grammar_dsl.py.\n\n"
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


def remove_section(path: Path, code: str):
    if not path.exists():
        return
    head, sections = split_sections(path.read_text(encoding="utf-8"))
    if sections.pop(code, None) is not None:
        path.write_text(head.rstrip() + "\n\n" + "\n".join(sections.values()), encoding="utf-8")


def render(clauses) -> str:
    """我们/PN 都/AD 是/VC 学生/NN ‖ ..."""
    return CLAUSE_SEP.join(" ".join(f"{w}/{t or '?'}" for w, t in toks) for toks in clauses)


# --------------------------------- PROMPTS ---------------------------------


# --------------------------------- DATA FILES ---------------------------------
# Exported from the grammar trial (test_scripts/run_all_grammar.py --export);
# the pipeline reads only these.
GRAMMAR_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "grammar"
RULES_FILE = GRAMMAR_DATA_DIR / "grammar_rules.md"
RULE_STATUS_FILE = GRAMMAR_DATA_DIR / "rule_status.json"            # {code: {"rule": "ok", "not_for": [...]}}
LEVELS_2025_FILE = GRAMMAR_DATA_DIR / "grammar_level_overrides.json"
LEVELS_EXAMPLES_FILE = GRAMMAR_DATA_DIR / "grammar_level_by_examples.json"
SENTENCES_FILE = GRAMMAR_DATA_DIR / "selected_sentences.csv"
EXPLANATIONS_FILE = GRAMMAR_DATA_DIR / "grammar_explanations.json"
TIP_PARTS_FILE = GRAMMAR_DATA_DIR / "grammar_tip_parts.json"          # short tips (written by AI)


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def level_moves() -> dict:
    """{code: level}: the 2025-syllabus levels, then the moves from each
    point's own examples (applied after, so they win)."""
    out = {}
    for path in (LEVELS_2025_FILE, LEVELS_EXAMPLES_FILE):
        for code, v in read_json(path, {}).items():
            if not code.startswith("_") and isinstance(v, dict) and v.get("level") is not None:
                out[code] = int(v["level"])
    return out


def usable_rules() -> tuple[dict, dict, dict]:
    """({code: Rule} for the rules that passed their test, {code: [lower points
    the rule must not be used against]}, {code: [the point's words]}). A rule
    that failed, or that can't be read, is not used.

    The words matter: a rule was written and tested only on sentences that
    contain one of its point's words (2-09's rule is just "a number", meant
    for sentences with 千/万/亿), so it must only be applied to those."""
    rules, _, _ = load_rules(RULES_FILE)
    status = read_json(RULE_STATUS_FILE, {})
    ok = {c: r for c, r in rules.items() if status.get(c, {}).get("rule") == "ok"}
    not_for = {c: list(status[c].get("not_for") or []) for c in ok}
    words = {c: list(status[c].get("words") or []) for c in ok}
    return ok, not_for, words
