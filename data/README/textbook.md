# textbook.db Schema & Architecture

Complete reference for the SQLite curriculum database that backs the Mandarin learning app. Replaces the old JSON-based system (index_output.json, units_output.json, unit_questions_hsk1.json, etc.) with a normalized SQL schema.

---

## Overview

**textbook.db** is the single source of truth for:
- Vocabulary definitions (hanzi, pinyin, english, word type)
- Units and their structure (HSK levels, unit numbers)
- Sentences (with full tagging and pinyin)
- Grammar tips and which sentences demonstrate them
- The complete question bank (vocab, sentence, FITB, etc.)

It's separate from **mandarin_app.db** (session/progress data, user tracking, review state), which is a distinct database for app state.

---

## Tables

### `units`
Curriculum units, scoped to HSK levels. Unit numbering restarts per level (HSK1 unit 1, HSK2 unit 1, etc.).

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PRIMARY KEY | Surrogate key |
| `unit_number` | INTEGER | Printed unit number (1-15 per level) |
| `title` | TEXT | Optional unit title |
| `hsk_level` | INTEGER | HSK level (1, 2, 3, ...). Default: 1 |

**Unique Constraint:** `(unit_number, hsk_level)` — no duplicate unit numbers within a level.

**Why two-part uniqueness?** HSK2's "unit 1" is a different curriculum section from HSK1's "unit 1", taught at different times. The composite key prevents ambiguity.

---

### `vocab`
Single source of truth for every taught hanzi word. **Globally unique by hanzi** — one row per word across all HSK levels.

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PRIMARY KEY | Surrogate key |
| `hanzi` | TEXT | The word itself (e.g., "你", "学习"). UNIQUE, indexed |
| `pinyin` | TEXT | Numeric-tone format: "ni3", "xue2xi2" (no spaces within a compound) |
| `english` | TEXT | Definition/translation |
| `word_type` | ENUM | One of: `vocab`, `grammar`, `proper_noun`, `auto` |
| `unit_id` | INTEGER FK | Foreign key to `units.id`. Nullable for `auto` words |

**word_type breakdown:**
- `vocab`: Regular vocabulary
- `grammar`: Particles, auxiliary markers (POS-classified from the printed index)
- `proper_noun`: Names, place names
- `auto`: Unknown words auto-created during sentence tagging (unit=NULL, filled in later if the word is formalized)

**Key design:** No `hsk_level` column on Vocab. Instead, it reaches HSK level *indirectly* through `unit_id → units.hsk_level`. A word has one home unit across all levels (the earliest unit it appears in); the same hanzi string is never taught twice as separate rows.

---

### `sentence`
Full sentences from textbooks/workbooks, with translations and phonetic breakdown.

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PRIMARY KEY | Surrogate key |
| `unit_id` | INTEGER FK | Foreign key to `units.id` |
| `hanzi` | TEXT | Chinese sentence |
| `english` | TEXT | English translation |
| `pinyin` | TEXT | Full pinyin breakdown: "Ni3 hao3. Wo3 hen3 hao3." (spaces between syllables, punctuation preserved) |
| `source` | TEXT | "textbook" or "workbook" |

**Unique Constraint:** `(unit_id, hanzi)` — no duplicate sentences within a unit.

**Note on unit_id:** Sentences can be "rehomed" to an earlier unit if all their words are taught earlier (see `rehome_sentences()` in db_utils.py). The `unit_id` reflects the *current* home, not the physical location in the PDF.

---

### `sentence_vocab`
Many-to-many join table: sentences ↔ vocabulary words. Replaces the old `tags: [str]` arrays.

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PRIMARY KEY | Surrogate key |
| `sentence_id` | INTEGER FK | Foreign key to `sentences.id` |
| `vocab_id` | INTEGER FK | Foreign key to `vocab.id` |
| `position` | INTEGER | Order of this tag within the sentence (0, 1, 2, ...) |

**Unique Constraint:** `(sentence_id, position)` — one tag per position per sentence.

**Why position instead of (sentence_id, vocab_id)?** Words can appear multiple times in a sentence (e.g., "我不是老师，我是学生，我是中国人" has 是 three times). This constraint allows it; a sentence-vocab pair keyed only on IDs would reject duplicates.

**Reading tags:** To get a sentence's tags in order, join and order by position:
```python
tags = db.query(Vocab.hanzi)
    .join(SentenceVocab)
    .filter(SentenceVocab.sentence_id == sentence_id)
    .order_by(SentenceVocab.position)
    .all()
```

---

### `fitb_questions`
Fill-in-the-blank questions extracted from workbooks.

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PRIMARY KEY | Surrogate key |
| `unit_id` | INTEGER FK | Foreign key to `units.id` |
| `sentence_id` | INTEGER FK | Foreign key to `sentences.id`. Nullable — best-effort match |
| `question` | TEXT | The prompt with `___` blanks: "我是___。" |
| `answer` | TEXT | The answer word: "学生" |
| `full_sentence` | TEXT | Complete sentence with answer filled in: "我是学生。" |

**Unique Constraint:** `(unit_id, question, answer)` — no duplicate FITB question/answer pairs within a unit.

