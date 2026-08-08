"""
Shared Chinese-language helpers: pinyin conversion, syllable/sound
segmentation, tone matching, and hanzi-first speaking-sentence grading.

Extracted from audio.py so audio.py, grading.py, and session.py can all import
these *down* from one place instead of reaching laterally into each other.
No FastAPI, no Azure, no network -- pure text logic.
"""

import re
from pypinyin import pinyin, Style
from sqlalchemy.orm import Session
from textbook import crud

# ----------------------------- OVERRIDES / DICT -----------------------------

PINYIN_OVERRIDES = {
    "谁": "shei2",
}

# Azure STT normalizes spoken numbers to Arabic digits -- say 一三四六五 and it
# transcribes "13465", say 五十 and it writes "50". Rather than fight that, we
# convert the EXPECTED hanzi into the same digit form before comparing, so both
# sides speak Azure's dialect.
_NUMBER_HANZI = {
    "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_NUMBER_UNITS = {"十": 10, "百": 100, "千": 1000}
_NUMBER_CHARS = set(_NUMBER_HANZI) | set(_NUMBER_UNITS)


def _number_run_to_int(run: str) -> int:
    """Cardinal reading of a number-character run: 五十 -> 50, 二十五 -> 25,
    十五 -> 15, 一百 -> 100."""
    total, current = 0, 0
    for ch in run:
        if ch in _NUMBER_HANZI:
            current = _NUMBER_HANZI[ch]
        else:
            unit = _NUMBER_UNITS[ch]
            total += (current or 1) * unit    # 十五 has an implicit leading 一
            current = 0
    return total + current


def hanzi_numbers_to_digits(text: str) -> str:
    """Rewrite runs of number characters as the Arabic digits Azure would
    return. A run containing 十/百/千 is a cardinal number (五十 -> "50");
    a run of bare digit characters is read digit-by-digit (一三四六五 ->
    "13465"), matching how each is actually spoken."""
    out, i = [], 0
    while i < len(text):
        if text[i] not in _NUMBER_CHARS:
            out.append(text[i])
            i += 1
            continue
        j = i
        while j < len(text) and text[j] in _NUMBER_CHARS:
            j += 1
        run = text[i:j]
        if any(ch in _NUMBER_UNITS for ch in run):
            out.append(str(_number_run_to_int(run)))
        else:
            out.append("".join(str(_NUMBER_HANZI[ch]) for ch in run))
        i = j
    return "".join(out)


# ----------------------------- PUNCTUATION / PINYIN -----------------------------

def strip_punct(text: str) -> str:
    return re.sub(r'[。？！，、；：""\'\'…\s]', '', text)


def to_numbered_pinyin(text: str) -> str:
    text = strip_punct(text)
    if text in PINYIN_OVERRIDES:
        return PINYIN_OVERRIDES[text]

    result = pinyin(text, style=Style.TONE3, heteronym=False)
    parts = []
    for i, syllables in enumerate(result):
        char = text[i] if i < len(text) else ''
        if char in PINYIN_OVERRIDES:
            parts.append(PINYIN_OVERRIDES[char])
        else:
            parts.append(syllables[0])
    return ''.join(parts).lower()


def char_to_pinyin(textbook_db: Session, ch: str) -> str:
    """Single character -> numeric pinyin. DB lookup first (crud.py's
    cached get_pinyin_for_word), pypinyin fallback for characters not in
    Vocab (e.g. punctuation-adjacent or never-taught chars)."""
    pinyin = crud.get_pinyin_for_word(textbook_db, ch)
    if pinyin:
        return pinyin
    return to_numbered_pinyin(ch)


def _base_tone(syllable: str):
    """'ta1' -> ('ta','1'); bare 'de' -> ('de','5')."""
    if syllable and syllable[-1] in '12345':
        return syllable[:-1], syllable[-1]
    return syllable, '5'

# ----------------------------- SYLLABLE / SOUND SEGMENTATION -----------------------------

VALID_INITIALS = ['zh', 'ch', 'sh', 'b', 'p', 'm', 'f', 'd', 't', 'n', 'l',
                  'g', 'k', 'h', 'j', 'q', 'x', 'r', 'z', 'c', 's', 'y', 'w']

VALID_FINALS = ['iang', 'iong', 'uang', 'ueng', 'uan', 'uen', 'uai', 'ing',
                'ang', 'eng', 'ong', 'ian', 'iao', 'ie', 'in', 'an', 'en',
                'ao', 'ou', 'ai', 'ei', 'ia', 'ua', 'uo', 'ui', 'un', 'iu',
                've', 'vn', 'a', 'o', 'e', 'i', 'u', 'v', 'er', 'ng']

# Sounds gated behind pronunciation practice because they have no English
# equivalent (see session.py's speaking-sentence gate). Everything else is
# assumed learnable by ear/spelling alone.
GATED_INITIALS = {'zh', 'ch', 'sh', 'r', 'j', 'q', 'x', 'z', 'c'}
GATED_FINALS = {'v', 'er', 'e'}
GATED_SOUNDS = GATED_INITIALS | GATED_FINALS

# Initials after which numeric pinyin spells the ü sound as a plain "u" instead
# of "v" (e.g. "qu4", not "qv4"). "nu"/"nv" (努 vs 女) are genuinely different
# finals and must NOT be collapsed, so the swap below only fires for an exact
# bare "u" final after these initials.
HIDDEN_V_INITIALS = {'j', 'q', 'x', 'y'}


def _match_pinyin_syllables(p: str):
    """Core longest-match segmentation shared by split_pinyin_syllables() and
    split_pinyin_sounds(). Yields (initial, final, tone) per syllable; initial
    is '' for syllables with no initial consonant."""
    i = 0
    p = p.lower()

    while i < len(p):
        if not (p[i].isalpha() or p[i] == 'v'):
            i += 1
            continue

        matched = False
        for init_len in [2, 1, 0]:
            if matched:
                break
            initial = p[i:i + init_len] if init_len > 0 else ''
            if init_len > 0 and (i + init_len > len(p) or initial not in VALID_INITIALS):
                continue
            rest_start = i + init_len
            for final in sorted(VALID_FINALS, key=len, reverse=True):
                end = rest_start + len(final)
                if p[rest_start:end] == final:
                    if end < len(p) and p[end] in '12345':
                        tone = p[end]
                        i = end + 1
                    else:
                        tone = '5'
                        i = end
                    yield initial, final, tone
                    matched = True
                    break
        if not matched:
            i += 1


def split_pinyin_syllables(p: str) -> list:
    return [(initial + final, tone) for initial, final, tone in _match_pinyin_syllables(p)]


def split_pinyin_sounds(p: str) -> list:
    """Like split_pinyin_syllables(), but keeps each syllable's initial and
    final separate, and normalizes the hidden-ü spelling (ju/qu/xu/yu ->
    treat the "u" as "v"; nu/nv stays contrastive). Returns (initial, final,
    tone) tuples."""
    result = []
    for initial, final, tone in _match_pinyin_syllables(p):
        if initial in HIDDEN_V_INITIALS and final == 'u':
            final = 'v'
        result.append((initial, final, tone))
    return result

# ----------------------------- TONE MATCHING -----------------------------

def apply_tone_sandhi(syllables: list) -> list:
    result = list(syllables)
    for i in range(len(result) - 1):
        base, tone = result[i]
        _, next_tone = result[i + 1]
        if base == 'bu' and tone == '4' and next_tone == '4':
            result[i] = (base, '2')
        elif base == 'yi' and tone == '1':
            if next_tone == '4':
                result[i] = (base, '2')
            elif next_tone in ('1', '2', '3'):
                result[i] = (base, '4')
        elif tone == '3' and next_tone == '3':
            result[i] = (base, '2')
    return result


def tones_match(t_pinyin: str, e_pinyin: str) -> bool:
    t_sylls = split_pinyin_syllables(t_pinyin)
    e_sylls = split_pinyin_syllables(e_pinyin)
    if len(t_sylls) != len(e_sylls):
        return False
    t_sandhi = apply_tone_sandhi(t_sylls)
    e_sandhi = apply_tone_sandhi(e_sylls)
    for (t_base, t_tone), (e_base, e_tone) in zip(t_sandhi, e_sandhi):
        if t_base != e_base:
            return False
        if e_tone == '5':
            continue
        if t_tone != e_tone:
            return False
    return True

# ----------------------------- SPEAKING-SENTENCE GRADING -----------------------------

def grade_speaking_sentence(transcription: str, expected_hanzi: str, textbook_db: Session) -> bool:

    t = strip_punct(hanzi_numbers_to_digits(transcription))
    e = strip_punct(hanzi_numbers_to_digits(expected_hanzi))

    if t == e:
        return True
    if len(t) != len(e):
        return False

    for tc, ec in zip(t, e):
        if tc == ec:
            continue
        tb, tt = _base_tone(char_to_pinyin(textbook_db, tc))
        eb, et = _base_tone(char_to_pinyin(textbook_db, ec))
        if tb != eb:
            return False
        if tt != et:
            if tt == '5' or et == '5':
                continue
            return False
    return True