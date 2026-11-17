"""
data_pipelines/textbook/scripts/grammar_explanations.py

Shared helpers for the grammar explanations (GrammarExplanation rows): used
by the grammar trial's export (test_scripts/run_all_grammar.py), by
load_hsk_lists.py when it loads them, and by finalize.py.

PINYIN OF THE EXAMPLES -- every example item says where its pinyin came from:
  pinyin_format "as_written"  AllSet Learning's own pinyin, written by people,
                              kept exactly as the wiki has it (tone marks,
                              capitals, punctuation: "Wǒ zài jiā chīfàn.").
                pinyin_by "AllSet Learning".
  pinyin_format "numeric"     the same format as Sentence.pinyin: tone
                              numbers, tone sandhi applied, words split by
                              spaces ("wo3 zai4 jia1 chi1fan4").
                pinyin_by "course sentence" -- the example IS a course
                              sentence, so it has that sentence's pinyin
                              (readings chosen by resolve_senses, sandhi by
                              finalize) and reads the same everywhere.
                pinyin_by "AI (<model> <date>)" -- not a course sentence: an
                              AI wrote the readings at export, checked in
                              code (one syllable per character, each a
                              reading the dictionary lists for that
                              character), then sandhi was applied in code.
  no pinyin (pinyin "", pinyin_format null): the AI's answer failed the
                              checks twice; nothing is guessed.
A course sentence's pinyin replaces an AI one whenever the hanzi match
(fill_course_pinyin, run by load_hsk_lists and finalize). As-written
pinyin is never replaced.
"""
import json
import re

AS_WRITTEN, NUMERIC = "as_written", "numeric"
BY_ALLSET, BY_COURSE = "AllSet Learning", "course sentence"

_CJK_RE = re.compile(r"[㐀-鿿]")
_SYLLABLE_RE = re.compile(r"([A-Za-züÜvV:]+)([1-5])")
_MARKS = {"a": "āáǎà", "e": "ēéěè", "i": "īíǐì", "o": "ōóǒò", "u": "ūúǔù", "ü": "ǖǘǚǜ"}


def cjk_key(hanzi: str) -> str:
    """The Chinese characters only: an example written 我有三本书 matches the
    course sentence 我有三本书。 (pinyin covers the characters only)."""
    return "".join(_CJK_RE.findall(hanzi or ""))


def _mark(letters: str, tone: int) -> str:
    s = letters.replace("u:", "ü").replace("v", "ü").replace("V", "Ü")
    if tone == 5 or tone == 0:
        return s
    low = s.lower()
    if "a" in low:
        i = low.index("a")
    elif "e" in low:
        i = low.index("e")
    elif "ou" in low:
        i = low.index("o")
    else:
        i = max((low.rfind(v) for v in "iouü"), default=-1)
        if i < 0:
            return s
    ch = low[i]
    marked = _MARKS[ch][tone - 1]
    return s[:i] + (marked.upper() if s[i].isupper() else marked) + s[i + 1:]


def numeric_to_marks(numeric: str) -> str:
    """"wo3 zai4jia1 nar3" -> "wǒ zàijiā nǎr" (for the Markdown only; the
    item keeps the numeric form)."""
    words = []
    for word in numeric.split():
        out = ""
        for letters, tone in _SYLLABLE_RE.findall(word):
            if letters.lower() == "r" and out:          # 哪儿 stored as na3r5
                out += letters
            elif letters.lower().endswith("r") and letters.lower() != "er" and len(letters) > 1:
                out += _mark(letters[:-1], int(tone)) + letters[-1]      # nar3 -> nǎr
            else:
                out += _mark(letters, int(tone))
        words.append(out or word)
    return " ".join(words)