---

### `grammar_tips`
Grammar explanations/rules extracted from the textbook's Notes sections.

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PRIMARY KEY | Surrogate key |
| `unit_id` | INTEGER FK | Foreign key to `units.id` |
| `raw_text` | TEXT | Original scraped tip text (for dedup/idempotency) |
| `content_json` | TEXT | Structured JSON: `{"sections": [{"title": "...", "body": "...", "table": {...}}, ...]}` |

**Unique Constraint:** `(unit_id, raw_text)` — no duplicate raw tips within a unit. This is the idempotency key: re-running the grammar extraction won't create duplicate rows or re-call the Claude reformatting agent.

**Note on content_json:** This is a complete nested document (sections vary in count, tables are optional and variable-width), so it's stored as JSON text rather than normalized into separate columns. Parse with `json.loads()`.

---

### `sentence_grammar`
Many-to-many join table: sentences ↔ grammar tips.

| Column | Type | Notes |
|--------|------|-------|
| `sentence_id` | INTEGER FK | Foreign key to `sentences.id`. Part of composite PK |
| `grammar_tip_id` | INTEGER FK | Foreign key to `grammar_tips.id`. Part of composite PK |

**Primary Key:** `(sentence_id, grammar_tip_id)` — composite, no surrogate key.

**Semantics:** One sentence can demonstrate multiple grammar tips, and one tip can be demonstrated by multiple sentences. Exactly like the old `sentence["grammar_tip"]: [tip, tip, ...]` arrays, but normalized.

---

### `questions`
The final question bank, generated by `create_questions.py`. One row per unique (unit, type, question, answer) tuple.

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PRIMARY KEY | Surrogate key (real DB id) |
| `unit_id` | INTEGER FK | Foreign key to `units.id` |
| `legacy_id` | TEXT | Optional legacy question ID (e.g., "u3_speaking_vocab_2") for backward compat. UNIQUE |
| `question_type` | TEXT | E.g., "listening vocab", "translate english sentence to chinese", "fill in the blank" |
| `question` | TEXT | The prompt shown to the learner |
| `answer` | TEXT | The expected answer |
| `vocab_id` | INTEGER FK | Foreign key to `vocab.id`. Nullable — only set for word-level questions |
| `sentence_id` | INTEGER FK | Foreign key to `sentences.id`. Nullable — only set for sentence-level questions |

**Unique Constraint:** `(legacy_id)` — preserves backward compat if something external references old question IDs.

**Key design insight:**
- **Word questions** (listening vocab, translate word, etc.) have `vocab_id` set. The single word being tested.
- **Sentence questions** (listening sentence, translate sentence) have `sentence_id` set. The tested word is *every* word in that sentence, recovered via `sentence.sentence_vocab_links` at read time.
- **FITB questions** may have either or neither (best-effort sentence link if matched during generation).

Why not store tags on every question row? Sentence questions test multiple words; storing a flat tag list would duplicate the information that's already in `sentence_vocab`. The `sentence_id` FK lets you recover the complete tag list when needed (see `crud.py`'s `_tags_for_question`).

---

## HSK-Level Design

### The Problem
Originally, `unit_number` was globally unique. But once a second HSK level's curriculum loads, numbering restarts (HSK2's unit 1 ≠ HSK1's unit 1), so `unit_number` alone becomes ambiguous.

### The Solution
- `units.hsk_level` column added (default 1)
- Uniqueness moved to composite constraint: `(unit_number, hsk_level)`
- Every Unit lookup must now filter on **both** fields

### Example
```python
# ❌ WRONG - ambiguous, will grab whichever row SQLite returns first
unit = db.query(Unit).filter(Unit.unit_number == 1).first()

# ✅ RIGHT - unambiguous
unit = db.query(Unit).filter(
    Unit.unit_number == 1,
    Unit.hsk_level == 1
).first()
```

### Cascading Changes
- `vocab.unit_id` (FK to units) is still single-valued — a word has one home unit across all levels
- `get_word_to_unit_map()` now returns `{hanzi: (unit_number, hsk_level)}` tuples (was bare int)
- `rehome_sentences()` scoped per level — stays within one HSK level, doesn't cross between them
- Pipeline scripts take `--hsk-level` flag; older code defaults to 1 (backward compat)

---

## Common Queries

### Get all vocab for a unit
```python
from app.textbook.db_utils import get_vocab_for_unit
from app.textbook.models import WordType

vocab = get_vocab_for_unit(
    db, 
    unit_number=3, 
    word_types=[WordType.vocab, WordType.grammar],
    hsk_level=1
)
```

### Get tags in a sentence
```python
from app.textbook.db_utils import get_tags_for_sentence

tags = get_tags_for_sentence(db, sentence_id=42)
# Returns: ["你", "好", "吗"]
```

### Get grammar tips attached to a sentence
```python
from app.textbook.db_utils import get_grammar_tips_for_sentence

tips = get_grammar_tips_for_sentence(db, sentence_id=42)
# Returns: [{"sections": [...]}, ...]
```

