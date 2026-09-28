"""
Converts numbered pinyin ("ma3") to diacritic pinyin ("mǎ"), for feeding
TTS a form it reliably reads as one Mandarin syllable rather than
misparsing digits/letters separately (see: "ma2" -> misread as "吗二").

Standard placement rule, not a fixed priority string:
  1. 'a' or 'e' always takes the mark if present.
  2. Else, in "ou", the 'o' takes the mark.
  3. Else, the LAST vowel in the cluster takes the mark (handles "iu" vs
     "ui" marking different vowels depending on order).
A flat priority string ("aoeiuv") gets this wrong for "iu"/"ui" -- this
implements the real rule instead.
"""
import json
from pathlib import Path

TONE_MARKS_PATH = Path(__file__).parent.parent / "data" / "raw" / "tone_marks.json"
with open(TONE_MARKS_PATH, encoding="utf-8") as f:
    TONE_MARKS = json.load(f)


def _find_mark_index(final: str) -> int:
    if "a" in final:
        return final.index("a")
    if "e" in final:
        return final.index("e")
    if "ou" in final:
        return final.index("o")
    for i in range(len(final) - 1, -1, -1):
        if final[i] in "iouv":
            return i
    return 0


def numbered_to_diacritic(numbered: str) -> str:
    """'ma3' -> 'mǎ'. Tone 5 (neutral) gets no mark, base returned as-is."""
    tone = int(numbered[-1])
    base = numbered[:-1]
    if tone == 5:
        return base
    idx = _find_mark_index(base)
    vowel = base[idx]
    marked = TONE_MARKS[vowel][tone - 1]
    return base[:idx] + marked + base[idx + 1:]