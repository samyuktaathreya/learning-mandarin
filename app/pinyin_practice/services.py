# app/pinyin_practice/services.py
"""
Pinyin dictation: hear a word, type its numbered pinyin ("lv3xing2").

Word choice is random within the user's HSK level and the one above, but
missed words come back. The browser keeps the list of words it got wrong
(and the last few it was shown) and sends both with each request, so there's
no per-user table to migrate.
"""
import json
import random
import re
from functools import lru_cache
from typing import Optional

from pypinyin import pinyin, Style
from pypinyin.contrib.tone_convert import to_tone
from sqlalchemy.orm import Session

from app.core.config.data import DATA_DIR
from app.textbook.models import Vocab
from app.pinyin_practice import crud

# Chance the next word is a missed one (when there's a missed word that
# wasn't just shown).
REVIEW_CHANCE = 0.4
# Don't repeat any of the last N words shown, missed or not, so a wrong
# answer isn't immediately followed by the same word.
RECENT_WINDOW = 3


class PinyinPracticeError(Exception):
    pass


def levels_for(hsk_level: int) -> list[int]:
    """The user's level and the one above (capped at the top level)."""
    return sorted({hsk_level, min(hsk_level + 1, crud.MAX_HSK_LEVEL)})


def pick_word(db: Session, hsk_level: int, missed: list[str], recent: list[str]) -> Vocab:
    recent_set = set(recent[-RECENT_WINDOW:])

    due = [w for w in crud.get_words_by_hanzi(db, missed) if w.hanzi not in recent_set]
    if due and random.random() < REVIEW_CHANCE:
        return random.choice(due)

    ids = crud.get_word_ids_for_levels(db, levels_for(hsk_level))
    if not ids:
        raise PinyinPracticeError(f"No words loaded for HSK {hsk_level}")
    # a few tries to dodge the recent words; with hundreds of words per level
    # the first try almost always works
    for _ in range(10):
        word = crud.get_word_by_id(db, random.choice(ids))
        if word.hanzi not in recent_set:
            return word
    return word


def normalize_pinyin(text: str) -> str:
    """Make typed and stored pinyin comparable:
      "Lv3 Xing2" -> "lü3xing2",  "ba4ba5" / "ba4ba" / "ba4ba0" -> "ba4ba"
    Case, spaces and apostrophes don't matter, v / u: mean ü, and the neutral
    tone can be written 5, 0 or left off.

    Third-tone sandhi: the word list writes some 3-3 words with the sandhi
    tone (可以 ke2yi3) and some without (水果 shui3guo3), so a 2 or 3 before
    a third tone counts as 3 either way."""
    s = text.strip().lower()
    s = s.replace("u:", "ü").replace("v", "ü")
    s = re.sub(r"[\s'’\-]", "", s)
    syllables = re.findall(r"[a-zü]+[0-5]?|[^a-zü]+", s)
    for i in range(len(syllables) - 1):
        if syllables[i][-1] in "23" and syllables[i + 1][-1] == "3":
            syllables[i] = syllables[i][:-1] + "3"
    return re.sub(r"[05]", "", "".join(syllables))


def is_correct(answer: str, pinyin: str) -> bool:
    return bool(answer.strip()) and normalize_pinyin(answer) == normalize_pinyin(pinyin)


def to_tone_marks(pinyin: str) -> str:
    """'lü3you2' -> 'lǚyóu'. to_tone only handles one syllable at a time."""
    return re.sub(r"([a-zü]+[0-5])", lambda m: to_tone(m.group(1)), pinyin, flags=re.IGNORECASE)


def word_payload(word: Vocab) -> dict:
    return {
        "id": word.id,
        "hanzi": word.hanzi,
        "pinyin": word.pinyin,
        "pinyin_marked": to_tone_marks(word.pinyin),
        "english": word.english,
        "hsk_level": word.hsk_level,
    }


def check_answer(db: Session, word_id: int, answer: str) -> Optional[dict]:
    word = crud.get_word_by_id(db, word_id)
    if word is None:
        return None
    return {"correct": is_correct(answer, word.pinyin), **word_payload(word)}


# ------------------------------ tapping mode ------------------------------
#
# Hear one syllable, tap the character (out of 4) that makes it. The
# characters are ones the user has only met INSIDE words: 期 from 星期 at
# HSK 1, where 期 isn't a word on its own. Of those, only the ones with the
# most strokes are asked about (see TAP_TARGETS). After answering, the word
# it came from is shown.
#
# Options must never be ambiguous, so a distractor is dropped if ANY reading
# it has (from any word in the list, or pypinyin's dictionary) sounds like
# the audio -- not just the reading it has in its own example word.

TAP_OPTIONS = 4
# Only the most complex characters are asked about: the TAP_TARGETS with the
# most strokes in the level's pool (ties at the cutoff included). Distractors
# come from the same group, so the answer doesn't stand out as the one
# complicated character among simple ones.
TAP_TARGETS = 40
STROKE_COUNTS_FILE = DATA_DIR / "stroke_counts.json"

