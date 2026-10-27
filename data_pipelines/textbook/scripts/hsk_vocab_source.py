"""
Read-only access to the HSK vocabulary list:
data/raw/disorganized_hsk_vocab_with_radicals.json (complete-hsk-vocabulary
-- MIT; definitions from CC-CEDICT, CC BY-SA 4.0).

LEVELS = the 2025 exam syllabus, the file's "newest-N" tags (HSK 1 ~300
words, HSK 1-2 ~500). The file also tags "new-N" (2021 standard) and "old-N"
(HSK 2.0); those don't set levels, but every word in the file under ANY
version counts as a real word (load_known_words), which tag_sentences.py
uses to decide how to split tokens.

LEVEL OVERRIDES = data/raw/hsk_level_overrides.json, hand-written, for the
few words the 2025 list leaves out but learners clearly know at a level
(没 is missing even though 没有 is HSK 1):
    {"没": {"level": 1, "why": "没有 is HSK 1"}, ...}
An override sets the word's level whether or not the 2025 list has one
(load_hsk_lists.py prints both when they differ). The word must be in the
file, which supplies its readings and meanings.

RADICALS = the same file's "radical" field: one per word, the first
character's for a multi-character word.

FILE FORMAT: a JSON list of entries:
    {"simplified": "爱好", "radical": "爫", "frequency": 4902,
     "level": ["newest-2", "new-1", "old-3"], "pos": ["n", "v"],
     "forms": [{"traditional": "愛好",
                "transcriptions": {"pinyin": "ài hào", "numeric": "ai4 hao4", ...},
                "meanings": ["to like; to be fond of; ...", "interest; hobby"],
                "classifiers": ["个"]}]}
"""
import json
import re
from functools import lru_cache
from pathlib import Path

VOCAB_FILE = "disorganized_hsk_vocab_with_radicals.json"
OVERRIDES_FILE = "hsk_level_overrides.json"
LEVEL_PREFIX = "newest-"
MAX_LEVEL = 7                # newest-7 = levels 7-9, if the file has it

# Readings with a capital letter are proper-noun forms, usually a surname
# (还 has "Huan2: surname Huan" listed before "hai2: still").
_SURNAME_RE = re.compile(r"^(surname|old variant|variant of)\b", re.I)
_SYLLABLE_RE = re.compile(r"([a-zü:v]+)([1-5])")

# JSON POS tags that mark a proper noun (person, place, organisation, other)
# and a grammar word (particle, modal particle).
PROPER_NOUN_POS = {"nr", "ns", "nt", "nz"}
GRAMMAR_POS = {"u", "y"}

# What load_entries() did beyond the plain list, for load_hsk_lists.py to print.
LOAD_NOTES = {"overrides": [], "override_missing": [], "merged_readings": [],
              "patterns": [], "pattern_conflicts": []}

_PATTERN_SPLIT_RE = re.compile(r"[…⋯.。～~\s]+")


def pattern_pieces(word: str) -> list[str]:
    """虽然……但是…… -> ["虽然", "但是"]; [word] for an ordinary word."""
    pieces = [p for p in _PATTERN_SPLIT_RE.split(word) if p]
    return pieces if len(pieces) > 1 else [word]


def _version_level(entry: dict, prefix: str) -> int | None:
    """The entry's level under one version ("newest-", "new-", "old-"),
    matched exactly: "new-" doesn't match "newest-2"."""
    levels = [int(l[len(prefix):]) for l in entry.get("level", [])
              if l.startswith(prefix) and l[len(prefix):].isdigit()]
    return min(levels) if levels else None


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", " ", (text or "").lower()).strip()


def _merge_identical_readings(entry: dict) -> dict:
    """Drops a form (reading) whose meanings are exactly an earlier form's
    (谁 shui2 / shei2, both "who"): one meaning, not two."""
    kept, seen = [], set()
    for form in entry.get("forms") or []:
        key = tuple(sorted(_norm(m) for m in form.get("meanings", []) if not is_note(m)))
        if key and key in seen:
            LOAD_NOTES["merged_readings"].append((entry["simplified"], numeric(form)))
            continue
        seen.add(key)
        kept.append(form)
    return {**entry, "forms": kept}


