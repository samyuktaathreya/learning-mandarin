"""
Which HSK word list do the hsk-sentences-audio level sections follow?
Read-only: touches no database.

For the dataset's HSK 1 section, and HSK 1+2 sections together, it counts
the distinct words in the sentences (names and numbers counted apart) and
compares them with the three lists in disorganized_hsk_vocab_with_radicals.json:
    old-N    = HSK 2.0        (150 / 300 words)
    new-N    = 2021 standard  (500 / 1,272)
    newest-N = 2025 syllabus  (300 / 500)
The list the dataset follows is the one where nearly every sentence word is
in the list AND nearly every list word appears in the sentences.

Run from data_pipelines/textbook/scripts:
    python check_hsa_levels.py
"""
import json
import re
from collections import Counter
from pathlib import Path

from hsk_sentences_audio import load_sentences

VOCAB = Path(__file__).resolve().parent.parent / "data" / "raw" / "disorganized_hsk_vocab_with_radicals.json"
NUMBER = re.compile(r"^[零〇一二两三四五六七八九十百千万亿几半第\d]+$")
LISTS = {"2.0 (old-)": "old-", "2021 (new-)": "new-", "2025 (newest-)": "newest-"}


def section(s: dict) -> int | None:
    """The dataset's level for a sentence, from its id: "hsk1-0002" -> 1."""
    m = re.match(r"hsk(\d+)-", s.get("id", ""))
    return int(m.group(1)) if m else None


def looks_like_name(gloss: str) -> bool:
    first = (gloss or "").split(";")[0].strip()
    return first[:1].isupper() and not re.match(r"^I\b", first)


def load_lists() -> dict:
    entries = json.load(open(VOCAB, encoding="utf-8"))
    out = {name: {} for name in LISTS}
    for e in entries:
        for name, prefix in LISTS.items():
            for lvl in e.get("level", []):
                if lvl.startswith(prefix) and lvl[len(prefix):].isdigit():
                    n = int(lvl[len(prefix):])
                    out[name][e["simplified"]] = min(n, out[name].get(e["simplified"], n))
    return out


def main():
    lists = load_lists()
    sentences = load_sentences()
    print(f"{len(sentences)} dataset sentences. Fields of one sentence: {sorted(sentences[0])}")
    print("Sentences per section: " + ", ".join(
        f"HSK {k}: {v}" for k, v in sorted(Counter(section(s) for s in sentences).items(), key=str)))

    for level in (1, 2):
        chosen = [s for s in sentences if section(s) is not None and section(s) <= level]
        uses, names, numbers = Counter(), set(), set()
        for s in chosen:
            for t in s.get("tokens") or []:
                w = t["word"]
                if NUMBER.match(w):
                    numbers.add(w)
                elif looks_like_name(t.get("gloss_en", "")):
                    names.add(w)
                else:
                    uses[w] += 1
        words = set(uses)
        title = "HSK 1 section" if level == 1 else "HSK 1 + 2 sections"
        print(f"\n=== {title}: {len(chosen)} sentences, {len(words)} distinct words "
              f"(+ {len(names)} probably names, {len(numbers)} numbers) ===")
        for name, lst in lists.items():
            listed = {w for w, l in lst.items() if l <= level}
            inside = words & listed
            print(f"  {name:<15} list has {len(listed):>5} words at level <= {level}.  "
                  f"Sentence words in it: {len(inside)}/{len(words)} ({len(inside) / max(len(words), 1):.0%}).  "
                  f"List words used in a sentence: {len(listed & words)}/{len(listed)}")

        newest = lists["2025 (newest-)"]
        outside = sorted((w for w in words if newest.get(w, 99) > level), key=lambda w: -uses[w])
        print(f"\n  Sentence words NOT in the 2025 list at level <= {level} ({len(outside)}), most used first:")
        for w in outside[:30]:
            levels = ", ".join(f"{n.split()[0]}: {lists[n].get(w, '-')}" for n in lists)
            print(f"    {w:<6} used {uses[w]:>3}x   ({levels})")
        if names:
            print(f"  Probably names: {' '.join(sorted(names))}")


if __name__ == "__main__":
    main()