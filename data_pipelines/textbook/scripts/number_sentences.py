"""
Generate number-practice sentences and append them to units_output.json.

The textbook teaches 一-十 as a bare vocabulary list and then never uses most
of them in a sentence -- 一, 六, 七, 八, 九 appear in zero extracted sentences,
so they can only ever reach the word-level question tiers and can never
graduate. This fills that gap with generated sentences built from vocab the
unit already teaches.

Two kinds:
  templates  -- real sentences with a number slot (我家有八口人。)
  digit runs -- bare strings of number words (八四七二九), written as
                CHARACTERS not Arabic digits, so every question type built
                from them is coherent: dictation writes the characters,
                E->C produces the characters, C->E reads them back as digits.

Runs after sentence_parser, before create_questions.
"""

import json
import random
from pathlib import Path
from app.core.config.textbook import UNITS_OUTPUT_JSON

NUMBER_UNIT = 5          # the unit that teaches 一-十
SEED = 20260715          # deterministic output across runs

DIGIT_HANZI = "零一二三四五六七八九"
DIGIT_PINYIN = ["ling2", "yi1", "er4", "san1", "si4", "wu3", "liu4", "qi1", "ba1", "jiu3"]

ENGLISH_NUMBERS = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven",
    8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve", 13: "thirteen",
    14: "fourteen", 15: "fifteen", 16: "sixteen", 17: "seventeen", 18: "eighteen",
    19: "nineteen", 20: "twenty", 30: "thirty", 40: "forty", 50: "fifty",
    60: "sixty", 70: "seventy", 80: "eighty", 90: "ninety",
}


def english_number(n: int) -> str:
    if n in ENGLISH_NUMBERS:
        return ENGLISH_NUMBERS[n]
    tens, ones = divmod(n, 10)
    return f"{ENGLISH_NUMBERS[tens * 10]}-{ENGLISH_NUMBERS[ones]}"


def number_to_hanzi(n: int) -> str:
    """Cardinal reading: 8 -> 八, 20 -> 二十, 35 -> 三十五, 15 -> 十五."""
    if n < 10:
        return DIGIT_HANZI[n]
    tens, ones = divmod(n, 10)
    out = ("" if tens == 1 else DIGIT_HANZI[tens]) + "十"
    return out + (DIGIT_HANZI[ones] if ones else "")


def number_to_tags(n: int) -> list:
    """The number's characters as individual tags -- 二十五 -> ['二','十','五'].
    One tag per character so each digit word gets credit for the sentence."""
    return list(number_to_hanzi(n))


# Each template: hanzi/english with {n} slots, the tags around the number,
# and the pinyin pieces. Vocab is restricted to what unit 5 and earlier teach.
TEMPLATES = [
    {
        "hanzi": "我家有{n}口人。",
        "english": "There are {en} people in my family.",
        "before": ["我", "家", "有"],
        "after": ["口", "人"],
        "pinyin_before": ["wo3", "jia1", "you3"],
        "pinyin_after": ["kou3", "ren2"],
        "range": (2, 9),
    },
    {
        "hanzi": "她今年{n}岁了。",
        "english": "She is {en} years old this year.",
        "before": ["她", "今", "年"],
        "after": ["岁", "了"],
        "pinyin_before": ["ta1", "jin1", "nian2"],
        "pinyin_after": ["sui4", "le5"],
        "range": (2, 99),
    },
    {
        "hanzi": "我有{n}个朋友。",
        "english": "I have {en} friends.",
        "before": ["我", "有"],
        "after": ["个", "朋友"],
        "pinyin_before": ["wo3", "you3"],
        "pinyin_after": ["ge4", "peng2you5"],
        "range": (2, 9),
    },
]

PER_TEMPLATE = 3
DIGIT_RUN_COUNT = 3
DIGIT_RUN_LENGTH = 5


def number_pinyin(n: int) -> list:
    """Per-character pinyin for a number, parallel to number_to_tags()."""
    out = []
    for ch in number_to_hanzi(n):
        if ch == "十":
            out.append("shi2")
        else:
            out.append(DIGIT_PINYIN[DIGIT_HANZI.index(ch)])
    return out


def build_template_sentence(tpl: dict, n: int) -> dict:
    hanzi = tpl["hanzi"].format(n=number_to_hanzi(n))
    english = tpl["english"].format(en=english_number(n))
    tags = tpl["before"] + number_to_tags(n) + tpl["after"]
    pinyin = tpl["pinyin_before"] + number_pinyin(n) + tpl["pinyin_after"]
    return {"hanzi": hanzi, "english": english, "tags": tags, "pinyin": " ".join(pinyin)}


def build_digit_run(digits: list) -> dict:
    """A bare string of number words: 八四七二九.

    Written as characters, not Arabic digits, so every question type derived
    from it is coherent -- see the module docstring. english is the digit
    string, which makes C->E a real 'read these characters as numbers'
    exercise and E->C a real 'write these numbers as characters' one."""
    hanzi = "".join(DIGIT_HANZI[d] for d in digits)
    english = "".join(str(d) for d in digits)
    tags = list(hanzi)
    pinyin = [DIGIT_PINYIN[d] for d in digits]
    return {"hanzi": hanzi, "english": english, "tags": tags, "pinyin": " ".join(pinyin)}


def generate(rng: random.Random) -> list:
    sentences = []

    for tpl in TEMPLATES:
        lo, hi = tpl["range"]
        chosen = rng.sample(range(lo, hi + 1), min(PER_TEMPLATE, hi - lo + 1))
        for n in chosen:
            sentences.append(build_template_sentence(tpl, n))

    for _ in range(DIGIT_RUN_COUNT):
        digits = [rng.randint(0, 9) for _ in range(DIGIT_RUN_LENGTH)]
        sentences.append(build_digit_run(digits))

    return sentences


def main():
    rng = random.Random(SEED)

    with open(UNITS_OUTPUT_JSON, encoding="utf-8") as f:
        units_data = json.load(f)

    unit_key = str(NUMBER_UNIT)
    bucket = units_data.setdefault(unit_key, {"sentences": [], "fill_in_the_blank": [], "counts": {}})

    # drop any previously generated sentences so re-running doesn't duplicate
    existing = [s for s in bucket["sentences"] if not s.get("generated")]
    generated = generate(rng)
    for s in generated:
        s["generated"] = True

    bucket["sentences"] = existing + generated

    with open(UNITS_OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(units_data, f, ensure_ascii=False, indent=2)

    print(f"Added {len(generated)} generated number sentence(s) to unit {NUMBER_UNIT}")
    for s in generated:
        print(f"  {s['hanzi']}  ({s['english']})")


if __name__ == "__main__":
    main()