def _pattern_piece_entries(pattern: str, entry: dict) -> list[dict]:
    """One entry per piece of a pattern (虽然……但是…… -> 虽然, 但是), sharing
    the pattern's level and meanings, with "pattern" = the whole pattern.
    The first form's pinyin is shared out one syllable per character; if
    the counts don't line up, the pieces get no reading."""
    pieces = pattern_pieces(pattern)
    form0 = entry["forms"][0]
    sylls = [f"{l}{t}" for l, t in syllables(numeric(form0))]
    readings = [""] * len(pieces)
    if len(sylls) == sum(len(p) for p in pieces):
        k, readings = 0, []
        for p in pieces:
            readings.append(" ".join(sylls[k:k + len(p)]))
            k += len(p)
    return [{"simplified": piece, "level": entry["level"], "pos": ["u"], "frequency": entry.get("frequency"),
             "radical": None, "pattern": pattern,
             "forms": [{"transcriptions": {"numeric": reading}, "meanings": list(form0["meanings"])}]}
            for piece, reading in zip(pieces, readings)]


def _load_overrides(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return {w: int(v["level"] if isinstance(v, dict) else v) for w, v in raw.items()
            if not w.startswith("_")}


@lru_cache(maxsize=None)
def _raw(path: str) -> list:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=None)
def load_entries(path: str) -> dict:
    """{simplified: entry} for every word in the curriculum list: a 2025
    level ("newest-N") or a level override. entry["level"] = that level.
      - readings with identical meanings are merged (_merge_identical_readings)
      - a PATTERN entry (虽然……但是……) is replaced by its pieces, each with
        entry["pattern"]; a sentence only counts a piece at that level when
        it has every piece (db_utils.set_sentence_level). A piece listed as
        a word on its own keeps its own entry.
    The overrides file is read from the same folder as `path`."""
    for v in LOAD_NOTES.values():
        v.clear()
    overrides = _load_overrides(Path(path).parent / OVERRIDES_FILE)
    out = {}
    for e in _raw(path):
        word = (e.get("simplified") or "").strip()
        if not word or not e.get("forms"):
            continue
        level = _version_level(e, LEVEL_PREFIX)
        if word in overrides:
            LOAD_NOTES["overrides"].append((word, level, overrides[word]))
            level = overrides[word]
        if level is None:
            continue
        if word in out and out[word]["level"] <= level:
            continue                       # listed twice: keep the lower level
        out[word] = _merge_identical_readings({**e, "simplified": word, "level": level})
    LOAD_NOTES["override_missing"] = sorted(set(overrides) - {w for w, *_ in LOAD_NOTES["overrides"]})

    for word in [w for w in out if len(pattern_pieces(w)) > 1]:
        entry = out.pop(word)
        LOAD_NOTES["patterns"].append((word, pattern_pieces(word)))
        for piece_entry in _pattern_piece_entries(word, entry):
            piece = piece_entry["simplified"]
            if piece in out:
                LOAD_NOTES["pattern_conflicts"].append((word, piece))
                continue
            out[piece] = piece_entry
    return out


@lru_cache(maxsize=None)
def load_known_words(path: str) -> dict:
    """Every word in the file under ANY version (2025, 2021, 2.0) -> a short
    gloss. A word here is a real word even if the curriculum list doesn't
    have it."""
    out = {}
    for e in _raw(path):
        word, forms = e.get("simplified"), e.get("forms") or []
        if not word or not forms or word in out:
            continue
        form = forms[default_form_index(e)]
        out[word] = "; ".join([m for m in form.get("meanings", []) if not is_note(m)][:2])
    return out


def radical_of(entry: dict) -> str | None:
    return (entry.get("radical") or "").strip() or None


def level_of(entry: dict) -> int | None:
    return entry.get("level")


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
        if not is_proper_form(form) and form.get("meanings") and not _SURNAME_RE.match(form["meanings"][0]):
            return i
    return 0


def word_type_of(entry: dict) -> str:
    """'proper_noun' if every POS tag is a proper-noun tag, 'grammar' if the
    first (most common) tag is a particle or the word is a pattern piece,
    else 'vocab'. Returned as the WordType value so this module doesn't
    import the models."""
    pos = entry.get("pos") or []
    if entry.get("pattern"):
        return "grammar"
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