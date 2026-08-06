# App Architecture

This document explains how the app is structured: its databases, the data
pipeline that populates them, and how the running app queries them. Written
for anyone (including future-you) who needs to get oriented quickly.

---

## 1. High-level shape

The app has **four separate SQLite databases**, each owning a distinct
domain. Nothing is shared across them except plain string/int identifiers
(e.g. a vocab "tag" is just a hanzi string, referenced by both the textbook
DB and the session DB, with no foreign key between them).

| Database | Owns | Written by | Read by |
|---|---|---|---|
| **textbook.db** | Curriculum: units, vocab, sentences, grammar tips, questions | The data pipeline (OCR + Claude agents) | The FastAPI app, via `textbook/crud.py` |
| **mandarin_app.db** (session DB) | Per-user progress: strength/SRS state, tiers, seen questions, sound practice | The FastAPI app (`session/crud.py`) as users answer questions | The FastAPI app |
| **characters.db** | Character metadata: IDS decomposition, radical meanings, confusible pairs | A separate one-off data pipeline (not covered here) | `characters/crud.py`, used by the character-quiz feature |
| **CC-CEDICT dictionary DB** | General-purpose Chinese-to-English dictionary, independent of the HSK curriculum | Bulk-seeded once from a CC-CEDICT text dump (`scripts/seed.py`) | `shared/crud.py`'s `get_dictionary_entries` |

Why separate DBs instead of one: **textbook.db is regenerated wholesale by
the pipeline** (rerun a script, data changes), while **mandarin_app.db holds
irreplaceable user progress** that must never be touched by a pipeline
rerun. Keeping them as separate files/connections makes that boundary
physically enforced, not just a convention.

---

## 2. textbook.db -- curriculum schema

```
units
  id, unit_number (unique), title

vocab
  id, hanzi (unique), pinyin, english,
  word_type (vocab | grammar | proper_noun | auto),
  unit_id -> units.id (nullable)

sentences
  id, unit_id -> units.id, hanzi, english, pinyin, source (textbook|workbook)
  unique (unit_id, hanzi)

sentence_vocab            -- replaces the old inline "tags: [...]" array
  id (surrogate PK),
  sentence_id -> sentences.id, vocab_id -> vocab.id, position
  unique (sentence_id, position)   -- NOT (sentence_id, vocab_id): a word
                                       can legitimately repeat in one sentence

grammar_tips
  id, unit_id -> units.id, raw_text, content_json (structured {"sections":[...]})
  unique (unit_id, raw_text)

sentence_grammar          -- many-to-many: a tip can attach to many sentences,
  sentence_id -> sentences.id     a sentence can carry many tips
  grammar_tip_id -> grammar_tips.id

fitb_questions
  id, sentence_id -> sentences.id (nullable), unit_id -> units.id,
  question, answer, full_sentence

questions                 -- the actual quiz bank
  id, legacy_id (nullable), question_type, question, answer,
  unit_id -> units.id,
  vocab_id -> vocab.id (nullable)      -- set for WORD-level questions
  sentence_id -> sentences.id (nullable)  -- set for SENTENCE-level questions
  unique (legacy_id)
```

**Key design decisions:**

- **`vocab` is the single source of truth for every hanzi word** -- regular
  vocab, grammar-classified particles, proper nouns, and "auto" (unknown
  words the tagger encountered but that were never in the printed index)
  all live here with a `word_type` discriminator. Downstream consumers that
  need "every known token up to unit N" never had to care about that split.
- **A question's `tags` (which words it exercises) isn't a stored column --
  it's derived at query time.** A word question's tag is its `vocab_id`. A
  sentence question's tags are *every* word in that sentence, recovered via
  `sentence_id -> sentence_vocab -> vocab`. This is why `Question` has both
  `vocab_id` and `sentence_id` FKs instead of one.
- **`word_type='auto'` vocab rows** are unknown-word fallbacks created
  during sentence tagging (a word appeared in a sentence but wasn't in the
  printed vocab index). They get real pinyin from the tagger but need their
  English definition backfilled -- that's what `sync_index_definitions.py`
  / `append_orphan_tags.py` is for.

---

## 3. The data pipeline (populates textbook.db)

Five scripts, run in order via `main.py`:

