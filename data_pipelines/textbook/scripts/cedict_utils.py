"""
data_pipelines/textbook/scripts/cedict_utils.py

Shared Chinese dictionary + validated word-segmentation utilities, backed by:
  - cepy_dict (CC-CEDICT) for word validation and authoritative
    pinyin/definitions -- a real dictionary, not an LLM guess, and CEDICT
    already stores COMPOUND pronunciations directly (e.g. 学生 -> "xue2
    sheng5"), so there's no need to derive a compound's tones from its
    individual characters' base readings (which is often wrong -- e.g.
    pypinyin alone would give 生 as sheng1, not the sheng5 it actually
    takes in 学生).
  - jieba for segmenting a string into words when it ISN'T a single
    dictionary entry, so a multi-word phrase (e.g. "太热了" = 太 + 热 + 了,
    three separate words) never gets registered as if it were one vocab
    entry with a made-up combined pronunciation.

WHY VALIDATE JIEBA'S OUTPUT: jieba is a statistical/dictionary segmenter,
not a validator -- fed "太热了" it proposes ["太热", "了"], and "太热" isn't
actually a CEDICT word either (it's a plausible-looking span, not a real
compound). segment_into_words() recursively re-segments any jieba-proposed
piece that isn't itself a real CEDICT entry, falling back to individual
characters only when jieba can't refine a piece any further.

Install: pip install cepy-dict jieba --break-system-packages
"""
import cepy_dict
import jieba

_CEDICT = None  # lazy-built: {hanzi: {"pinyin": "xue2sheng5", "english": "student"}}


def _build_cedict_index() -> dict:
    index = {}
    for entry in cepy_dict.entries():
        _entry_text, _traditional, simplified, pinyin, definitions = entry
        simplified = (simplified or "").strip()
        if not simplified:
            continue
        # CC-CEDICT pinyin is already numeric-tone, space-separated per
        # syllable ("xue2 sheng5") -- just strip spaces and lowercase to
        # match this app's storage format ("xue2sheng5"). Proper nouns are
        # capitalized in CEDICT (e.g. "Zhong1guo2"); lowercase for
        # consistency with how the rest of the pipeline stores pinyin.
        numeric_pinyin = (pinyin or "").replace(" ", "").lower()
        english = definitions[0] if definitions else ""
        # First occurrence wins if a hanzi has multiple entries (polyphonic
        # words like 了) -- CC-CEDICT generally lists the most common
        # reading first. Not a perfect disambiguation without sentence
        # context, but a reasonable default and consistent with how the
        # rest of this pipeline already handles ambiguity.
        index.setdefault(simplified, {"pinyin": numeric_pinyin, "english": english})
    return index


def _get_index() -> dict:
    global _CEDICT
    if _CEDICT is None:
        _CEDICT = _build_cedict_index()
    return _CEDICT


def lookup_word(hanzi: str) -> dict | None:
    """Returns {"pinyin": ..., "english": ...} if `hanzi` is a real CEDICT
    entry, else None. Authoritative -- prefer this over asking Claude or
    falling back to pypinyin whenever it's available."""
    return _get_index().get(hanzi)


def is_known_word(hanzi: str) -> bool:
    return lookup_word(hanzi) is not None


def segment_into_words(hanzi: str) -> list[str]:
    """Splits `hanzi` into real words, validating every result against
    CEDICT rather than trusting jieba's raw output. Only call this on
    strings that already failed is_known_word() -- if the whole string is
    already a real word, don't fragment it."""
    if len(hanzi) <= 1 or is_known_word(hanzi):
        return [hanzi]

    raw_segments = [w for w in jieba.cut(hanzi) if w.strip()]

    # jieba returning the input unchanged means it can't split this span any
    # further -- stop recursing here, fall back to individual characters,
    # rather than looping forever re-feeding it the same string.
    if len(raw_segments) == 1 and raw_segments[0] == hanzi:
        return list(hanzi)

    validated = []
    for seg in raw_segments:
        if len(seg) <= 1 or is_known_word(seg):
            validated.append(seg)
        else:
            validated.extend(segment_into_words(seg))  # recurse to refine further
    return validated