_HANZI = re.compile(r"^[一-鿿]+$")
_SYLLABLE = re.compile(r"[a-zü]+[0-5]")

# built once per process from the textbook DB, which doesn't change while
# the server runs (restart after re-running the pipeline)
_tap_index: Optional[dict] = None


def _syllables(pinyin: str) -> list[str]:
    return _SYLLABLE.findall(pinyin.lower())


def _dictionary_readings(char: str) -> set[str]:
    readings = pinyin(char, style=Style.TONE3, heteronym=True, neutral_tone_with_five=True, v_to_u=True)
    return set(readings[0]) if readings else set()


def _citation_reading(char: str, syllables: list[str], i: int) -> Optional[str]:
    """The reading to play for char i of a word, or None if it can't be
    played on its own: neutral tones (子 in 包子) have no stand-alone
    sound, and a reading the dictionary doesn't know is likely a data slip.
    Undoes third-tone sandhi (可 in 可以 ke2yi3 -> ke3)."""
    reading = syllables[i]
    if reading.endswith("5"):
        return None
    known = _dictionary_readings(char)
    if reading in known:
        return reading
    if reading.endswith("2") and i + 1 < len(syllables) and syllables[i + 1].endswith("3"):
        unsandhied = reading[:-1] + "3"
        if unsandhied in known:
            return unsandhied
    return None


def _build_tap_index(db: Session) -> dict:
    """
    {
      "readings": {char: {every reading it has anywhere}},       # for ruling options OUT
      "word_readings": {char: {citation readings used in the word list}},  # for picking them
      "standalone": {char: lowest HSK level it's a one-character word at},
      "known": {char: lowest HSK level of any word it's in, alone or not},
      "occurrences": [(char, reading, word_payload), ...]  # one per char-in-multi-char-word
    }
    Words are stored as payload dicts, not ORM rows, since the cache
    outlives the session they were loaded in.
    """
    readings: dict[str, set[str]] = {}
    word_readings: dict[str, set[str]] = {}
    standalone: dict[str, int] = {}
    known: dict[str, int] = {}
    occurrences = []

    for word in crud.get_all_words(db):
        if not _HANZI.match(word.hanzi):
            continue
        for char in word.hanzi:
            readings.setdefault(char, set())  # so even unaligned words get dictionary readings below
            if word.hsk_level is not None:
                known[char] = min(word.hsk_level, known.get(char, word.hsk_level))
        if len(word.hanzi) == 1:
            if word.hsk_level is not None:
                standalone[word.hanzi] = min(word.hsk_level, standalone.get(word.hanzi, word.hsk_level))
            readings.setdefault(word.hanzi, set()).update(_syllables(word.pinyin))
            word_readings.setdefault(word.hanzi, set()).update(_syllables(word.pinyin))
            continue

        syllables = _syllables(word.pinyin)
        if len(syllables) != len(word.hanzi):
            continue  # can't line pinyin up with characters (e.g. unwritten erhua)
        for i, char in enumerate(word.hanzi):
            readings.setdefault(char, set()).add(syllables[i])
            reading = _citation_reading(char, syllables, i)
            if reading:
                word_readings.setdefault(char, set()).add(reading)
            if reading and word.hsk_level is not None:
                occurrences.append((char, reading, word_payload(word)))

    for char in readings:
        readings[char] |= _dictionary_readings(char)
    return {"readings": readings, "word_readings": word_readings, "standalone": standalone, "known": known, "occurrences": occurrences}


def _get_tap_index(db: Session) -> dict:
    global _tap_index
    if _tap_index is None:
        _tap_index = _build_tap_index(db)
    return _tap_index


def tap_levels_for(hsk_level: int) -> list[int]:
    """The user's level and the one below (tapping is a step easier than typing)."""
    return sorted({max(hsk_level - 1, 1), hsk_level})


def get_tap_pool(db: Session, hsk_level: int) -> dict[str, tuple[str, dict]]:
    """{char: (reading, example word)} for characters from multi-character
    words at the user's level or one below that aren't words by themselves
    at or under the user's level. Each char keeps the reading from its
    lowest-level example word, which is also the word shown after answering."""
    index = _get_tap_index(db)
    levels = set(tap_levels_for(hsk_level))
    pool: dict[str, tuple[str, dict]] = {}
    for char, reading, word in index["occurrences"]:
        if word["hsk_level"] not in levels:
            continue
        if index["standalone"].get(char, crud.MAX_HSK_LEVEL + 1) <= hsk_level:
            continue
        if char not in pool or word["hsk_level"] < pool[char][1]["hsk_level"]:
            pool[char] = (reading, word)
    return pool


def _same_sound(a: str, b: str) -> bool:
    return normalize_pinyin(a) == normalize_pinyin(b)


@lru_cache(maxsize=1)
def _stroke_counts() -> dict[str, int]:
    with open(STROKE_COUNTS_FILE, encoding="utf-8") as f:
        return json.load(f)