```
1. vocab_index_parser.py
     OCR the vocab index PDF -> Claude extraction agent -> classify
     (vocab/grammar/proper_noun) -> dedupe (lowest unit wins) -> upsert
     Vocab rows

2. sentence_parser.py
     OCR each unit's pages -> Claude sentence-finder + FITB-finder/solver
     agents -> verbatim filter -> vocab gate -> Claude tagger agent
     (segment sentence into known words) -> tone sandhi -> upsert
     Sentence + SentenceVocab + FitbQuestion rows

3. extract_and_match_grammar.py
     Regex-split each unit's OCR'd "Notes" section into raw tips -> Claude
     reformats each tip into structured JSON -> Claude matches tips to
     sentences -> upsert GrammarTip + SentenceGrammar rows

4. create_questions.py
     Rehome sentences to their earliest legitimate unit (a sentence using
     only unit-1 words shouldn't be stuck at unit 3) -> generate word-level
     and sentence-level Question rows for every vocab/grammar/proper_noun/
     sentence/FITB entry -> upsert Question rows

5. sync_index_definitions.py / append_orphan_tags.py
     Find Vocab rows with missing/placeholder/blank pinyin or english ->
     ask Claude for pinyin + definition + whether it's really standalone
     vocab or a sub-character of a larger compound word -> repair or
     recover the parent word -> cache rejections in a TSV file so the same
     word isn't re-asked every run
```

**Idempotency:** every script upserts keyed on natural content (hanzi for
vocab, `(unit, hanzi)` for sentences, `(unit, raw_text)` for grammar tips,
`(unit, type, question, answer)` for questions) -- rerunning a script, or
rerunning just a subset of units via `main.py --units 3 4 5`, updates
in-place rather than duplicating.

**`main.py` flags:**
```
python main.py                    # full pipeline
python main.py --vocab-only       # stop after step 1
python main.py --from-sentences   # skip step 1
python main.py --from-grammar     # skip steps 1-2
python main.py --from-questions   # skip steps 1-3
python main.py --from-sync        # only run step 5
python main.py --units 3 4 5      # reprocess only these units (step 2)
python main.py --sources textbook # only textbook PDFs, not workbook
```

**Diagnostics** (run after the pipeline to sanity-check the result):
```
check_pipeline_stats_simple.py    # totals + a flat list of problems found
inspect_gaps.py                   # the exact incomplete vocab words / orphan questions
```

---

## 4. The running app -- query layer pattern

The app never reads JSON. Every piece of curriculum data the old JSON-based
`services.py` used to hold in RAM (loaded once at import) is now a real
query, following one consistent 3-layer pattern:

```
textbook/crud.py       <- raw SQLAlchemy queries against Vocab/Sentence/
                           Question/etc. Owns an in-process TTL cache
                           (curriculum data changes only when the pipeline
                           reruns, so aggressive caching is safe and
                           correct). clear_cache() available for after a
                           pipeline rerun.

textbook/services.py   <- thin wrappers around crud.py, named close to
                           what the old JSON globals used to be called
                           (get_unit_vocab_tags, get_questions_for_tag,
                           lookup_word, ...), so migrating a caller from
                           "read a dict" to "call a function" is a
                           near-mechanical diff.

router.py / session_builder.py / tier_engine.py / review_engine.py
                        <- call services.py functions, passing a
                           `textbook_db: Session` alongside their own
                           `db: Session` (session DB) wherever curriculum
                           data is needed.
```

**Why two DB sessions everywhere:** `db` (session DB: StrengthTable,
SeenQuestion, User) and `textbook_db` (curriculum DB: Vocab, Question,
Sentence) are genuinely different connections. Any function that needs
both -- which is most of session generation -- takes both explicitly.
`textbook/database.py`'s `get_textbook_db()` is the FastAPI dependency for
it, mirroring the existing `characters/database.py`'s `get_characters_db()`.

**Key functions in `textbook/services.py`:**

| Function | Replaces (old JSON global) |
|---|---|
| `get_unit_vocab_tags(db, unit)` | `unit_to_vocab_tags_dict[unit]` |
| `get_tag_home_unit(db, tag)` | `tags_to_unit_dict[tag]` |
| `get_questions_for_tag(db, tag, unit, question_type=None)` | `inverted_index[tag]`, filtered to one unit |
| `get_questions_for_tag_up_to_unit(db, tag, max_unit, question_type=None)` | `inverted_index[tag]`, filtered by `unit <= max_unit` (used by review, which can pull from any past unit) |
| `get_all_questions_for_unit(db, unit)` | `unit_questions[str(unit)]` (used by unit tests) |
| `get_all_unit_numbers(db)` | `unit_questions.keys()` |
| `lookup_word(db, hanzi)` | the dictionary+pypinyin-fallback logic that used to be inline in `/api/lookup` |
| `get_vocab_definition` / `get_pinyin_for_word` | `hsk1_dictionary[hanzi]` / `word_to_pinyin[hanzi]` |