def blocks_markdown(blocks: list[dict]) -> str:
    """The blocks as Markdown, for anywhere plain text is easier."""
    out = []
    for b in blocks:
        if b["type"] == "heading":
            out.append(f"## {b['text']}")
        elif b["type"] == "paragraph":
            out.append(b["text"])
        elif b["type"] == "structure":
            out += [f"> {line}" for line in b["lines"]]
        elif b["type"] == "examples":
            for it in b["items"]:
                who = f"{it['speaker']}: " if it.get("speaker") else ""
                pinyin = it.get("pinyin") or ""
                if pinyin and it.get("pinyin_format") == NUMERIC:
                    pinyin = numeric_to_marks(pinyin)
                extra = " — ".join(x for x in (pinyin, it.get("english")) if x)
                out.append(f"- {who}{it['hanzi']}" + (f" — {extra}" if extra else ""))
        elif b["type"] == "forms":
            out += [f"{'✓' if it['ok'] else '✗'} {it['hanzi']}" for it in b["items"]]
    return "\n".join(out)


def example_items(examples, body) -> list[dict]:
    """Every example item of a row: its `examples` list and the body's
    example blocks (often the same items twice -- harmless)."""
    items = [it for it in examples or [] if isinstance(it, dict)]
    for b in body or []:
        if isinstance(b, dict) and b.get("type") == "examples":
            items += [it for it in b.get("items") or [] if isinstance(it, dict)]
    return items


def mark_as_written(items: list[dict]) -> None:
    """Items that came with pinyin (the wiki's) are as written by people."""
    for it in items:
        if it.get("pinyin") and not it.get("pinyin_format"):
            it["pinyin_format"], it["pinyin_by"] = AS_WRITTEN, BY_ALLSET


def course_pinyin_map(db) -> dict:
    from app.textbook.models import Sentence
    out = {}
    for hanzi, pinyin in db.query(Sentence.hanzi, Sentence.pinyin).filter(Sentence.pinyin != "").all():
        out.setdefault(cjk_key(hanzi), pinyin)
    return out


def fill_from_course(items: list[dict], pinyin_of: dict) -> int:
    """Course sentences' pinyin into the items (never over as-written
    pinyin). Returns how many items changed."""
    n = 0
    for it in items:
        if it.get("pinyin_format") == AS_WRITTEN:
            continue
        p = pinyin_of.get(cjk_key(it.get("hanzi")))
        if p and (it.get("pinyin") != p or it.get("pinyin_by") != BY_COURSE):
            it.update(pinyin=p, pinyin_format=NUMERIC, pinyin_by=BY_COURSE)
            n += 1
    return n


def _loads(text, default):
    try:
        return json.loads(text) if text else default
    except (TypeError, json.JSONDecodeError):
        return default


def fill_course_pinyin(db) -> int:
    """Every stored explanation example that is a course sentence gets that
    sentence's pinyin; the Markdown is redrawn. Returns the number of rows'
    distinct examples changed. Safe to run any time (finalize does, after
    writing the sentences' pinyin; load_hsk_lists does after loading)."""
    from app.textbook.models import GrammarExplanation
    pinyin_of = course_pinyin_map(db)
    if not pinyin_of:
        return 0
    changed = 0
    for e in db.query(GrammarExplanation).all():
        examples, body = _loads(e.examples, []), _loads(e.body, None)
        # the same example can sit in both lists as separate copies: fill both
        before = {cjk_key(it.get("hanzi")) for it in example_items(examples, body)
                  if pinyin_of.get(cjk_key(it.get("hanzi"))) and it.get("pinyin_by") == BY_COURSE
                  and it.get("pinyin") == pinyin_of[cjk_key(it.get("hanzi"))]}
        if not fill_from_course(example_items(examples, body), pinyin_of):
            continue
        after = {cjk_key(it.get("hanzi")) for it in example_items(examples, body)
                 if it.get("pinyin_by") == BY_COURSE}
        changed += len(after - before)
        e.examples = json.dumps(examples, ensure_ascii=False)
        if body is not None:
            e.body = json.dumps(body, ensure_ascii=False)
            e.explanation = blocks_markdown(body) or None
    db.flush()
    return changed
