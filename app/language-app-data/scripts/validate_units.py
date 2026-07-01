"""
Post-processing validation and cleanup for index_output.json and units_output.json.
Run after parse_index.py and parse_textbook.py, before create_questions.py.

Fixes:
  1. Deduplicate entries in index_output.json (vocab, grammar, proper_nouns)
  2. Fix 不 tone sandhi in pinyin: bu4 shi4 → bu2 shi4, bu4 qu4 → bu2 qu4, etc.
  3. Remove sentences that end with a comma (truncated/incomplete)
  4. Deduplicate sentences in units_output.json
"""

import os
import re
import json

INDEX_FILEPATH = "../data/clean"
INDEX_FILENAME = "index_output.json"
UNITS_FILEPATH = "../data/clean"
UNITS_FILENAME = "units_output.json"

# tone-4 syllable initials — used to detect when 不 should sandhi to bu2
# a syllable is tone 4 if its trailing digit is 4
TONE4_PATTERN = re.compile(r'[a-zv]+4(?:\s|$)')


def fix_bu_sandhi(pinyin: str) -> str:
    """
    Replace bu4 with bu2 when immediately followed by a tone-4 syllable.
    Works on space-separated pinyin strings like "wo3 bu4 shi4 lao3shi1".
    """
    syllables = pinyin.split()
    result = []
    for i, syl in enumerate(syllables):
        if syl == 'bu4' and i + 1 < len(syllables):
            next_syl = syllables[i + 1]
            # check if next syllable ends in tone 4
            if re.search(r'[a-zv]4$', next_syl):
                result.append('bu2')
                continue
        result.append(syl)
    return ' '.join(result)


def is_truncated(sentence: dict) -> bool:
    """Sentence is truncated if hanzi or english ends with a comma."""
    hanzi   = sentence.get('hanzi', '')
    english = sentence.get('english', '')
    return hanzi.rstrip().endswith('，') or hanzi.rstrip().endswith(',') \
        or english.rstrip().endswith(',')


def dedup_by_key(items: list, key: str) -> tuple[list, int]:
    """Remove duplicates by a given key field. Returns (deduped, count_removed)."""
    seen = set()
    result = []
    removed = 0
    for item in items:
        val = item.get(key)
        if val in seen:
            removed += 1
        else:
            seen.add(val)
            result.append(item)
    return result, removed


def fix_pinyin_in_item(item: dict) -> dict:
    """Apply bu4→bu2 sandhi fix to the pinyin field of a dict."""
    if 'pinyin' in item and item['pinyin']:
        item['pinyin'] = fix_bu_sandhi(item['pinyin'])
    return item


def main():
    # ── index_output.json ─────────────────────────────────────────
    index_path = os.path.join(INDEX_FILEPATH, INDEX_FILENAME)
    with open(index_path, 'r', encoding='utf-8') as f:
        index = json.load(f)

    total_removed = 0

    for section in ['vocab', 'grammar', 'proper_nouns']:
        items = index.get(section, [])

        # fix bu sandhi in pinyin
        items = [fix_pinyin_in_item(item) for item in items]

        # deduplicate by hanzi
        items, removed = dedup_by_key(items, 'hanzi')
        if removed:
            print(f"index {section}: removed {removed} duplicate(s)")
        total_removed += removed

        index[section] = items

    with open(index_path, 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    print(f"index_output.json: {total_removed} duplicate(s) removed, bu sandhi fixed")

    # ── units_output.json ─────────────────────────────────────────
    units_path = os.path.join(UNITS_FILEPATH, UNITS_FILENAME)
    if not os.path.exists(units_path):
        print("units_output.json not found, skipping.")
        return

    with open(units_path, 'r', encoding='utf-8') as f:
        units = json.load(f)

    for unit_str, unit_data in units.items():
        sentences = unit_data.get('sentences', [])
        original_count = len(sentences)

        # fix bu sandhi in pinyin
        sentences = [fix_pinyin_in_item(s) for s in sentences]

        # remove truncated sentences (ending with comma)
        truncated = [s['hanzi'] for s in sentences if is_truncated(s)]
        if truncated:
            print(f"Unit {unit_str}: removing {len(truncated)} truncated sentence(s):")
            for h in truncated:
                print(f"  {h}")
        sentences = [s for s in sentences if not is_truncated(s)]

        # deduplicate by hanzi
        sentences, removed = dedup_by_key(sentences, 'hanzi')
        if removed:
            print(f"Unit {unit_str}: removed {removed} duplicate sentence(s)")

        unit_data['sentences'] = sentences

    with open(units_path, 'w', encoding='utf-8') as f:
        json.dump(units, f, ensure_ascii=False, indent=2)
    print("units_output.json: truncated sentences removed, duplicates removed, bu sandhi fixed")


if __name__ == "__main__":
    main()