---

## 5. Session domain (mandarin_app.db)

Separate from textbook data entirely. Tracks per-user learning state:

```
strength_table       -- (user, tag, facet) -> correct_count, stability,
                         miss_count, times_seen. facet is "character" or
                         "pinyin" -- a word's meaning-recall and sound-
                         recall are tracked independently.
sound_progress        -- per-user mastery of atomic Mandarin sounds
word_tier_progress    -- per-user, per-word tier (1-4) within a unit's
                          skill progression
seen_questions        -- per-user exposure count per question, so
                          selection prefers unseen variants
accepted_answers      -- cache of AI-graded-correct (question, answer)
                          pairs, so identical answers skip a re-grade
flagged_mismatches    -- detection log for OCR/pipeline bugs where a
                          question's expected answer doesn't actually
                          match the question shown
question_tips         -- learner-authored notes attached to specific
                          question/answer text
```

`tag` here is just the hanzi string (e.g. "wo3" -- no, `"我"`) -- **not** an
FK into `textbook.db`'s `vocab` table, since they're different databases.
This is intentional: session progress is keyed on the word itself, which is
stable even if the textbook DB's internal IDs change on a pipeline rerun.

**Question generation flow** (`session/services/`):

```
session_builder.generate_full_session(db, characters_db, textbook_db, user_id, ...)
  1. graduation check -> generate_unit_test() if the unit's fully learned
  2. review check -> generate_review_session() if words are due for review
  3. otherwise: generate_practice_session()
       - review_engine.generate_review_questions()  (due reviews first)
       - tier_engine.generate_tier_questions()       (fill the rest, tier-
                                                        weighted selection)
  + character_questions.generate_character_questions()  (2 bonus questions:
    character recognition / radical quizzes, drawing on characters.db)
```

`process_submission()` grades answers, updates `StrengthTable`/tier/miss
counts, and checks for unit-test pass/fail -- entirely session-DB-only, no
textbook_db needed.

---

## 6. Migration history / known gotchas

Things that broke during the JSON-to-SQL migration, worth knowing if
similar issues resurface:

- **`Base.metadata.create_all()` only creates missing tables -- it never
  ALTERs existing ones.** Any time a model gains a new column or a
  constraint changes, an existing DB file needs an explicit migration
  script (see `migrate_sentence_vocab.py`, `migrate_add_sentence_id.py`).
  There's no Alembic in place yet; each schema change so far has been a
  hand-written migration.
- **`SentenceVocab`'s original PK was `(sentence_id, vocab_id)`** -- wrong,
  since a word can repeat within one sentence. Fixed to a surrogate PK with
  uniqueness on `(sentence_id, position)` instead.
- **`get_all_vocab_with_status`'s "needs repair" check** originally only
  matched the literal string `"UNKNOWN_PINYIN"`/`"UNKNOWN_ENGLISH"` -- it
  missed `word_type='auto'` words that have real pinyin but a blank English
  field. Fixed to also flag blank/whitespace-only fields.
- **Relative SQLite paths resolve against the process's cwd, not the
  file's location** -- running the pipeline from one directory and the app
  from another can silently open two different (or one empty) database
  files. Use an absolute path built from `Path(__file__).resolve()`.
- **Config constant renames ripple everywhere** -- `TEXTBOOK_APP_DATA_DIR`
  -> `TEXTBOOK_APP_DIR` broke both pipeline scripts that imported it. No
  central registry for this yet; a rename needs a full grep across the
  pipeline and app.

---

## 7. Where to look for X

| I need to... | Look at |
|---|---|
| Add a new pipeline step | `main.py`'s `pipeline` list + write a new script following the `upsert_*` pattern in `db.py` |
| Add a new curriculum query | `textbook/crud.py` (raw query + cache) then a thin wrapper in `textbook/services.py` |
| Change how questions are selected for a session | `session/services/tier_engine.py` (new-content selection) or `review_engine.py` (due-review selection) |
| Debug "why doesn't the DB have X" | `check_pipeline_stats_simple.py`, then `inspect_gaps.py` for specifics |
| Fix a schema mismatch after a model change | Write a migration following `migrate_add_sentence_id.py`'s pattern (check -> migrate -> verify) |
| Understand what a "tag" is | It's a hanzi string, used as the review/SRS unit everywhere. In textbook.db it maps to a `Vocab` row; in mandarin_app.db it's just a plain string key on `StrengthTable`. |