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
(load_hsk_lists.py prints both when they differ). The vocab file supplies
the word's readings and meanings; a word the file doesn't have at all must
give its own: {"儿": {"level": 1, "pinyin": "er2", "meanings": [...]}}.

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
              "patterns": [], "pattern_conflicts": [], "grouped": [], "groups_stale": []}

# MEANING GROUPS = data/raw/meaning_groups.json, written by group_meanings.py
# (one AI call per word, once) and editable by hand. CC-CEDICT lists
# synonyms as separate meanings ("to try to find", "to look for", "to find",
# "to seek"), so each sentence picked a different one and 找 was taught three
# times. A group is one meaning: its texts joined with "; ".
#     {"找": {"forms": [{"reading": "zhao3",
#                        "groups": [["to try to find", "to look for", "to find", "to seek"],
#                                   ["to call on sb"], ["to return", "to give change"]]}]}}
# A word's groups are used only if they hold exactly the word's current
# meanings; otherwise (dictionary changed, or a typo in an edit) the word
# stays ungrouped and load_hsk_lists.py says so.
GROUPS_FILE = "meaning_groups.json"

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


@lru_cache(maxsize=None)
def _load_overrides(path: str) -> dict:
    """{word: {"level": int, ...}} from the overrides file ("_" keys skipped)."""
    p = Path(path)
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        raw = json.load(f)
    return {w: (dict(v) if isinstance(v, dict) else {"level": v}) for w, v in raw.items()
            if not w.startswith("_")}


def _entry_from_override(word: str, o: dict) -> dict | None:
    """An entry for an override word the vocab file doesn't have, built from
    the override's own "pinyin" (numeric, e.g. "er2") and "meanings" (list).
    None if it doesn't give both."""
    if not o.get("pinyin") or not o.get("meanings"):
        return None
    return {"simplified": word, "level": int(o["level"]), "pos": o.get("pos") or [],
            "radical": o.get("radical"), "frequency": None,
            "forms": [{"transcriptions": {"numeric": o["pinyin"]}, "meanings": list(o["meanings"])}]}


@lru_cache(maxsize=None)
def _raw(path: str) -> list:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def groupable_meanings(form: dict) -> list[str]:
    """The meanings of a form that grouping works on (and candidates()
    offers): dictionary notes left out, unless a form has nothing else."""
    meanings = list(form.get("meanings") or [])
    return [m for m in meanings if not is_note(m)] or meanings


@lru_cache(maxsize=None)
def load_groups(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        raw = json.load(f)
    return {w: v for w, v in raw.items() if not w.startswith("_")}


def apply_groups(entry: dict, stored: dict | None) -> dict:
    """The entry with each form's meanings replaced by its groups (one
    meaning per group, texts joined with "; "). Unchanged when there is
    nothing stored or the stored groups don't match the current meanings."""
    if not stored:
        return entry
    stored_forms = stored.get("forms") or []
    if len(stored_forms) != len(entry["forms"]):
        LOAD_NOTES["groups_stale"].append(entry["simplified"])
        return entry
    new_forms = []
    for form, sf in zip(entry["forms"], stored_forms):
        meanings = groupable_meanings(form)
        groups = [[m for m in g if isinstance(m, str)] for g in (sf.get("groups") or [])]
        flat = [m for g in groups for m in g]
        if sf.get("reading") != numeric(form) or sorted(flat) != sorted(meanings):
            LOAD_NOTES["groups_stale"].append(entry["simplified"])
            return entry
        order = {m: i for i, m in enumerate(meanings)}
        groups = sorted((sorted(g, key=order.get) for g in groups if g), key=lambda g: order[g[0]])
        new_forms.append({**form, "meanings": ["; ".join(g) for g in groups]})
    if any(len(f["meanings"]) < len(groupable_meanings(o)) for f, o in zip(new_forms, entry["forms"])):
        LOAD_NOTES["grouped"].append(entry["simplified"])
    return {**entry, "forms": new_forms}


def load_entries(path: str, grouped: bool = True) -> dict:
    """See _load_entries; grouped=False skips the meaning groups (what
    group_meanings.py itself works from)."""
    return _load_entries(path, grouped)


@lru_cache(maxsize=None)
def _load_entries(path: str, grouped: bool) -> dict:
    """{simplified: entry} for every word in the curriculum list: a 2025
    level ("newest-N") or a level override. entry["level"] = that level.
      - readings with identical meanings are merged (_merge_identical_readings)
      - a PATTERN entry (虽然……但是……) is replaced by its pieces, each with
        entry["pattern"]; a sentence only counts a piece at that level when
        it has every piece (db_utils.set_sentence_level). A piece listed as
        a word on its own keeps its own entry.
      - synonyms are one meaning, when meaning_groups.json has the word
        (apply_groups)
    The overrides and meaning-groups files are read from the same folder as
    `path`."""
    for v in LOAD_NOTES.values():
        v.clear()
    overrides = {w: int(o["level"]) for w, o in _load_overrides(str(Path(path).parent / OVERRIDES_FILE)).items()}
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
    # override words the file doesn't have: defined in the override itself, or ignored
    for word in sorted(set(overrides) - {w for w, *_ in LOAD_NOTES["overrides"]}):
        entry = _entry_from_override(word, _load_overrides(str(Path(path).parent / OVERRIDES_FILE))[word])
        if entry is None:
            LOAD_NOTES["override_missing"].append(word)
            continue
        LOAD_NOTES["overrides"].append((word, None, entry["level"]))
        out[word] = entry

    if grouped:
        groups = load_groups(str(Path(path).parent / GROUPS_FILE))
        out = {w: apply_groups(e, groups.get(w)) for w, e in out.items()}

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
    for word, o in _load_overrides(str(Path(path).parent / OVERRIDES_FILE)).items():
        if word not in out and o.get("meanings"):
            out[word] = "; ".join(o["meanings"][:2])      # defined only in the overrides file
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
    if {gl, wl} == {"er", "r"}:
        gl = wl          # 儿 as erhua "r" in the sentence, "er" in the dictionary (or the reverse)
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
                        "pinyin": numeric(form), "english": clean_meaning(meaning)})
    return out


