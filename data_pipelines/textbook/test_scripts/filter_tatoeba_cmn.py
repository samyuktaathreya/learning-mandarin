"""
Filter Tatoeba Mandarin (cmn) sentences down to ones you can trust more,
keeping every column from the detailed sentences file plus English translations.

Folder layout this expects:
  textbook/
    data/raw/tatoeba/
      cmn_sentences_detailed.tsv   id, lang, text, owner, date_added, date_modified
      user_languages.csv           lang, skill_level, username, details
      Sentence pairs in Mandarin Chinese-English - <date>.tsv
                                   (optional) cmn_id, cmn_text, eng_id, eng_text
      users_sentences.csv          (optional) username, sentence_id, rating, ...
      tags.csv                     (optional) sentence_id, tag_name
    data/intermediate/tatoeba/
      cmn_filtered.tsv             <- output (folder is created if missing)
    test_scripts/
      filter_tatoeba_cmn.py        <- this file

How a sentence is judged:
  - DROP if anyone rated it -1, or it has a problem tag (@change, @needs native check, ...)
  - KEEP if it has an "OK" tag or a +1 review                      -> reason "approved"
  - KEEP if its owner says they're a native cmn speaker (level 5)  -> reason "native_owner"
  - otherwise drop

Output columns:
  id, lang, text, owner, date_added, date_modified, eng_id, eng_text, reason_kept
A sentence with several English translations gets one row per translation.
A sentence with no translation gets one row with eng_id / eng_text left empty.
"""
import re
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEXTBOOK = HERE.parent
RAW = TEXTBOOK / "data" / "raw" / "tatoeba"
OUT_DIR = TEXTBOOK / "data" / "intermediate" / "tatoeba"

SENTENCES_FILE = RAW / "cmn_sentences_detailed.tsv"
USER_LANGS_FILE = RAW / "user_languages.csv"
PAIRS_GLOB = "Sentence pairs in Mandarin Chinese-English*.tsv"  # newest match is used
REVIEWS_FILE = RAW / "users_sentences.csv"    # optional
TAGS_FILE = RAW / "tags.csv"                  # optional
OUTPUT_FILE = OUT_DIR / "cmn_filtered.tsv"

LANG = "cmn"
REQUIRE_APPROVAL = False   # True = only keep sentences with an OK tag / +1 review
DROP_UNTRANSLATED = False  # True = drop sentences that have no English translation

BAD_TAGS = {
    "@change", "@check", "@needs native check", "@delete", "@fixme",
    "@not a sentence", "@duplicate", "@translation check",
    "vulgar", "archaic", "sexual content", "offensive", "insult",
}
GOOD_TAGS = {"ok"}

DETAIL_COLUMNS = ["id", "lang", "text", "owner", "date_added", "date_modified"]
LANG_CODE = re.compile(r"^[a-z]{2,4}$")


def lines(path):
    """Yield tab-split lines. Plain line reading, no csv module, because
    user_languages.csv has free-text 'details' that span multiple lines and
    contain stray quote marks."""
    with open(path, encoding="utf-8") as f:
        for line in f:
            yield line.rstrip("\n").split("\t")


# 1. Mandarin sentences (keep every column)
sentences = {}  # id -> list of all original columns
for parts in lines(SENTENCES_FILE):
    if len(parts) >= 4 and parts[1] == LANG:
        sentences[parts[0]] = (parts + [""] * len(DETAIL_COLUMNS))[:len(DETAIL_COLUMNS)]
print(f"Loaded {len(sentences):,} {LANG} sentences")

# 2. Self-reported native speakers of cmn
natives = set()
for parts in lines(USER_LANGS_FILE):
    if len(parts) >= 3 and LANG_CODE.match(parts[0]) and parts[0] == LANG and parts[1] == "5":
        natives.add(parts[2])
print(f"Found {len(natives):,} self-reported native {LANG} speakers")

# 3. English translations (optional)
translations = defaultdict(list)  # cmn id -> [(eng_id, eng_text), ...]
pair_files = sorted(RAW.glob(PAIRS_GLOB), key=lambda p: p.stat().st_mtime)
if pair_files:
    PAIRS_FILE = pair_files[-1]  # newest download
    for parts in lines(PAIRS_FILE):
        if len(parts) >= 4 and parts[0] in sentences:
            translations[parts[0]].append((parts[2], parts[3]))
    print(f"Translations from '{PAIRS_FILE.name}': "
          f"{sum(map(len, translations.values())):,} pairs for {len(translations):,} sentences")
elif DROP_UNTRANSLATED:
    raise SystemExit(f"No pairs file matching '{PAIRS_GLOB}' in {RAW}, "
                     f"and DROP_UNTRANSLATED is on, so every sentence would be dropped.")
else:
    print(f"(no pairs file matching '{PAIRS_GLOB}' found, eng columns will be empty)")

# 4. Reviews (optional)
pos, neg = defaultdict(int), defaultdict(int)
if REVIEWS_FILE.exists():
    for parts in lines(REVIEWS_FILE):
        if len(parts) >= 3 and parts[1] in sentences:
            if parts[2] == "1":
                pos[parts[1]] += 1
            elif parts[2] == "-1":
                neg[parts[1]] += 1
    print(f"Reviews: {len(pos):,} sentences rated OK, {len(neg):,} rated not OK")
else:
    print(f"(no {REVIEWS_FILE.name} found, skipping reviews)")

# 5. Tags (optional)
good_tag, bad_tag = set(), set()
if TAGS_FILE.exists():
    for parts in lines(TAGS_FILE):
        if len(parts) >= 2 and parts[0] in sentences:
            tag = parts[1].strip().lower()
            if tag in GOOD_TAGS:
                good_tag.add(parts[0])
            if tag in BAD_TAGS:
                bad_tag.add(parts[0])
    print(f"Tags: {len(good_tag):,} tagged OK, {len(bad_tag):,} with problem tags")
else:
    print(f"(no {TAGS_FILE.name} found, skipping tags)")

# 6. Filter and write
OUT_DIR.mkdir(parents=True, exist_ok=True)
kept_sentences = 0
rows_written = 0
with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    f.write("\t".join(DETAIL_COLUMNS + ["eng_id", "eng_text", "reason_kept"]) + "\n")
    for sid, cols in sentences.items():
        owner = cols[3]
        if neg[sid] or sid in bad_tag:
            continue
        if sid in good_tag or pos[sid]:
            reason = "approved"
        elif not REQUIRE_APPROVAL and owner in natives:
            reason = "native_owner"
        else:
            continue

        pairs = translations.get(sid)
        if not pairs:
            if DROP_UNTRANSLATED:
                continue
            pairs = [("", "")]

        kept_sentences += 1
        for eng_id, eng_text in pairs:
            f.write("\t".join(cols + [eng_id, eng_text, reason]) + "\n")
            rows_written += 1

print(f"Kept {kept_sentences:,} of {len(sentences):,} sentences "
      f"({rows_written:,} rows) -> {OUTPUT_FILE}")