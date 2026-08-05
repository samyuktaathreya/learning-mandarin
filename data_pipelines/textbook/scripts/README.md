# Textbook Curriculum SQL Pipeline

Complete rewrite of the 5-script JSON pipeline (`vocab_index_parser`, `sentence_parser`, `extract_and_match_grammar`, `create_questions`, and the dictionary sync script) to write directly to SQLite instead of generating 7 intermediate JSON files.

## Overview

Instead of:
```
PDF → OCR → JSON → Agent calls → JSON files (index_output.json, units_output.json, 
unit_vocab_tags.json, unit_questions_hsk1.json, etc.) → JSON merges → final JSON
```

Now:
```
PDF → OCR → Agent calls → Direct SQL writes (Vocab, Sentence, SentenceVocab, 
GrammarTip, SentenceGrammar, FitbQuestion, Question rows)
```

**Key advantages:**
- No JSON intermediate files = no redundant copying of vocab definitions across N sentences
- Idempotency built-in: re-running a script re-processes only the units you specify without losing prior work
- Many-to-many relationships properly modeled (e.g., one grammar tip can attach to many sentences)
- Single source of truth for definitions, pinyin, vocab-to-unit mapping

## Schema

Five core tables:

### `units`
One row per textbook unit.
- `unit_number` (int, unique)
- `title` (text, nullable)

### `vocab`
One row per unique hanzi word. Single source of truth for pinyin/english definitions.
- `hanzi` (text, unique, indexed)
- `pinyin` (text) — numeric-tone format: `zhong1guo2`, not `Zhōngguó`
- `english` (text)
- `word_type` (enum: vocab, grammar, proper_noun, auto)
  - `vocab`: regular vocabulary
  - `grammar`: particles, auxiliary markers, etc. (POS-classified in the index)
  - `proper_noun`: names, place names
  - `auto`: fallback for unknown words auto-created during tagging (unknown unit, word_type=auto lets us track "words we saw but didn't formally teach")
- `unit_id` (FK → units) — unit word is FIRST introduced in

### `sentence`
Full sentences from textbook/workbook units.
- `hanzi` (text) — Chinese sentence
- `english` (text) — translation
- `pinyin` (text) — full pinyin breakdown
- `source` (text) — "textbook" or "workbook"
- `unit_id` (FK → units)
- Unique constraint: (unit_id, hanzi) — no duplicate sentences within a unit

### `sentence_vocab`
Many-to-many: sentences ↔ vocab words (replaces the old `tags` array).
- `sentence_id` (FK)
- `vocab_id` (FK)
- `position` (int) — order of tags within the sentence
- Primary key: (sentence_id, vocab_id)

### `grammar_tips`
Grammar points/explanations, many-to-many with sentences.
- `raw_text` (text) — original scraped tip text (for dedup/idempotency)
- `content_json` (text) — full structured `{"sections": [...]}` output from Claude
- `unit_id` (FK)
- Unique constraint: (unit_id, raw_text) — re-running the grammar extraction doesn't duplicate tips

### `sentence_grammar`
Many-to-many: sentences ↔ grammar tips.
- `sentence_id` (FK)
- `grammar_tip_id` (FK)

### `fitb_questions`
Fill-in-the-blank questions.
- `question` (text) — the prompt with `___` blanks
- `answer` (text) — the answer word
- `full_sentence` (text) — the complete sentence with answer filled in
- `sentence_id` (FK, nullable) — link back to the original Sentence if matched
- `unit_id` (FK)

### `questions`
The final question bank, one row per (unit, question_type, question, answer) tuple.
- `legacy_id` (text, nullable) — e.g., `u3_speaking_vocab_2` for backward compat
- `question_type` (text)
- `question` (text)
- `answer` (text)
- `vocab_id` (FK, nullable) — the word being tested
- Unique constraint: (legacy_id) — avoids ID collisions on re-runs

## Running the Pipeline

### Quick Start
```bash
python main.py  # Runs all 5 scripts in order
```

### Selective Reprocessing

**Run only vocab extraction:**
```bash
python main.py --vocab-only
```

**Skip vocab (already done), start from sentences:**
```bash
python main.py --from-sentences
```

**Start from grammar tips (vocab + sentences already done):**
```bash
python main.py --from-grammar
```

**Start from question generation:**
```bash
python main.py --from-questions
```

**Only run definition sync (post-processing):**
```bash
python main.py --from-sync
```

**Reprocess only specific units (skips vocab re-extraction):**
```bash
python main.py --units 3 4 5
# Only sentence_parser, grammar, questions for units 3, 4, 5
```

