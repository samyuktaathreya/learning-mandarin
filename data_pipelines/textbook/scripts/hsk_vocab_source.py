"""
Read-only access to the HSK vocabulary list (data/raw/hsk_vocab.json, from
complete-hsk-vocabulary -- MIT; definitions from CC-CEDICT, CC BY-SA 4.0).

Used by two stages:
- load_hsk_lists.py: one Vocab row per word with a 2021-standard level.
- tag_sentences.py: the candidate meanings for a word, picked by reading.

FILE FORMAT: a JSON list of entries:
    {"simplified": "爱好", "radical": "爫", "frequency": 4902,
     "level": ["newest-2", "new-1", "old-3"], "pos": ["n", "v"],
     "forms": [{"traditional": "愛好",
                "transcriptions": {"pinyin": "ài hào", "numeric": "ai4 hao4", ...},
                "meanings": ["to like; to be fond of; ...", "interest; hobby"],
                "classifiers": ["个"]}]}
LEVELS: "new-N" = the 2021 standard (GF0025-2021), N = 1-7, where 7 covers
levels 7-9. "old-N" = HSK 2.0 and "newest-N" = the 2025 exam syllabus; both
are ignored here.
"""
import json
import re
from functools import lru_cache
from pathlib import Path

LEVEL_PREFIX = "new-"

# Readings with a capital letter are proper-noun forms, usually a surname
# (还 has "Huan2: surname Huan" listed before "hai2: still").
_SURNAME_RE = re.compile(r"^(surname|old variant|variant of)\b", re.I)
_SYLLABLE_RE = re.compile(r"([a-zü:v]+)([1-5])")

# JSON POS tags that mark a proper noun (person, place, organisation, other)
# and a grammar word (particle, modal particle).
PROPER_NOUN_POS = {"nr", "ns", "nt", "nz"}
GRAMMAR_POS = {"u", "y"}


@lru_cache(maxsize=None)
def load_entries(path: str) -> dict:
    """{simplified: entry} for every entry with a 2021-standard level."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    out = {}
    for entry in raw:
        if level_of(entry) is not None:
            out[entry["simplified"]] = entry
    return out


def level_of(entry: dict) -> int | None:
    """The entry's 2021-standard level (1-7), or None if it has none.
    Matches "new-N" exactly -- "newest-N" also starts with "new"."""
    for lvl in entry.get("level", []):
        if lvl.startswith(LEVEL_PREFIX) and lvl[len(LEVEL_PREFIX):].isdigit():
            return int(lvl[len(LEVEL_PREFIX):])
    return None


def numeric(form: dict) -> str:
    return form["transcriptions"]["numeric"]


def is_proper_form(form: dict) -> bool:
    """A capitalised reading (Bei3 jing1, Huan2): a proper-noun form."""
    return numeric(form)[:1].isupper()


def default_form_index(entry: dict) -> int:
    """The form to show before any sentence picks one: the first lowercase
    reading whose first meaning isn't "surname ...". Falls back to 0, which
    is right for proper nouns like 北京 that only have a capitalised form."""
    for i, form in enumerate(entry["forms"]):
        if not is_proper_form(form) and not _SURNAME_RE.match(form["meanings"][0]):
            return i
    return 0


def word_type_of(entry: dict) -> str:
    """'proper_noun' if every POS tag is a proper-noun tag, 'grammar' if the
    first (most common) tag is a particle, else 'vocab'. Returned as the
    WordType value so this module doesn't import the models."""
    pos = entry.get("pos") or []
    if pos and all(p in PROPER_NOUN_POS for p in pos):
        return "proper_noun"
    if pos and pos[0] in GRAMMAR_POS:
        return "grammar"
    return "vocab"


# --------------------------------- READING MATCH ---------------------------------

