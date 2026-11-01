"""
What do hsk-sentences-audio's own grammar fields contain? Read-only.

Prints the raw `grammar_points` / `grammar_tags` of a few example sentences
(the ones from playtesting first), then how often each grammar label appears
across the dataset's HSK 1 and HSK 2 sections. This decides whether the
pipeline can use the dataset's grammar labels instead of (or alongside) its
own rule-based detection.

Run from data_pipelines/textbook/scripts (or debug_scripts):
    python check_hsa_grammar.py
"""
import json
import re
from collections import Counter

from hsk_sentences_audio import load_sentences

EXAMPLES = ["他不怎么爱说话。", "我在一家大公司工作。", "我爱你。", "我来介绍一下我的朋友。"]


def section(s: dict) -> int | None:
    m = re.match(r"hsk(\d+)-", s.get("id", ""))
    return int(m.group(1)) if m else None


def labels(value) -> list[str]:
    """Flatten a grammar field (list of strings, list of dicts, dict, str)
    into short readable labels for counting."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [json.dumps(value, ensure_ascii=False, sort_keys=True)]
    out = []
    for item in value:
        if isinstance(item, dict):
            key = item.get("id") or item.get("code") or item.get("name") or item.get("label")
            out.append(str(key) if key else json.dumps(item, ensure_ascii=False, sort_keys=True))
        else:
            out.append(str(item))
    return out


def main():
    sentences = load_sentences()
    by_text = {s.get("chinese"): s for s in sentences}

    print("=== Example sentences: raw grammar fields ===")
    shown = 0
    for text in EXAMPLES:
        s = by_text.get(text)
        if s is None:
            continue
        shown += 1
        print(f"\n{s['id']}  {text}")
        for field in ("grammar_points", "grammar_tags", "sentence_type"):
            print(f"  {field}: {json.dumps(s.get(field), ensure_ascii=False)}")
    if not shown:
        print("(none of the example sentences found; showing the first 3 HSK 1 sentences)")
        for s in [x for x in sentences if section(x) == 1][:3]:
            print(f"\n{s['id']}  {s.get('chinese')}")
            for field in ("grammar_points", "grammar_tags"):
                print(f"  {field}: {json.dumps(s.get(field), ensure_ascii=False)}")

    for field in ("grammar_points", "grammar_tags"):
        filled = [s for s in sentences if s.get(field)]
        print(f"\n=== {field}: present on {len(filled)}/{len(sentences)} sentences ===")
        for lvl in (1, 2):
            counts = Counter(l for s in sentences if section(s) == lvl for l in labels(s.get(field)))
            print(f"  HSK {lvl} section: {len(counts)} distinct labels. Most common:")
            for label, n in counts.most_common(25):
                print(f"    {n:>4}  {label[:110]}")


if __name__ == "__main__":
    main()