**Reprocess only textbook (not workbook):**
```bash
python main.py --sources textbook
```

**Combine options: units 3-5 from workbook:**
```bash
python main.py --units 3 4 5 --sources workbook
```

**Skip database stats at the end:**
```bash
python main.py --no-stats
```

### How It Works

`main.py` is a wrapper that:
1. Initializes the SQLite/Postgres database
2. Runs each script as a subprocess (isolation, easy to debug)
3. Supports partial reruns via command-line flags
4. Prints database stats (row counts) at the end
5. Continues past script failures (so partial progress isn't lost) but reports them

All scripts are idempotent — reruns don't duplicate data because upserts are keyed on content (hanzi for vocab, (unit, hanzi) for sentences, (unit, type, question, answer) for questions, etc.).


### 1. `vocab_index_parser.py`
**Input:** Textbook index PDF (cached OCR markdown)  
**Output:** Rows in `units` + `vocab` tables

Extracts vocabulary/grammar/proper-noun definitions from the printed index. Calls:
- `OCR()` on the PDF (cached in `OCR_PATH`, can be cleared to force re-OCR)
- Claude's extraction agent to parse OCR into structured entries
- Dedup by hanzi (lowest-unit-wins if a word appears in the index multiple times)
- `upsert_vocab()` to write each entry (keyed on `hanzi`, so re-runs are idempotent)

**Usage:**
```bash
python vocab_index_parser.py
```

### 2. `sentence_parser.py`
**Input:** Textbook + workbook PDFs (cached OCR)  
**Output:** Rows in `sentences` + `sentence_vocab` tables, plus `fitb_questions`

Extracts sentences and fill-in-the-blank exercises from each unit's pages. Calls:
- `OCR()` on unit page ranges
- Claude sentence/FITB finder agents
- Verbatim filter (validates sentences exist in OCR cache)
- Vocab gate (drops sentences using not-yet-taught words; only workbook)
- Tagger agent to segment sentences into known words
- Tone sandhi + digit expansion (for pinyin accuracy)
- `upsert_sentence()` writes sentence + its tag links atomically

**Module-level overrides (for selective reprocessing):**
```python
UNITS_TO_PROCESS = [3, 4]  # only re-run these units
SOURCES_TO_PROCESS = ["textbook"]  # or ["workbook"], or None for both
```

**Usage:**
```bash
python sentence_parser.py
```

### 3. `extract_and_match_grammar.py`
**Input:** Cached OCR markdown from textbook units (from `sentence_parser`'s OCR step)  
**Output:** Rows in `grammar_tips` + `sentence_grammar` tables

Extracts grammar points from the unit's Notes section and matches them to sentences. Calls:
- Regex to split OCR notes into individual tips
- Claude reformat agent to structure each tip as `{"sections": [...]}`
- Claude matching agent to find sentences that demonstrate each tip
- `get_or_create_grammar_tip()` to upsert the tip (keyed on raw_text, so re-runs don't duplicate)
- `link_sentence_grammar()` to create many-to-many links (idempotent re-linking)

**Usage:**
```bash
python extract_and_match_grammar.py
```

### 4. `create_questions.py`
**Input:** Vocab + Sentence + Grammar data (all in DB now)  
**Output:** Rows in `questions` table

Generates the final question bank (listening vocab, speaking, translation, FITB, etc.). Calls:
- `db.rehome_sentences()` — moves sentences to their earliest-possible unit (a sentence using only unit-1 words that was filed under unit 3 gets moved to unit 1)
- `build_questions_for_unit()` for each unit:
  - Generates word-level questions from vocab/grammar/proper-noun entries
  - Generates sentence questions from Sentence rows
  - Generates FITB questions from FitbQuestion rows
  - Skips "typing required" questions if a word isn't yet taught
- `upsert_question()` creates or merges questions (keyed on (unit, type, question, answer))

**Usage:**
```bash
python create_questions.py
```

### 5. `sync_index_definitions.py`
**Input:** Vocab table + Sentence rows (for example contexts)  
**Output:** Updated pinyin/english in Vocab rows

Post-processing: repairs incomplete definitions and catches mistagging. Calls:
- `get_all_taught_words()` to find all words that appear in curriculum
- `get_all_vocab_with_status()` to find vocab rows with UNKNOWN_* placeholders
- For each gap:
  - Fetches pinyin from dictionary API (per-character fallback for compounds)
  - Finds example sentence from DB
  - Asks Claude if word is standalone or a sub-character of a larger word
  - If standalone: updates/creates Vocab row with definition
  - If not standalone: caches rejection + tries to recover parent word
- `update_vocab_entry()` writes back to Vocab

**Usage:**
```bash
python sync_index_definitions.py
```

All rejected sub-character tags are logged to `REJECTED_VOCAB_CACHE` (a TSV file) for manual review and to avoid re-running expensive Claude calls on the same tags.

## Database Setup

### Initialize the DB
```python
from db import init_db
init_db()  # creates all tables at the DATABASE_URL location
```

### Configuration
Set these environment variables or edit `db.py`:

```python
DATABASE_URL = "sqlite:///./textbook.db"  # or postgres://, mysql://, etc.
```

For **Postgres/MySQL** compatibility, just change the URL — the code is fully ORM'd with SQLAlchemy, no SQL-specific dialect.

### Session Usage
```python
from db import get_session

with get_session() as db:
    # Your code here
    # Automatic commit on success, rollback on exception
```

## Migration: Legacy JSON → SQL

To backfill existing JSON files into the DB (one-time step before you delete the JSONs):

```python
# migrate_legacy_json.py (not included, but straightforward):
import json
from db import get_session, upsert_vocab, upsert_sentence, upsert_question
from models import WordType

# Load index_output.json
with open("index_output.json") as f:
    index = json.load(f)

with get_session() as db:
    for section in ["vocab", "grammar", "proper_nouns"]:
        for item in index.get(section, []):
            wtype = WordType[section.rstrip('s')]  # vocab→vocab, proper_nouns→proper_noun, etc.
            upsert_vocab(db, item["hanzi"], item["pinyin"], item["english"], 
                         item["unit"], wtype)
    
    # Similar for units_output.json sentences → upsert_sentence()
    # Similar for unit_questions_hsk1.json → upsert_question()
```

Once migrated, delete the JSON files and adjust your app to read from the DB instead.

## Querying the DB

### Get all tags in a sentence
```python
from db import get_tags_for_sentence
tags = get_tags_for_sentence(db, sentence_id)  # [str, str, ...]
```

### Get grammar tips attached to a sentence
```python
from db import get_grammar_tips_for_sentence
tips = get_grammar_tips_for_sentence(db, sentence_id)  # [dict, dict, ...]
```

### Get all vocab for a unit
```python
from db import get_vocab_for_unit
from models import WordType
vocab = get_vocab_for_unit(db, unit_number, word_types=[WordType.vocab])
```

### Get word → unit mapping (for SRS / vocab gating)
```python
from db import get_word_to_unit_map
home_unit = get_word_to_unit_map(db)  # {hanzi: unit_number}
```

## Testing

All scripts have been tested end-to-end against a real SQLite database:
- Sentence rehoming works correctly
- Many-to-many grammar links are idempotent
- Question generation deduplicates on (unit, type, question, answer)
- Reruns don't create duplicates

## Notes

1. **Idempotency:** All scripts are safe to re-run for the same input. Specific unit reprocessing is supported (e.g., `sentence_parser.UNITS_TO_PROCESS = [3]` only re-OCRs and re-parses unit 3 without touching units 1, 2, 4+).

2. **OCR Caching:** PDF→markdown OCR is cached in `OCR_PATH` by default. Delete files or set `FORCE_OCR=True` to refresh.

3. **API Errors:** If Claude API key is unavailable, fallback to dictionary-only definitions (no contextual definitions from sentences). If dictionary API is unavailable, words get `UNKNOWN_PINYIN` placeholders that `sync_index_definitions.py` can retry later.

4. **Rejected Vocab Cache:** Non-standalone sub-character tags are logged to `REJECTED_VOCAB_CACHE` (a TSV file). Reprocessing re-reads this file, so manual edits are respected (e.g., if a word was wrongly cached as non-standalone, remove the line and re-run).

5. **Backward Compatibility:** The `legacy_id` field on `questions` rows preserves old ID strings (e.g., `u3_speaking_vocab_2`) if something external references them. Otherwise, it can be left null.

## File Structure

```
textbook_migration/
├── models.py                      # SQLAlchemy ORM definitions
├── db.py                          # Session setup, shared query/upsert helpers
├── vocab_pinyin_utils.py          # Diacritic → numeric pinyin conversion
├── vocab_index_parser.py          # Script 1: Extract index → Vocab rows
├── sentence_parser.py             # Script 2: Extract sentences → Sentence/SentenceVocab/FitbQuestion
├── extract_and_match_grammar.py   # Script 3: Extract grammar tips → GrammarTip/SentenceGrammar
├── create_questions.py            # Script 4: Generate questions → Question rows
└── sync_index_definitions.py      # Script 5: Repair definitions via Claude + dictionary API
```

All scripts import from `db.py` and `models.py`, which handle database setup and all ORM operations.