def syllables(pinyin) -> list[tuple[str, int]]:
    """Numeric pinyin -> [(letters, tone)]. Accepts "ai4 hao4", "ai4hao4", or a
    list of syllables (pypinyin's lazy_pinyin output). ü, u: and v are all
    folded to "v"."""
    if isinstance(pinyin, (list, tuple)):
        pinyin = " ".join(pinyin)
    p = pinyin.lower().replace("u:", "v").replace("ü", "v")
    out = []
    for letters, tone in _SYLLABLE_RE.findall(p):
        # 儿 written into the syllable ("dianr3", the dataset's style) is
        # split off the way CC-CEDICT writes it ("dian3 r5"). "er" itself
        # (二, 儿) is a real syllable and is never split.
        if letters.endswith("r") and letters != "er" and len(letters) > 1:
            out += [(letters[:-1], int(tone)), ("r", 5)]
        else:
            out.append((letters, int(tone)))
    return out


def _syllable_match(got: tuple[str, int], want: tuple[str, int]) -> bool:
    """`got` = the reading tagging computed, `want` = the JSON form's.
    Lenient on the three systematic disagreements between pypinyin and
    CC-CEDICT: neutral tones (爸爸 ba4 ba5), 儿 endings (哪儿 na3 r5 vs
    na3 er2 vs nar3), and the tone changes of 不 and 一 (不对 bu2 vs bu4)."""
    (gl, gt), (wl, wt) = got, want
    if gl == "er" and wl == "r":
        gl = "r"
    if gl != wl:
        return False
    if gt == wt or wt == 5 or gt == 5:
        # Neutral tone on either side matches: the list marks 爸爸 ba4 ba5,
        # and the dataset marks 在 in 坐在 as neutral "zai".
        return True
    if wl == "bu" and {gt, wt} <= {2, 4}:
        return True
    if wl == "yi" and {gt, wt} <= {1, 2, 4}:
        return True
    return False


def match_forms(entry: dict, pinyin) -> list[int]:
    """Indexes of the entry's forms whose reading matches `pinyin`, best
    first: exact matches before lenient ones, lowercase readings before
    capitalised (surname / proper-noun) ones. Empty list = no form matches."""
    got = syllables(pinyin)
    scored = []
    for i, form in enumerate(entry["forms"]):
        want = syllables(numeric(form))
        if len(want) != len(got):
            continue
        if not all(_syllable_match(g, w) for g, w in zip(got, want)):
            continue
        exact = got == want
        scored.append((not exact, is_proper_form(form), i))
    return [i for *_, i in sorted(scored)]


# CC-CEDICT entries that are notes about the word, not meanings of it:
# 一 "also pr. [yāo] for greater clarity...", 一 '"one" radical in Chinese
# characters', "variant of ...", "see ...". Offering them let Haiku pick
# "also pr. [yāo]..." as the meaning of 一 in 一年有十二个月.
NOTE_RE = re.compile(r'^(also pr\.|also written|variant of|old variant of|see also |see |"[^"]*" radical|radical )',
                     re.IGNORECASE)


def is_note(meaning: str) -> bool:
    return bool(NOTE_RE.match(meaning.strip()))


def candidates(entry: dict, form_indexes: list[int]) -> list[dict]:
    """Every meaning of the given forms, flattened and numbered for the
    sense-matching prompt:
        [{"form_index": 1, "meaning_index": 0, "pinyin": "hai2",
          "english": "still"}, ...]
    Notes (NOTE_RE) are left out, unless a form has nothing else.
    meaning_index stays the position in the JSON."""
    out = []
    for fi in form_indexes:
        form = entry["forms"][fi]
        meanings = list(enumerate(form["meanings"]))
        real = [(mi, m) for mi, m in meanings if not is_note(m)]
        for mi, meaning in (real or meanings):
            out.append({"form_index": fi, "meaning_index": mi,
                        "pinyin": numeric(form), "english": meaning})
    return out


def single_meaning_is_safe(entry: dict, forms: list[int]) -> bool:
    """Whether a reading that leaves ONE meaning can be trusted without AI:
    yes if the word has one form, or the reading picked the word's usual
    form. No if it picked a rare one -- the dataset reads 的 as dí / dì /
    dī in some sentences (目的-style), which would give 'really and truly'
    or "bull's-eye" to an ordinary 的. Those go to resolve_senses, which
    offers that reading first and then every reading."""
    return len(entry["forms"]) == 1 or default_form_index(entry) in forms