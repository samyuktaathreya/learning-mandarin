"""
Adds pinyin to grammar markers in units_output.json using pypinyin.
Grammar markers like 了, 吗, 个 don't have pinyin in units_output.json
because the LLM SOP didn't include it. This script patches them in.

Run this once after parse_textbook.py, before create_questions.py.
"""

import json
import os
from pypinyin import pinyin, Style

INPUT_FILEPATH = "../data/clean"
INPUT_FILENAME = "units_output.json"

# overrides for grammar markers where pypinyin gives wrong conversational tone
PINYIN_OVERRIDES = {
    "不": "bu4",   # citation form; sandhi handled at runtime
    "个": "ge4",
    "了": "le5",
    "吗": "ma5",
    "呢": "ne5",
    "吧": "ba5",
    "啊": "a5",
    "的": "de5",
    "地": "de5",
    "得": "de5",
    "着": "zhe5",
    "过": "guo5",
}


def marker_to_pinyin(marker: str) -> str:
    if marker in PINYIN_OVERRIDES:
        return PINYIN_OVERRIDES[marker]
    result = pinyin(marker, style=Style.TONE3, heteronym=False)
    return ''.join([s[0] for s in result]).lower()


def main():
    filepath = os.path.join(INPUT_FILEPATH, INPUT_FILENAME)
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    updated = 0
    for unit_number, unit_data in data.items():
        for item in unit_data.get("grammar", []):
            if "pinyin" not in item or item.get("pinyin") is None:
                item["pinyin"] = marker_to_pinyin(item["marker"])
                updated += 1
                print(f"  Unit {unit_number}: {item['marker']} -> {item['pinyin']}")

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nUpdated {updated} grammar markers with pinyin.")
    print(f"Now run create_questions.py to regenerate the question bank.")


if __name__ == "__main__":
    main()