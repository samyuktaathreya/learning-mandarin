"""
Reads index_output.json and builds a flat word lookup dictionary,
written to data/clean/hsk1_dictionary.json.

Used by the app for instant character/word lookup when a user clicks
a Chinese character — no external API needed.
"""

import os
import json

INDEX_FILEPATH  = "../data/clean"
INDEX_FILENAME  = "index_output.json"
OUTPUT_FILEPATH = "../data/clean"
OUTPUT_FILENAME = "hsk1_dictionary.json"


def main():
    path = os.path.join(INDEX_FILEPATH, INDEX_FILENAME)
    with open(path, "r", encoding="utf-8") as f:
        index = json.load(f)

    dictionary = {}

    for item in index.get("vocab", []):
        hanzi = item["hanzi"]
        if hanzi not in dictionary:
            dictionary[hanzi] = {
                "pinyin":  item["pinyin"],
                "english": item["english"],
                "type":    "vocab",
                "unit":    item["unit"],
            }

    for item in index.get("grammar", []):
        hanzi = item["hanzi"]
        if hanzi not in dictionary:
            dictionary[hanzi] = {
                "pinyin":  item.get("pinyin"),
                "english": item["english"],
                "type":    "grammar",
                "unit":    item["unit"],
            }

    for item in index.get("proper_nouns", []):
        hanzi = item["hanzi"]
        if hanzi not in dictionary:
            dictionary[hanzi] = {
                "pinyin":  item["pinyin"],
                "english": item["english"],
                "type":    "proper_noun",
                "unit":    item["unit"],
            }

    out_path = os.path.join(OUTPUT_FILEPATH, OUTPUT_FILENAME)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(dictionary, f, ensure_ascii=False, indent=2)

    print(f"Built dictionary: {len(dictionary)} entries")
    print(f"Written to {out_path}")


if __name__ == "__main__":
    main()