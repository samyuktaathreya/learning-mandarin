"""
Diacritic -> numeric pinyin conversion, factored out of vocab_index_parser.py
unchanged (pure string transform, no data-model implications) so it can be
imported by any script without duplicating it. Logic is identical to the
original JSON-based version.
"""
import re

_TONE_TABLE = {}
for base, marks in {
    "a": "āáǎà", "e": "ēéěè", "i": "īíǐì", "o": "ōóǒò", "u": "ūúǔù", "v": "ǖǘǚǜ",
}.items():
    for tone, ch in enumerate(marks, start=1):
        _TONE_TABLE[ch] = (base, tone)
        _TONE_TABLE[ch.upper()] = (base.upper(), tone)
_TONE_TABLE["ü"] = ("v", 0)
_TONE_TABLE["Ü"] = ("V", 0)

_VOWELS = "aeiouv"


def _demark(word: str):
    plain, tones = [], {}
    for ch in word:
        if ch in _TONE_TABLE:
            base, tone = _TONE_TABLE[ch]
            if tone:
                tones[len(plain)] = tone
            plain.append(base)
        else:
            plain.append(ch)
    return "".join(plain), tones


def _split_syllables(plain: str):
    tokens = re.split(r"([ \-''])", plain)
    syllables = []
    for tok in tokens:
        if tok in (" ", "-", "'", "'", ""):
            continue
        i = 0
        n = len(tok)
        while i < n:
            start = i
            two = tok[i:i + 2].lower()
            if two in ("zh", "ch", "sh"):
                i += 2
            elif tok[i].lower() not in _VOWELS:
                i += 1
            vstart = i
            while i < n and tok[i].lower() in _VOWELS:
                i += 1
            if i == vstart:
                if i == start:
                    i = start + 1
                syllables.append(tok[start:i])
                continue
            if i < n and tok[i].lower() == "n":
                if i + 1 < n and tok[i + 1].lower() == "g":
                    if i + 2 >= n or tok[i + 2].lower() not in _VOWELS:
                        i += 2
                    else:
                        i += 1
                elif i + 1 >= n or tok[i + 1].lower() not in _VOWELS:
                    i += 1
            elif i < n and tok[i].lower() == "r" and (i + 1 >= n or tok[i + 1].lower() not in _VOWELS):
                i += 1
            syllables.append(tok[start:i])

    merged = []
    for syl in syllables:
        if syl.lower() == "r" and merged:
            prev = merged[-1]
            if prev.lower().endswith("ng"):
                prev = prev[:-2]
            elif prev.lower().endswith("n"):
                prev = prev[:-1]
            merged[-1] = prev + syl
        else:
            merged.append(syl)
    return merged


def diacritic_to_numeric(pinyin: str) -> str:
    """Convert accented pinyin to the app's numeric form: 'Zhōngguó' -> 'Zhong1guo2'."""
    pinyin = (pinyin or "").strip()
    if not pinyin:
        return ""
    if re.search(r"[1-5]", pinyin):
        return pinyin
    plain, tone_positions = _demark(pinyin)
    syllables = _split_syllables(plain)
    out, cursor = [], 0
    stripped = plain.replace(" ", "").replace("-", "").replace("'", "").replace("’", "")
    tones_stripped = {}
    j = 0
    for i, ch in enumerate(plain):
        if ch in " -'’":
            continue
        if i in tone_positions:
            tones_stripped[j] = tone_positions[i]
        j += 1
    for syl in syllables:
        tone = 5
        for k in range(cursor, cursor + len(syl)):
            if k in tones_stripped:
                tone = tones_stripped[k]
                break
        out.append(f"{syl}{tone}")
        cursor += len(syl)
    result = "".join(out)
    if stripped != "".join(syllables):
        print(f"  [pinyin-warning] syllabification mismatch for '{pinyin}' -> '{result}'")
    return result