### Get all questions for a tag in a unit
```python
from app.textbook.crud import get_questions_for_tag

questions = get_questions_for_tag(
    db, 
    tag="学",
    unit_number=3,
    hsk_level=1
)
# Returns: [{"id": "...", "question_type": "...", "question": "...", "answer": "...", "tags": [...]}, ...]
```

### Get a word's definition
```python
vocab = db.query(Vocab).filter(Vocab.hanzi == "你").first()
print(vocab.pinyin, vocab.english)  # "ni3", "you"
```

### Get a word's home unit
```python
vocab = db.query(Vocab).filter(Vocab.hanzi == "你").first()
if vocab.unit:
    print(f"Unit {vocab.unit.unit_number}, HSK level {vocab.unit.hsk_level}")
    # "Unit 1, HSK level 1"
```

---

## Data Flow (Pipeline)

The data pipeline (`main.py`) populates textbook.db in this order:

1. **vocab_index_parser.py** → `units` + `vocab` tables
   - Reads the printed index PDF
   - Extracts hanzi, pinyin, definitions, word types, unit numbers
   - Creates `Unit` rows per level
   - Upserts `Vocab` rows (keyed on hanzi; lowest unit wins if duplicates)

2. **sentence_parser.py** → `sentences` + `sentence_vocab` tables
   - Reads textbook & workbook PDFs (per unit)
   - Extracts sentences, translates them, generates pinyin
   - Tags each sentence with known vocab
   - Upserts unknown word tags as `Vocab(word_type="auto", unit=None)`

3. **extract_and_match_grammar.py** → `grammar_tips` + `sentence_grammar` tables
   - Reads Notes sections from textbook OCR
   - Reformats each tip into structured JSON
   - Matches tips to sentences that demonstrate them
   - Creates join table rows

4. **create_questions.py** → `questions` table + rehoming
   - Rehomes sentences to earlier units if all their words are known earlier
   - Generates word-level questions (listening vocab, translation, etc.)
   - Generates sentence-level questions
   - Links FITB questions to their source sentences (best-effort)
   - Upserts all questions (keyed on type + question + answer; legacy_id for compat)

5. **append_orphan_tags.py** → Repairs `vocab` (pinyin/english)
   - Finds words used but not formally indexed (gaps)
   - Calls Claude to look up definitions
   - Updates/creates `Vocab` rows for orphaned words
   - Logs non-standalone sub-characters to a rejection cache

---

## Important Notes

### Cascading Deletes
- `Unit` → cascades delete to `Vocab`, `Sentence`, `GrammarTip`, `FitbQuestion`, `Question`
- `Sentence` → cascades delete to `SentenceVocab`, `FitbQuestion`, but **NOT** `SentenceGrammar` (due to composite PK with sentence_id; must be deleted manually)
- `Vocab` → cascades delete to `SentenceVocab`
- `GrammarTip` → cascades delete to `SentenceGrammar`

### Idempotency
All pipeline scripts are idempotent — re-running them doesn't duplicate data:
- `vocab`: keyed on `hanzi` (unique)
- `sentences`: keyed on `(unit_id, hanzi)` (unique)
- `grammar_tips`: keyed on `(unit_id, raw_text)` (unique)
- `questions`: keyed on `(unit_id, question_type, question, answer)` (unique, plus legacy_id)

### Pinyin Format
- Numeric tones: "zhong1guo2" (not "Zhōngguó")
- No spaces within compound words: "xue2xi2" (not "xue2 xi2")
- Spaces between separate syllables in multi-word sentences: "Ni3 hao3. Wo3 hen3 hao3."

### word_type="auto"
Words auto-created during sentence tagging (not in the printed index) start with `unit_id=NULL` and `word_type="auto"`. If formalized later (e.g., by `append_orphan_tags.py`), the row is updated in-place: `unit_id` is filled in, `word_type` upgraded to `vocab`/`grammar`/`proper_noun`. The row is never duplicated.

---

## Querying via CRUD Layer

The app doesn't query textbook.db directly; it goes through `app/textbook/crud.py`, which:
- Adds caching (1-hour TTL on curriculum data)
- Converts Question rows to dicts matching old JSON format
- Handles `sentence_id` resolution for sentence-level questions
- Provides convenient lookup functions for common tasks

**Always use crud.py functions** (`get_questions_for_tag`, `get_vocab_tags_for_unit`, etc.) rather than raw SQL queries when writing app code.

---

## Maintenance

### Clear Cache After Pipeline Runs
```python
from app.textbook.crud import clear_cache
clear_cache()
```

Or restart the app (cache is cleared on import).

### Inspect the Database
```bash
sqlite3 textbook.db
> .schema  # Show all table definitions
> SELECT COUNT(*) FROM vocab;  # Count vocab entries
> SELECT * FROM units ORDER BY hsk_level, unit_number;  # See all units
```

### Backup Before Major Changes
```bash
cp textbook.db textbook.db.backup
```

---

## Related Files

- **models.py** — SQLAlchemy ORM model definitions
- **db_utils.py** — Shared upsert/query helpers (used by pipeline scripts)
- **crud.py** — High-level query API (used by the app itself)
- **main.py** — Pipeline orchestrator
- **migration_scripts/** — One-time schema upgrades (run once, then archive)