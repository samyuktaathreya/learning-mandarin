"""
Diacritic -> numeric pinyin conversion, factored out of vocab_index_parser.py
unchanged (pure string transform, no data-model implications) so it can be
imported by any script without duplicating it. Logic is identical to the
original JSON-based version.

TONE SANDHI: apply_tone_sandhi() bakes spoken-form tone changes into the
stored numeric pinyin (bu4shi4 -> bu2shi4, ni3hao3 -> ni2hao3), per product
decision to store spoken-form rather than citation-form pinyin. It's split
into two independent passes:

  - apply_bu_yi_sandhi(): 不 / 一 sandhi. Purely deterministic and keyed off
    a specific character + its neighbor's tone -- no segmentation ambiguity,
    so this is hand-rolled with no external dependency.

  - apply_third_tone_sandhi(): 3rd-tone-chain sandhi. This genuinely needs
    word/phrase segmentation (a lone 3rd tone behaves differently than one
    in a chain), which pypinyin's tone_sandhi engine already implements.
    Rather than trusting pypinyin's own hanzi->pinyin READINGS (risky for
    polyphonic characters -- see cross_check_pinyin's original rationale),
    this only borrows pypinyin's sandhi-vs-no-sandhi DIFF (which syllable
    positions flip 3->2) and applies that diff to our own already-verified
    pinyin. If our verified base tone at a flipped position isn't actually
    3rd tone, we don't touch it -- that's what keeps this safe against
    pypinyin computing a different reading than what we've verified.
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


try:
    from pypinyin import pinyin as _pypinyin_fn, lazy_pinyin as _lazy_pinyin, Style as _PypinyinStyle
except ImportError:
    _pypinyin_fn = None
    _lazy_pinyin = None


def pypinyin_numeric(word: str) -> str:
    """Independently computed numeric pinyin, straight from hanzi -- no
    diacritic-string parsing involved, so it can't inherit a misplaced tone
    mark from someone else's diacritic text. Used both as a primary source
    for brand-new words and as a cross-check against diacritic-derived
    pinyin from external sources. Returns "" if pypinyin isn't installed."""
    if _pypinyin_fn is None:
        return ""
    syllables = _pypinyin_fn(word, style=_PypinyinStyle.TONE3, neutral_tone_with_five=True)
    return "".join(s[0] for s in syllables)


def cross_check_pinyin(hanzi: str, candidate_pinyin: str) -> str | None:
    """Compares a candidate pinyin (from wherever -- Claude, an external
    diacritic string, etc.) against pypinyin's independent computation.

    Returns a human-readable warning string if they disagree, or None if
    they match (or pypinyin isn't installed / can't be checked). This
    exists specifically for the failure mode where a diacritic source
    string has a tone mark on the WRONG syllable: diacritic_to_numeric()
    converts it "successfully" (valid-looking numeric output, right digit
    COUNT) but the result is semantically wrong, and nothing about the
    output alone looks broken. Comparing against an independently-computed
    source is the only way to catch that class of error.

    `expected` is run through apply_tone_sandhi() before comparison, since
    `candidate_pinyin` is expected to be POST-sandhi (that's what gets
    written to the DB -- see module docstring) while pypinyin_numeric()
    returns citation-form tones. Without this, every word touching 不/一/
    a 3rd-tone chain would falsely "mismatch" here.

    This is advisory, not authoritative -- pypinyin can be wrong too
    (polyphonic characters, context-dependent readings), so callers should
    log/print the warning rather than silently overwrite with pypinyin's
    version. See validate_pinyin.py for a batch version of this same check."""
    if not candidate_pinyin or candidate_pinyin == "UNKNOWN_PINYIN":
        return None
    expected = pypinyin_numeric(hanzi)
    if not expected:
        return None
    expected = apply_tone_sandhi(hanzi, expected)
    if expected == candidate_pinyin:
        return None
    return (f"pinyin mismatch for '{hanzi}': got '{candidate_pinyin}', "
            f"pypinyin expects '{expected}' -- please verify")


# --------------------------------- TONE SANDHI ---------------------------------

def _syllables_with_tones(numeric_pinyin: str) -> list[tuple[str, int]]:
    """'bu4shi4' -> [('bu', 4), ('shi', 4)]"""
    return [(m.group(1), int(m.group(2)))
            for m in re.finditer(r"([a-zA-ZüÜ]+)([1-5])", numeric_pinyin)]