def get_tap_targets(pool: dict) -> list[str]:
    """The pool's TAP_TARGETS highest-stroke characters, most strokes first.
    A character missing from the stroke file counts as 0 strokes."""
    strokes = _stroke_counts()
    ranked = sorted(pool, key=lambda c: strokes.get(c, 0), reverse=True)
    if len(ranked) <= TAP_TARGETS:
        return ranked
    cutoff = strokes.get(ranked[TAP_TARGETS - 1], 0)
    return [c for c in ranked if strokes.get(c, 0) >= cutoff]


def _same_syllable_tones(c: str, reading: str, word_readings: dict) -> set[str]:
    """Tones (1-4) other than the answer's that c is read with in the word
    list on the answer's syllable: for qi1, 骑 -> {"2"}. Empty if c never
    uses that syllable. Uses word_readings, not every dictionary reading,
    so a rare one (个 gě) doesn't get a character picked or shown with it."""
    base, tone = reading[:-1], reading[-1]
    return {
        r[-1] for r in word_readings.get(c, ())
        if r[:-1] == base and r[-1] in "1234" and r[-1] != tone
    }


def _display_reading(c: str, reading: str, pool: dict, word_readings: dict) -> str:
    """The reading to show under an option: its same-syllable reading if it
    has one (that's why it was picked), else its reading in its example word.
    Characters from outside the pool are only ever same-syllable picks."""
    tones = _same_syllable_tones(c, reading, word_readings)
    pool_reading = pool[c][0] if c in pool else None
    if tones and not (pool_reading and pool_reading[:-1] == reading[:-1] and pool_reading[-1] in tones):
        return reading[:-1] + min(tones)
    return pool_reading


def pick_distractors(char: str, reading: str, targets: list[str], pool: dict, known: list[str],
                     readings: dict, word_readings: dict) -> list[str]:
    """TAP_OPTIONS - 1 wrong options, none of which can be read as `reading`.

    Best: same syllable, different tone (qí / qǐ / qì for qī), so the
    question is really about hearing the tone -- one per tone where
    possible. Those come from the pool first, then from `known` (any
    character at or under the user's level, single-character words like
    起 included), since the pool alone is too small at low levels. Then any
    other high-stroke character, then the rest of the pool. Within each tier
    the pick is random."""
    def unambiguous(chars):
        return [
            c for c in chars
            if c != char and not any(_same_sound(r, reading) for r in readings.get(c, ()))
        ]

    target_set = set(targets)
    rest_of_pool = [c for c in pool if c not in target_set]
    outside_pool = [c for c in known if c not in pool]
    needed = TAP_OPTIONS - 1
    picked: list[str] = []
    tones_used: set[str] = set()

    same_syllable = []
    for group in (targets, rest_of_pool, outside_pool):
        group_matches = [c for c in unambiguous(group) if _same_syllable_tones(c, reading, word_readings)]
        random.shuffle(group_matches)
        same_syllable += group_matches

    # first pass: only characters that add a tone not yet covered, so the
    # options span as many tones as possible; second pass: any of them
    for new_tones_only in (True, False):
        for c in same_syllable:
            if len(picked) == needed:
                return picked
            tones = _same_syllable_tones(c, reading, word_readings)
            if c in picked or (new_tones_only and tones <= tones_used):
                continue
            picked.append(c)
            tones_used |= tones

    for group in (targets, rest_of_pool):
        others = [c for c in unambiguous(group) if c not in picked]
        random.shuffle(others)
        picked += others[:needed - len(picked)]
        if len(picked) == needed:
            return picked

    raise PinyinPracticeError(f"Not enough distinct-sounding characters for {char}")


def pick_tap_question(db: Session, hsk_level: int, missed: list[str], recent: list[str]) -> dict:
    pool = get_tap_pool(db, hsk_level)
    if len(pool) < TAP_OPTIONS:
        raise PinyinPracticeError(f"Not enough characters for HSK {hsk_level}")
    targets = get_tap_targets(pool)

    recent_set = set(recent[-RECENT_WINDOW:])
    due = [c for c in missed if c in pool and c not in recent_set]
    if due and random.random() < REVIEW_CHANCE:
        char = random.choice(due)
    else:
        candidates = [c for c in targets if c not in recent_set] or targets
        char = random.choice(candidates)
    reading, word = pool[char]

    index = _get_tap_index(db)
    readings, word_readings = index["readings"], index["word_readings"]
    known = [c for c, level in index["known"].items() if level <= hsk_level]
    distractors = pick_distractors(char, reading, targets, pool, known, readings, word_readings)
    options = distractors + [char]
    random.shuffle(options)

    # each option's reading, shown after answering so the tone contrast is visible
    option_pinyin = {c: to_tone_marks(_display_reading(c, reading, pool, word_readings)) for c in distractors}
    option_pinyin[char] = to_tone_marks(reading)

    return {
        "character": char,
        "pinyin": reading,
        "pinyin_marked": to_tone_marks(reading),
        "options": options,
        "option_pinyin": option_pinyin,
        "word": word,
    }