# --------------------------------- MEANING CLEANUP ---------------------------------
# CC-CEDICT meanings carry notes for dictionary readers that make no sense as
# a learner's definition (and break text-to-speech): "(Taiwan pr. [hàn])",
# "(colloquial pr. [nèi])", "(CL:個|个[ge4])", Chinese characters inside a
# note ("you (courteous, as opposed to informal 你)"), "(bound form)".

_CJK = r"[\u3400-\u9fff]"
_PRON_NOTE_RE = re.compile(r"\(?\s*(?:Taiwan |colloquial |also )pr\.\s*\[[^\]]*\]\s*\)?", re.I)
_CL_NOTE_RE = re.compile(r"\(?\s*CL:[^)\]]*\]\s*\)?")
_HANZI_REF_RE = re.compile(rf"\S*{_CJK}\S*\[[^\]]*\]")          # 老頭子|老头子[lao3 tou2 zi5]
_PAREN_RE = re.compile(r"\(([^()]*)\)")
_BOUND_RE = re.compile(r"\(bound form\)\s*", re.I)
_PR_CLAUSE_RE = re.compile(r"\bpr\.\s*\[")                        # "commonly pr. [zhèi]" inside a note


def clean_meaning(text: str) -> str:
    """A dictionary meaning as a learner-facing definition: pronunciation
    and classifier notes removed, Chinese removed from parenthesised notes
    (dropping just the clause that had it), "(bound form)" removed. Falls
    back to the original if nothing would be left."""
    original = (text or "").strip()

    def paren(m):
        inner = m.group(1)
        if re.match(r"\s*note\b", inner, re.I):
            return ""                        # "(Note: In Taiwan, 妳 is used ...)": a usage paragraph
        if _PR_CLAUSE_RE.search(inner):
            return ""                        # "(in this sense, commonly pr. [zhèi], esp. in Beijing)"
        if not re.search(_CJK, inner) and not _PR_CLAUSE_RE.search(inner):
            return m.group(0)
        kept = [part for part in re.split(r",\s*", inner)
                if part.strip() and not re.search(_CJK, part) and not _PR_CLAUSE_RE.search(part)]
        return f"({', '.join(kept)})" if kept else ""
    # whole parenthesised notes first, so a note is dropped in one piece --
    # removing "also pr. [...]" first left "(... for this sense)" behind
    t = _PAREN_RE.sub(paren, original)
    t = _PRON_NOTE_RE.sub("", t)
    t = _CL_NOTE_RE.sub("", t)
    t = _HANZI_REF_RE.sub("", t)
    t = _BOUND_RE.sub("", t)
    t = re.sub(r"\s+([;,.)])", r"\1", t)
    t = re.sub(r"\(\s*\)", "", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" ;,")
    if not t or t.lower() in ("see", "see also", "variant of", "old variant of", "also written"):
        return original                      # a pure cross-reference: nothing to clean it down to
    return t


def single_meaning_is_safe(entry: dict, forms: list[int]) -> bool:
    """Whether a reading that leaves ONE meaning can be trusted without AI:
    yes if the word has one form, or the reading picked the word's usual
    form. No if it picked a rare one -- the dataset reads 的 as dí / dì /
    dī in some sentences (目的-style), which would give 'really and truly'
    or "bull's-eye" to an ordinary 的. Those go to resolve_senses, which
    offers that reading first and then every reading."""
    return len(entry["forms"]) == 1 or default_form_index(entry) in forms