def apply_bu_yi_sandhi(hanzi: str, numeric_pinyin: str) -> str:
    """Applies the two LEXICAL sandhi rules dictionaries/textbooks spell out
    in citation pinyin: 不 (bu4->bu2 before a 4th-tone syllable) and 一
    (yi1->yi2/yi4 depending on what follows).

    Assumes one hanzi char = one syllable in `numeric_pinyin`, true for the
    vast majority of entries. If hanzi/syllable counts don't line up
    (erhua absorption, etc.) it leaves the pinyin untouched rather than
    guess wrong, and warns if a 不/一 was actually in play.
    """
    sylls = _syllables_with_tones(numeric_pinyin)
    if len(sylls) != len(hanzi):
        if "不" in hanzi or "一" in hanzi:
            print(f"  [sandhi-skip] hanzi/syllable count mismatch for "
                  f"'{hanzi}' ({numeric_pinyin}); bu/yi sandhi not applied")
        return numeric_pinyin

    out = list(sylls)
    for i, ch in enumerate(hanzi):
        base, tone = out[i]
        if ch == "不":
            if tone == 4 and i + 1 < len(out):
                out[i] = (base, 2)
        elif ch == "一":
            if tone != 1:
                continue  # already non-1st-tone (e.g. neutral in a fixed
                          # phrase like 想一想) -- trust the source
            if i + 1 >= len(out):
                continue  # word-final/isolated 一 -- no sandhi (第一, 十一)
            next_tone = out[i + 1][1]
            if next_tone == 4:
                out[i] = (base, 2)
            elif next_tone in (1, 2, 3):
                out[i] = (base, 4)
            # next_tone == 5 (neutral): ambiguous, leave as yi1

    return "".join(f"{b}{t}" for b, t in out)


def _third_tone_sandhi_positions(hanzi: str) -> set[int] | None:
    """Uses pypinyin's segmentation-aware sandhi engine to find WHICH
    character positions in `hanzi` shift 3rd tone -> 2nd tone under sandhi.
    Only the DIFF is trusted, not pypinyin's actual readings -- a polyphonic
    character could get a different base tone from pypinyin than what's
    already verified in our DB (see cross_check_pinyin). Returns None if
    pypinyin isn't installed or if pypinyin's own segmentation doesn't line
    up 1:1 with hanzi characters (rare, but safer to bail than guess).
    """
    if _lazy_pinyin is None:
        return None
    baseline = _lazy_pinyin(hanzi, style=_PypinyinStyle.TONE3,
                             neutral_tone_with_five=True, tone_sandhi=False)
    sandhied = _lazy_pinyin(hanzi, style=_PypinyinStyle.TONE3,
                             neutral_tone_with_five=True, tone_sandhi=True)
    if len(baseline) != len(hanzi) or len(sandhied) != len(hanzi):
        return None
    return {
        i for i, (b, s) in enumerate(zip(baseline, sandhied))
        if b[-1:] == "3" and s[-1:] == "2"
    }


def apply_third_tone_sandhi(hanzi: str, numeric_pinyin: str) -> str:
    """Flips 3rd-tone syllables to 2nd tone at whichever positions
    pypinyin's sandhi engine identifies as part of a 3rd-tone chain --
    but only where OUR verified pinyin also has 3rd tone at that position
    (guards against acting on a spot where pypinyin's own possibly-wrong
    base reading happened to be 3rd tone but ours isn't).
    """
    sylls = _syllables_with_tones(numeric_pinyin)
    if len(sylls) != len(hanzi):
        return numeric_pinyin  # can't align source pinyin to hanzi 1:1; leave alone

    positions = _third_tone_sandhi_positions(hanzi)
    if positions is None:
        print(f"  [sandhi-skip] pypinyin unavailable/misaligned; "
              f"3rd-tone sandhi not applied for '{hanzi}'")
        return numeric_pinyin

    out = list(sylls)
    for i in positions:
        base, tone = out[i]
        if tone == 3:
            out[i] = (base, 2)
    return "".join(f"{b}{t}" for b, t in out)


def apply_tone_sandhi(hanzi: str, numeric_pinyin: str) -> str:
    """Full sandhi pipeline, applied to already-verified numeric pinyin:
    不/一 (deterministic, no library needed) then 3rd-tone (segmentation-
    dependent, pypinyin-diff-assisted). Order matters: bu/yi sandhi can
    itself change whether a following syllable still starts a 3rd-tone
    chain, so it runs first."""
    numeric_pinyin = apply_bu_yi_sandhi(hanzi, numeric_pinyin)
    numeric_pinyin = apply_third_tone_sandhi(hanzi, numeric_pinyin)
    return numeric_pinyin