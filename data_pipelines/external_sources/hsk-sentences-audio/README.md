I'm drawing sentences from this external github repository: 
https://github.com/no7z/hsk-sentences-audio/blob/main/README.md
https://pypi.org/project/hsk-sentences-audio/

# hsk_sentences_audio import

Imports supplementary example sentences (with audio) from the `hsk_sentences_audio`
pip package into `textbook.db`, alongside the sentences already extracted from
the textbook/workbook PDFs.

## Why this isn't inside `data_pipelines/textbook/`

The textbook pipeline is fundamentally PDF-based: OCR, LLM extraction, page-range
splitting, OCR caching. This source is different — it's an already-structured
Python package (no OCR, no LLM extraction calls needed for the sentence text
itself). It gets its own folder so the two ingestion mechanisms don't get
tangled together, while still writing to the **same** `textbook.db` through the
**same** `app/textbook/db_utils.py` / `models.py` the textbook pipeline uses —
that part has to stay shared, or sentence placement/tagging rules will drift
between the two paths over time.

## What it does

1. Finds the highest `hsk_level` currently loaded in `textbook.db`. Only
   external sentences at that level or below are considered — we don't want
   to file a sentence under a curriculum level whose units don't exist yet.
2. For each candidate sentence, segments it against **our own** vocab list
   (greedy longest-match — same approach the textbook pipeline uses), not the
   package's own `tokens` field, which only lists a few notable words per
   sentence rather than a full segmentation.
3. Drops any sentence that uses vocabulary taught in a *later* HSK level than
   the sentence's own declared level (per our curriculum, not theirs).
4. Places the sentence in the highest unit among its same-level matched words
   (or the level's earliest unit, if nothing anchors it higher).
5. Writes it via the same idempotent `upsert_sentence()` the textbook pipeline
   uses — safe to re-run, words not yet in our `vocab` table get
   auto-created (`word_type="auto"`) exactly like today.

`grammar_tags` from the source package are explicitly ignored (per current
scope) and traditional-character text isn't stored (the app only reads
simplified `Sentence.hanzi` elsewhere).

## One-time setup

This adds three nullable columns to `sentences` (`audio_url`, `topic`,
`external_id`) that the textbook/workbook pipeline never needed. Run once:

```bash
python migration_scripts/add_sentence_external_metadata.py
```

Safe to re-run — it checks for existing columns first.

## Usage

```bash
# Dry run first -- see what would be written without touching the DB
python data_pipelines/external_sources/hsk_sentences_audio/import_sentences.py --dry-run

# Real run, all topics, up to your highest loaded HSK level
python data_pipelines/external_sources/hsk_sentences_audio/import_sentences.py

# Just one topic
python data_pipelines/external_sources/hsk_sentences_audio/import_sentences.py --topic food
```

## After running

New sentences exist with tags, but have **no grammar-tip links or Question
rows** yet — those come from the textbook pipeline's later stages, which
re-query sentences fresh from the DB each run. For each HSK level you just
touched:

```bash
python data_pipelines/textbook/scripts/main.py --from-grammar --hsk-level 1
```

(`--from-grammar` runs `extract_and_match_grammar.py` then `create_questions.py`
then `append_orphan_tags.py`; use whichever `--hsk-level` matches what you
imported.)

Then clear the app's curriculum cache (or restart the app) so it picks up
the new questions — see `crud.clear_cache()`.
