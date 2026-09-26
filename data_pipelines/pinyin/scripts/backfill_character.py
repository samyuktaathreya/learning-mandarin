"""
Backfill PinyinSyllable.character with a reference hanzi for each syllable+tone.

Scores each (character, reading) pair by how often the character is used with
that reading, summed over every CC-CEDICT word it appears in, weighted by
wordfreq. The old approach scored the character as a whole.

Example: 与 appears in hundreds of common words, but almost always as yǔ.
Its yú reading gets almost no score, so 于/鱼 win yu2.
"""

import math
import re
from collections import defaultdict
from functools import lru_cache

from pypinyin import Style, pinyin
from pypinyin.pinyin_dict import pinyin_dict
from wordfreq import zipf_frequency

from app.textbook.db_utils import SessionLocal
from app.textbook.models import PinyinSyllable

CEDICT_PATH = "../data/raw/cedict_1_0_ts_utf8.txt"

LINE_RE = re.compile(r"^(\S+)\s+(\S+)\s+\[([^\]]+)\]\s+/(.+)/$")
HANZI_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
SYLLABLE_RE = re.compile(r"^[a-zv]+[1-5]$")

# Entries whose definitions start with one of these are regional, not
# standard Putonghua, so they never contribute to scores.
NONSTANDARD_RE = re.compile(r"^\((dialect|Tw|Cantonese|old)\b|^Taiwan pr\.")

# Picks scoring below this (as a Zipf value) are still assigned, but they are
# listed at the end so you can glance over them. It doesn't block anything.
REVIEW_BELOW_ZIPF = 2.5

# Hand-picked choices that beat the automatic pick. Keys are normalized
# ("lv4", not "lü4" / "lu:4").
OVERRIDES: dict[str, str] = {
    # "yan1": "烟",
}


def _norm(syllable: str) -> str:
    """One spelling for ü everywhere: CEDICT uses 'u:', pypinyin uses 'v'."""
    return syllable.lower().replace("u:", "v").replace("ü", "v")


@lru_cache(maxsize=None)
def _pypinyin_readings(char: str) -> frozenset[str]:
    """Every reading pypinyin knows for this character, e.g. 与 -> {yu2, yu3, yu4}."""
    result = pinyin(char, heteronym=True, style=Style.TONE3, neutral_tone_with_five=True)
    if not result:
        return frozenset()
    return frozenset(_norm(r) for r in result[0])


def _reading_confirmed(char: str, syllable: str) -> bool:
    """Second opinion from pypinyin. Rejects CEDICT oddities like 工 -> ei1
    (from the 工口 'ero' joke) or 不 -> bu1 (dialect 不儿道). pypinyin rarely
    lists neutral tone, so for tone 5 any tone of the same syllable counts."""
    readings = _pypinyin_readings(char)
    if syllable in readings:
        return True
    if syllable.endswith("5"):
        base = syllable[:-1]
        return any(r[:-1] == base for r in readings)
    return False


@lru_cache(maxsize=None)
def _primary_reading(char: str) -> str | None:
    """pypinyin's default (most common) reading, e.g. 与 -> 'yu3'."""
    result = pinyin(char, style=Style.TONE3, neutral_tone_with_five=True)
    if not result or not result[0]:
        return None
    return _norm(result[0][0])


# Every character seen in CEDICT's simplified column. Filled by
# build_reading_scores() and used to keep traditional forms out of the fallback.
simplified_chars: set[str] = set()


def _pypinyin_index() -> dict[str, set[str]]:
    """{"shuang4": {"淙", "灀", ...}} built from pypinyin's own per-character
    table. Covers readings CEDICT has no entry for."""
    index: dict[str, set[str]] = defaultdict(set)
    for codepoint in pinyin_dict:
        char = chr(codepoint)
        if not HANZI_RE.fullmatch(char):
            continue
        for reading in _pypinyin_readings(char):
            index[reading].add(char)
    return index


def fallback_character(key: str, index: dict[str, set[str]]) -> str | None:
    """Most frequent character pypinyin lists for this reading.

    Restricted to characters CEDICT actually uses as a simplified form.
    A traditional-only variant (磣, traditional of 碜) can carry a high
    overall zipf_frequency earned entirely through its *other*, common
    reading (chen3), while the reading being looked up here (ca4) is an
    obscure heteronym nothing else corroborates. Requiring simplified-form
    status filters those out, since a traditional-only character never
    independently means anything in simplified text.
    """
    candidates = index.get(key)
    if not candidates:
        return None
    simplified_candidates = candidates & simplified_chars
    if not simplified_candidates:
        return None
    return max(simplified_candidates, key=lambda c: zipf_frequency(c, "zh"))


