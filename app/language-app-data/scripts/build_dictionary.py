"""
Reads data/clean/units_output.json and builds a flat dictionary of every
vocab word, grammar marker, and proper noun across all units, written to
data/clean/hsk1_dictionary.json.

This is used by the app for instant character/word lookup without any
external API calls. When a user clicks a Chinese character, the app finds
the longest matching word in the dictionary and shows pinyin + english.

Run this whenever units_output.json is regenerated.
"""

import os
import json

INPUT_FILEPATH = "../data/clean"
INPUT_FILENAME = "units_output.json"

OUTPUT_FILEPATH = "../data/clean"
OUTPUT_FILENAME = "hsk1_dictionary.json"


def load_units_output(filepath: str, filename: str) -> dict:
    full_path = os.path.join(filepath, filename)
    with open(full_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_dictionary(dictionary: dict, filepath: str, filename: str):
    full_path = os.path.join(filepath, filename)
    with open(full_path, "w", encoding="utf-8") as f:
        json.dump(dictionary, f, ensure_ascii=False, indent=2)


def build_dictionary(units_output: dict) -> dict:
    dictionary = {}

    for unit_number, unit_data in units_output.items():

        # vocab words
        for item in unit_data.get("vocab", []):
            hanzi = item["hanzi"]
            if hanzi not in dictionary:
                dictionary[hanzi] = {
                    "pinyin": item["pinyin"],
                    "english": item["english"],
                    "type": "vocab",
                    "unit": int(unit_number),
                }

        # grammar markers
        for item in unit_data.get("grammar", []):
            marker = item["marker"]
            if marker not in dictionary:
                dictionary[marker] = {
                    "pinyin": None,  # grammar markers don't have standalone pinyin in units_output
                    "english": item["english"],
                    "type": "grammar",
                    "unit": int(unit_number),
                }

        # proper nouns
        for item in unit_data.get("proper_nouns", []):
            hanzi = item["hanzi"]
            if hanzi not in dictionary:
                dictionary[hanzi] = {
                    "pinyin": item["pinyin"],
                    "english": item["english"],
                    "type": "proper_noun",
                    "unit": int(unit_number),
                }

    return dictionary


def main():
    units_output = load_units_output(INPUT_FILEPATH, INPUT_FILENAME)
    dictionary = build_dictionary(units_output)
    save_dictionary(dictionary, OUTPUT_FILEPATH, OUTPUT_FILENAME)

    print(f"Built dictionary with {len(dictionary)} entries.")
    print(f"Written to {os.path.join(OUTPUT_FILEPATH, OUTPUT_FILENAME)}")

    # show a sample
    sample = list(dictionary.items())[:5]
    for hanzi, data in sample:
        print(f"  {hanzi}: {data}")


if __name__ == "__main__":
    main()