def build_reading_scores(path: str) -> dict[str, dict[str, float]]:
    """Returns {"yu2": {"于": 1.2e6, "鱼": 8.1e5, ...}, ...} using raw
    (non-log) frequencies so they can be summed across words."""
    scores: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    seen: set[tuple[str, str]] = set()
    simplified_chars.clear()

    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            match = LINE_RE.match(line.strip())
            if not match:
                continue

            # Simplified column only. Traditional forms never become candidates.
            _traditional, simplified, pinyin_raw, defs = match.groups()
            if NONSTANDARD_RE.match(defs):
                continue
            syllables = [_norm(s) for s in pinyin_raw.split()]
            chars = list(simplified)

            # Need a clean 1:1 char-to-syllable alignment.
            if len(chars) != len(syllables):
                continue
            if not all(HANZI_RE.fullmatch(c) for c in chars):
                continue
            if not all(SYLLABLE_RE.match(s) for s in syllables):
                continue

            # Several traditional forms can collapse to the same simplified
            # word+reading (乾/干 -> 干 gan1). Count each only once.
            dedupe_key = (simplified, " ".join(syllables))
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            simplified_chars.update(chars)

            # Words wordfreq has never seen still count, just at the lowest
            # possible weight, so a character that only appears in rare
            # compounds (摽) is still a candidate.
            zipf = zipf_frequency(simplified, "zh")
            freq = 10 ** zipf if zipf > 0 else 1.0

            if len(chars) == 1:
                # A lone character's frequency covers all of its readings.
                # Credit it only to the primary reading. Otherwise 与's huge
                # standalone frequency would leak into yu2 and yu4.
                if _primary_reading(simplified) != syllables[0]:
                    continue

            for char, syllable in zip(chars, syllables):
                if _reading_confirmed(char, syllable):
                    scores[syllable][char] += freq

    return scores


def _top(candidates: dict[str, float], n: int = 3) -> list[tuple[str, float]]:
    ranked = sorted(candidates.items(), key=lambda kv: kv[1], reverse=True)[:n]
    return [(c, round(math.log10(f), 2)) for c, f in ranked]


def backfill_character() -> None:
    scores = build_reading_scores(CEDICT_PATH)
    fallback_index = _pypinyin_index()

    db = SessionLocal()
    try:
        rows = db.query(PinyinSyllable).all()
        updated = 0
        unmapped: list[str] = []
        from_fallback: list[tuple[str, str]] = []
        low_score: list[tuple[str, list[tuple[str, float]]]] = []
        rejected: list[tuple[str, str, frozenset[str]]] = []

        for row in rows:
            # Assumes tones are stored 1-5 (5 = neutral), matching CEDICT.
            key = _norm(f"{row.syllable}{row.tone}")
            source = None  # for the rejection log, so it's clear which path erred

            if key in OVERRIDES:
                char, source = OVERRIDES[key], "override"
                low_score_entry = None
            else:
                # 1) CEDICT, counting every word the character appears in,
                #    compounds included (鳔 is scored from 鱼鳔 and 鳔胶).
                candidates = scores.get(key)
                if candidates:
                    char, best_freq = max(candidates.items(), key=lambda kv: kv[1])
                    source = "cedict"
                    low_score_entry = (key, _top(candidates)) if math.log10(best_freq) < REVIEW_BELOW_ZIPF else None
                else:
                    # 2) CEDICT has no word with this reading at all (shuang4),
                    #    so fall back to pypinyin's per-character table.
                    char = fallback_character(key, fallback_index)
                    source = "fallback"
                    low_score_entry = None

            # Final gate, independent of which path produced `char`: pypinyin
            # must actually agree this character can be read this way. This is
            # what catches a bug in any path above (or a bad manual override)
            # rather than trusting each path to have gotten it right.
            if char is not None and not _reading_confirmed(char, key):
                rejected.append((key, char, _pypinyin_readings(char)))
                char = None

            if char is None:
                # 3) Nothing survived. Almost always an invalid syllable
                #    (shiu3, jio2, cuang1), or a bad override/candidate caught
                #    by the gate above.
                row.character = None
                unmapped.append(key)
                continue

            row.character = char
            updated += 1
            if source == "fallback":
                from_fallback.append((key, char))
            elif low_score_entry:
                low_score.append(low_score_entry)

        db.commit()

        print(f"Backfilled {updated} of {len(rows)} rows.")
        if rejected:
            print(f"\n{len(rejected)} candidates rejected by the pypinyin final check "
                  f"(bug upstream, or a bad OVERRIDES entry -- these are now unmapped):")
            for key, char, readings in sorted(rejected):
                print(f"  {key:10} {char}  (pypinyin says: {sorted(readings)})")
        if from_fallback:
            print(f"\n{len(from_fallback)} filled from pypinyin (no CEDICT word has the reading):")
            for key, char in sorted(from_fallback):
                print(f"  {key:10} {char}")
        if low_score:
            print(f"\n{len(low_score)} assigned but rarely used (check, or set OVERRIDES):")
            for key, top in sorted(low_score):
                print(f"  {key:10} {top}")
        if unmapped:
            print(f"\n{len(unmapped)} with no character in either source "
                  f"(probably not real syllables):")
            for key in sorted(unmapped):
                print(f"  {key}")
    finally:
        db.close()


if __name__ == "__main__":
    backfill_character()