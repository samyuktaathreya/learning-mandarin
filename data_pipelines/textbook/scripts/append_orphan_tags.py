"""
Post-processing step: fill in missing vocab definitions and catch cases where
the tagging algorithm split a compound word incorrectly.

Workflow (DB-backed, powered entirely by Claude):
  1. Query which words SHOULD be indexed (from Vocab rows + all Sentence tags +
     FITB answers), combining sources in unit order
  2. Find gaps: words present in curriculum but missing from Vocab index or
     marked with UNKNOWN_* placeholders
  3. For each gap:
     a. Find an example sentence from DB
     b. Ask Claude for the word's pinyin, definition, and context analysis
     c. Claude determines if the word is standalone or a sub-character of a
        larger compound
     d. If standalone: update/create Vocab row with Claude's pinyin and definition
     e. If sub-character: cache rejection, try to recover the parent word using Claude
  4. Commit updated Vocab rows back to the DB

Rejected-vocab cache (REJECTED_VOCAB_CACHE) is kept as a simple TSV file
(diagnostic log only, not part of the core data model, so no need to add a
DB table for it).
"""

import os
import re
import json
from pathlib import Path
from dotenv import load_dotenv
import anthropic
from typing import Optional

from app.core.config.shared import ENV_FILE
from app.core.config.textbook import REJECTED_VOCAB_CACHE

from app.textbook.db_utils import (
    get_session, init_db, get_all_vocab_senses_with_status, get_uncovered_word_units,
    find_example_sentence, get_nearest_sense, rehome_sense, fill_sense_pinyin,
    update_incomplete_sense, upsert_vocab_sense, get_senses_for_vocab, resolve_sense_for_sentence,
)
from app.textbook.models import WordType
from app.textbook.models import Vocab, VocabSense, Sentence, SentenceVocab, Question
from data_pipelines.textbook.scripts.vocab_pinyin_utils import diacritic_to_numeric, cross_check_pinyin
from data_pipelines.textbook.scripts.cedict_utils import lookup_word, segment_into_words

# --- Configuration ---
load_dotenv(ENV_FILE)
api_key = os.environ.get("CLAUDE_API_KEY")
client = anthropic.Anthropic(api_key=api_key) if api_key else None
MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

# HSK level being processed this run (threaded the same way main.py already
# threads UNITS_TO_PROCESS / SOURCES_TO_PROCESS to sentence_parser.py).
HSK_LEVEL = int(os.environ.get("HSK_LEVEL", "1"))


# --- Rejected Vocab Cache ---

def load_rejected_vocab_cache() -> dict:
    """Loads previously-rejected (non-standalone) words from TSV cache."""
    if not os.path.exists(REJECTED_VOCAB_CACHE):
        return {}

    cache = {}
    with open(REJECTED_VOCAB_CACHE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            parts = line.split("\t")
            word = parts[0] if len(parts) > 0 else ""
            unit = parts[1] if len(parts) > 1 else ""
            parent_word = parts[2] if len(parts) > 2 else ""
            reasoning = parts[3] if len(parts) > 3 else ""
            if word:
                cache[word] = {"unit": unit, "parent_word": parent_word, "reasoning": reasoning}
    return cache


def append_rejected_vocab_entry(word: str, unit, parent_word: str, reasoning: str):
    os.makedirs(os.path.dirname(REJECTED_VOCAB_CACHE), exist_ok=True)
    safe_reasoning = (reasoning or "").replace("\t", " ").replace("\n", " ").strip()
    safe_parent = (parent_word or "").replace("\t", " ").replace("\n", " ").strip()
    with open(REJECTED_VOCAB_CACHE, "a", encoding="utf-8") as f:
        f.write(f"{word}\t{unit}\t{safe_parent}\t{safe_reasoning}\n")


# --- Utils ---

def clean_pinyin(pinyin: str) -> str:
    """Normalizes pinyin formatting to ensure consistency: strips stray
    whitespace/brackets, THEN converts to the app's numeric-tone storage
    format (diacritic_to_numeric is a no-op if the string already has a
    digit in it, so this is safe to call unconditionally regardless of
    which format Claude happened to return).

    BUGFIX: this used to only strip whitespace/brackets and never actually
    normalized tone notation, so any pinyin Claude returned in diacritic
    form (e.g. "tàirelè") went straight into the DB unconverted -- the
    prompt below asks for "standard Pinyin" without specifying numeric
    tones, so Claude reasonably defaults to diacritics (the more common
    convention outside this app). Words extracted from the printed index by
    vocab_index_parser.py were never affected (it already calls
    diacritic_to_numeric explicitly); only words filled in here, by this
    script's Claude-backed gap-filling, were."""
    if not isinstance(pinyin, str):
        return pinyin
    stripped = pinyin.strip().strip("[]").replace(" ", "")
    return diacritic_to_numeric(stripped)


# --- Claude Vocab Analysis ---

def analyze_vocab(word: str, sentence: Optional[str] = None) -> dict:
    """Uses Claude to fetch pinyin, definition, and standalone status."""
    if client is None:
        print("  [warning] CLAUDE_API_KEY not configured; skipping AI disambiguation")
        return {
            "is_standalone": True,
            "pinyin": "UNKNOWN_PINYIN",
            "definition": "UNKNOWN_ENGLISH",
            "parent_word": None,
            "reasoning": "Claude API client unavailable",
        }

    if sentence:
        task_instructions = (
            f'Analyze the target word/character "{word}" as used in the following sentence:\n'
            f'"{sentence}"\n\n'
            f'Task:\n'
            f'1. Determine if "{word}" is used as an INDEPENDENT, STANDALONE vocabulary word/meaning in this sentence.\n'
            f'2. OR if "{word}" is merely a component character of a LARGER compound word/name (e.g., "卫" inside "大卫", or "视" inside "电视").\n'
            f'3. Provide the accurate standard Pinyin and English definition.'
        )
    else:
        task_instructions = (
            f'Analyze the target Chinese word/character "{word}".\n\n'
            f'Task:\n'
            f'1. Provide its standard Pinyin and general English definition.\n'
            f'2. Determine if it is typically used as a standalone word, or if it is merely a bound morpheme/sub-character.'
        )

    prompt = f"""You are a Chinese language curriculum expert.
{task_instructions}

Output ONLY valid JSON matching this exact format. No markdown, no preambles:
{{
    "is_standalone": true or false,
    "parent_word": "The larger compound word if false, otherwise null",
    "pinyin": "Pinyin in NUMERIC TONE format, e.g. 'tai4re4le5' -- NOT diacritic format like 'tàirelè'. Tone 5 (neutral tone) still gets a '5' suffix (e.g. 'le5', 'ma5'). Do not include spaces between syllables for a single compound word.",
    "definition": "A concise, natural English definition for the target word (null if is_standalone is false)",
    "reasoning": "Brief 1-sentence explanation"
}}
"""

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=256,
            system="You extract vocabulary definitions, translate Chinese to English with Pinyin, and filter out sub-word characters for Chinese language learning.",
            messages=[{"role": "user", "content": prompt}],
        )

        raw_text = response.content[0].text
        json_match = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if json_match:
            raw_text = json_match.group(0)

        data = json.loads(raw_text)
        if data.get("pinyin"):
            data["pinyin"] = clean_pinyin(data["pinyin"])
            
        return data

    except json.JSONDecodeError as e:
        print(f"  [Error] Failed to parse Claude JSON for '{word}': {e}")
    except Exception as e:
        print(f"  [Error] Claude API call failed for '{word}': {e}")

    return {
        "is_standalone": True,
        "pinyin": "UNKNOWN_PINYIN",
        "definition": "UNKNOWN_ENGLISH",
        "parent_word": None,
        "reasoning": "Fallback due to error",
    }

def resolve_primary_definition(word: str, cedict_english: str, sentence: Optional[str]) -> str:
    """CEDICT entries can list/return an obscure or narrow sense for a
    polysemous word (e.g. '老' -> a surname-prefix sense instead of 'old
    (of people)', even though the curriculum only ever teaches the latter).
    Blindly trusting the raw CEDICT string as Vocab.english can surface the
    wrong sense as if it were the word's meaning. If we have a real
    curriculum sentence for this word, ask Claude to pick/write the
    definition that actually matches how it's taught here."""
    if not sentence or client is None:
        return cedict_english  # nothing to disambiguate against

    prompt = f"""You are a Chinese curriculum editor. A dictionary lookup
returned this definition for the word "{word}":
"{cedict_english}"

Example sentence from the curriculum where "{word}" is taught:
"{sentence}"

Write ONE concise English definition for "{word}" that reflects how it's
actually used in this sentence -- the primary sense a beginner should
learn here, not an obscure dictionary sense, unless that IS the sense used.

Output ONLY the definition text. No quotes, no markdown, no preamble.
"""
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=64,
            system="You are a precise bilingual dictionary editor for a Chinese-learning app.",
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        return raw or cedict_english
    except Exception as e:
        print(f"  [warning] primary-definition resolution failed for '{word}': {e}")
        return cedict_english


def is_same_sense(word: str, existing_english: str, candidate_english: str,
                   sentence: Optional[str] = None) -> bool:
    """Asks Claude whether `candidate_english` describes the SAME taught
    meaning as an existing sense's `existing_english`, or a genuinely
    different one. Used ONLY for definitions coming from OUTSIDE the
    printed index (CEDICT gap-fills, Claude-authored definitions) -- the
    printed index itself is trusted directly by vocab_index_parser.py,
    without an AI call, since it has real text on both sides of every
    comparison it makes. Here, at least one side of the comparison didn't
    come from the index, so the text alone isn't trustworthy enough to
    diff directly.

    Defaults to True (assume same meaning, don't fragment into a spurious
    new sense) if Claude is unavailable or the call fails -- a missed new
    sense is a smaller, easier-to-notice problem than the vocab list
    silently accumulating near-duplicate senses for the same word."""
    if client is None:
        return True

    prompt = f"""You are a Chinese curriculum editor. A word may have more
than one taught meaning across a curriculum.

Word: "{word}"
Meaning already on file: "{existing_english}"
Candidate meaning for a new appearance: "{candidate_english}"
{f'Example sentence for the new appearance: "{sentence}"' if sentence else ""}

Are these describing the SAME core meaning (possibly just worded
differently), or are they genuinely DIFFERENT senses of "{word}" that a
learner would need to learn separately (e.g. 老 as "old" vs. 老 as a
surname-prefix, or 还 "still/also" vs. 还 "to return something")?

Output ONLY one word: SAME or DIFFERENT.
"""
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=8,
            system="You are a precise bilingual dictionary editor.",
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip().upper()
        return raw.startswith("SAME")
    except Exception as e:
        print(f"  [warning] sense-match check failed for '{word}': {e}")
        return True


def register_word_sense(db, hanzi: str, unit: int, pinyin: str, english: str,
                         word_type: WordType = WordType.vocab,
                         sentence: Optional[str] = None) -> VocabSense:
    """Registers `english` as a taught meaning of `hanzi`, sense-aware. This
    is the single funnel every gap-filling path in this script goes
    through (CEDICT hits, Claude-authored definitions, recovered parent
    words) -- per the pipeline's sense-matching policy: the printed index
    is trusted directly (vocab_index_parser.py), but anything sourced from
    OUTSIDE the index asks Claude to compare against the nearest existing
    sense before deciding to fragment into a new one.

    - No existing senses at all -> this becomes the (primary) first sense.
    - Existing sense(s) -> compare against the nearest one by home unit;
      if Claude says SAME, reuse it (re-homing it earlier if this
      appearance turns out to be earlier, filling in pinyin if it was
      blank) instead of creating a duplicate; if DIFFERENT, create a new
      sense homed at `unit`."""
    nearest = get_nearest_sense(db, hanzi, unit, hsk_level=HSK_LEVEL)
    if nearest is None:
        return upsert_vocab_sense(db, hanzi, pinyin, english, unit, word_type, hsk_level=HSK_LEVEL)

    if is_same_sense(hanzi, nearest.english, english, sentence):
        rehome_sense(db, nearest, unit, hsk_level=HSK_LEVEL)
        fill_sense_pinyin(db, nearest, pinyin)
        return nearest

    return upsert_vocab_sense(db, hanzi, pinyin, english, unit, word_type, hsk_level=HSK_LEVEL)


def select_best_cedict_sense(word: str, candidates: list, sentence: Optional[str]) -> int:
    """Which CEDICT candidate (index into `candidates`, each a
    {"pinyin", "english"} dict from cedict_utils.lookup_word) best matches
    how `word` is actually used in `sentence`. Falls back to index 0
    (CEDICT's own listed order, which generally puts the most common sense
    first) when there's only one candidate, no sentence to compare
    against, or Claude is unavailable/fails."""
    if len(candidates) < 2 or not sentence or client is None:
        return 0

    options = "\n".join(f"{i + 1}. ({c['pinyin']}) {c['english']}" for i, c in enumerate(candidates))
    prompt = f"""You are a Chinese-English dictionary editor. The word
"{word}" has multiple dictionary meanings/readings. Which one is used in
this sentence?

Sentence: "{sentence}"

Candidate meanings:
{options}

Output ONLY the number of the matching candidate. No explanation.
"""
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=8,
            system="You are a precise bilingual dictionary editor.",
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        m = re.match(r"\d+", raw)
        if not m:
            return 0
        idx = int(m.group()) - 1
        return idx if 0 <= idx < len(candidates) else 0
    except Exception as e:
        print(f"  [warning] cedict sense-selection failed for '{word}': {e}")
        return 0


def register_cedict_word(db, hanzi: str, unit: int, incomplete_here: Optional[VocabSense] = None) -> None:
    """Registers a word found in CEDICT. CEDICT usually returns SEVERAL
    candidates for one hanzi (different glosses and/or different
    readings) -- picks whichever one best fits this unit's usage as the
    TAUGHT sense (sense-matched against any existing senses via
    register_word_sense, same as every other outside-the-index
    definition), and stores every OTHER candidate as an UNTAUGHT
    reference sense (unit_number=None) so the word's full dictionary
    entry stays available -- e.g. for an app-side "other definitions"
    list -- without cluttering the curriculum-facing taught senses.

    If `incomplete_here` is given (an existing incomplete sense already
    homed at `unit`), fills it in rather than registering a new sense on
    top of it."""
    candidates = lookup_word(hanzi)
    if not candidates:
        return

    sentence_for_def = find_example_sentence(db, unit, hanzi, hsk_level=HSK_LEVEL)
    best_idx = select_best_cedict_sense(hanzi, candidates, sentence_for_def)
    best = candidates[best_idx]

    warning = cross_check_pinyin(hanzi, best["pinyin"])
    if warning:
        print(f"  [pinyin-warning] {warning}")

    english = resolve_primary_definition(hanzi, best["english"], sentence_for_def)
    if english != best["english"]:
        print(f"  [definition-refined] '{hanzi}': CEDICT said '{best['english']}' -> "
              f"using '{english}' based on curriculum sentence.")

    if incomplete_here is not None:
        update_incomplete_sense(db, incomplete_here, best["pinyin"], english)
        print(f"  [cedict] Filled in: {hanzi} ({best['pinyin']}) -> {english} [Unit {unit}]")
    else:
        register_word_sense(db, hanzi, unit, best["pinyin"], english, sentence=sentence_for_def)
        print(f"  [cedict] Added: {hanzi} ({best['pinyin']}) -> {english} [Unit {unit}]")

    if len(candidates) > 1:
        print(f"  [cedict] '{hanzi}' has {len(candidates) - 1} other dictionary "
              f"meaning(s) -- storing as untaught reference sense(s).")
    for i, cand in enumerate(candidates):
        if i == best_idx:
            continue
        # unit_number=None: a real dictionary meaning, just not (yet)
        # taught anywhere in the curriculum. upsert_vocab_sense's
        # (vocab, unit_id=None, english) uniqueness keeps this idempotent
        # across reruns.
        upsert_vocab_sense(db, hanzi, cand["pinyin"], cand["english"],
                            unit_number=None, hsk_level=HSK_LEVEL)


# --- Parent Word Recovery ---

def try_recover_parent_word(db, parent: str, unit, senses_map: dict, valid_indexed_words: set) -> bool:
    """When a tag is rejected as a sub-character of `parent`, try to add the
    parent word to the index if it's a real word and not already indexed.
    Returns True if the index was modified."""
    if not parent:
        return False

    if parent in senses_map:
        modified = False
        for sense in senses_map[parent]:
            normalized_pinyin = clean_pinyin(sense.pinyin or "")
            if normalized_pinyin != (sense.pinyin or ""):
                print(f"  [normalized] '{parent}' had stale formatting -- "
                      f"pinyin '{sense.pinyin}' -> '{normalized_pinyin}'.")
                sense.pinyin = normalized_pinyin
                db.flush()
                modified = True
        return modified

    if parent in valid_indexed_words:
        return False

    # --- NEW: is `parent` a real single dictionary word, or a productive
    # multi-word construction (surname + title, e.g. "李老师", "王小姐") that
    # isn't itself lexicalized? Same CEDICT+jieba guard resegment_bad_tag()
    # already uses for bad original tags -- without it here, Claude will
    # happily "define" a two-word phrase as if it were one atomic vocab
    # item, and we'd write a bogus compound like "李老师" into Vocab instead
    # of registering the real underlying words (李 + 老师).
    if not lookup_word(parent) and len(parent) > 1:
        segments = segment_into_words(parent)
        if len(segments) > 1:
            print(f"  [multi-word-parent] '{parent}' isn't a single CEDICT word -- "
                  f"splits into {segments}. Registering the individual words "
                  f"instead of '{parent}' as one compound.")
            modified = False
            for seg in segments:
                if seg not in senses_map and seg not in valid_indexed_words:
                    ensure_word_registered(db, seg, unit)
                    senses_map[seg] = get_senses_for_vocab(db, seg)
                    valid_indexed_words.add(seg)
                    modified = True
            return modified

    # Call CEDICT first for the parent word (before falling back to Claude),
    # same as everywhere else in this script.
    if lookup_word(parent):
        register_cedict_word(db, parent, unit)
        senses_map[parent] = get_senses_for_vocab(db, parent)
        valid_indexed_words.add(parent)
        print(f"  [recovered] '{parent}' is a valid CEDICT word (tagging had split it) "
              f"-- added as vocab entry [Unit {unit}].")
        return True

    # Call Claude for the parent word (without a specific sentence context)
    analysis = analyze_vocab(parent)
    parent_pinyin = analysis.get("pinyin", "UNKNOWN_PINYIN")
    parent_english = analysis.get("definition", "UNKNOWN_ENGLISH")

    if parent_pinyin == "UNKNOWN_PINYIN" or parent_english == "UNKNOWN_ENGLISH":
        print(f"  [note] parent word '{parent}' couldn't be defined by AI either "
              f"-- not added. Worth checking the tagging for unit {unit} manually.")
        return False

    warning = cross_check_pinyin(parent, parent_pinyin)
    if warning:
        print(f"  [pinyin-warning] {warning}")

    register_word_sense(db, parent, unit, parent_pinyin, parent_english)
    senses_map[parent] = get_senses_for_vocab(db, parent)
    valid_indexed_words.add(parent)
    print(f"  [recovered] '{parent}' is a valid word (tagging had split it) "
          f"-- added as vocab entry ({parent_pinyin}) -> {parent_english} [Unit {unit}].")
    return True


# --- Multi-word Tag Detection & Repair ---
#
# Some upstream tagging bug (in sentence_parser.py, import_sentences.py, or
# the Claude tagger they call) can let a multi-word span through as if it
# were a single SentenceVocab tag -- e.g. "太热了" (太 + 热 + 了, three
# separate words) getting tagged as one "word" with a made-up combined
# pronunciation. CEDICT + jieba catch this BEFORE any Claude call: if the
# dictionary doesn't recognize the tag as one word, segment it and register
# each real word individually instead of trusting whatever Claude would
# have said about the combined (nonexistent) "word".

def ensure_word_registered(db, word: str, unit) -> None:
    """Makes sure `word` has at least one COMPLETE sense. CEDICT first
    (authoritative, free, no API call) -- Claude only as a fallback for
    words CEDICT doesn't have (names, very colloquial terms, genuine
    textbook-specific compounds). Routed through register_word_sense so a
    word that already has a sense elsewhere doesn't get a spurious
    duplicate if this appearance turns out to mean the same thing."""
    existing_senses = get_senses_for_vocab(db, word)
    if any(s.pinyin and s.pinyin != "UNKNOWN_PINYIN" and s.english and s.english != "UNKNOWN_ENGLISH"
           for s in existing_senses):
        return  # already has at least one properly registered sense

    if lookup_word(word):
        register_cedict_word(db, word, unit)
        return

    sentence = find_example_sentence(db, unit, word, hsk_level=HSK_LEVEL)
    analysis = analyze_vocab(word, sentence)
    pinyin = analysis.get("pinyin", "UNKNOWN_PINYIN")
    english = analysis.get("definition", "UNKNOWN_ENGLISH")
    warning = cross_check_pinyin(word, pinyin)
    if warning:
        print(f"  [pinyin-warning] {warning}")
    register_word_sense(db, word, unit, pinyin, english, sentence=sentence)
    print(f"  [claude] registered '{word}' ({pinyin}) -> {english}")

def resegment_bad_tag(db, bad_hanzi: str, segments: list[str], unit) -> None:
    """`bad_hanzi` isn't a real word (CEDICT doesn't know it and jieba splits
    it into `segments`), but it may already exist as a bogus Vocab row and/or
    be tagged onto one or more sentences as if it were one word. This:
      1. Makes sure each real word in `segments` has its own proper Vocab row.
      2. Rewrites every sentence's tag list that referenced the bad combined
         tag, splicing in the individual words in its place.
      3. Deletes Question rows that tested the bogus combined "word" (they'll
         regenerate correctly for the real words on the next
         create_questions.py run).
      4. Removes the bad Vocab row itself.
    """
    for seg in segments:
        ensure_word_registered(db, seg, unit)

    bad_vocab = db.query(Vocab).filter(Vocab.hanzi == bad_hanzi).first()
    if bad_vocab is None:
        return  # was never actually created as a Vocab row -- just a phantom gap entry

    affected_links = db.query(SentenceVocab).filter(SentenceVocab.vocab_id == bad_vocab.id).all()
    affected_sentence_ids = {link.sentence_id for link in affected_links}

    for sentence_id in affected_sentence_ids:
        old_links = (
            db.query(SentenceVocab)
            .filter(SentenceVocab.sentence_id == sentence_id)
            .order_by(SentenceVocab.position)
            .all()
        )
        new_tags = []
        for link in old_links:
            if link.vocab_id == bad_vocab.id:
                new_tags.extend(segments)
            else:
                tag_vocab = db.query(Vocab).filter(Vocab.id == link.vocab_id).first()
                if tag_vocab:
                    new_tags.append(tag_vocab.hanzi)

        # delete-then-reinsert avoids any (sentence_id, position) collision
        # mid-transaction from shifting positions in place
        db.query(SentenceVocab).filter(SentenceVocab.sentence_id == sentence_id).delete()
        db.flush()

        sentence_row = db.query(Sentence).filter(Sentence.id == sentence_id).first()
        for position, tag in enumerate(new_tags):
            tag_vocab = db.query(Vocab).filter(Vocab.hanzi == tag).first()
            if tag_vocab is None:
                continue  # shouldn't happen -- ensure_word_registered ran above for `segments`
            sense = None
            if sentence_row is not None:
                sense = resolve_sense_for_sentence(
                    db, tag_vocab.id, sentence_row.unit.unit_number, sentence_row.unit.hsk_level)
            db.add(SentenceVocab(sentence_id=sentence_id, vocab_id=tag_vocab.id,
                                  vocab_sense_id=sense.id if sense else None, position=position))
        db.flush()
        print(f"  [resegmented] sentence {sentence_id}: replaced '{bad_hanzi}' with {segments}")

    deleted_questions = db.query(Question).filter(Question.vocab_id == bad_vocab.id).delete()
    if deleted_questions:
        print(f"  [cleanup] removed {deleted_questions} question(s) that tested the bogus word '{bad_hanzi}'")

    db.delete(bad_vocab)
    db.flush()
    print(f"  [removed] '{bad_hanzi}' was not a real word -- removed from vocab")


# --- Main ---

def sync_index_definitions():
    print(f"Checking for missing or incomplete senses (HSK level {HSK_LEVEL})...\n")

    init_db()
    with get_session() as db:
        # 1. Map existing senses and identify which are incomplete
        senses_map, incomplete_ids = get_all_vocab_senses_with_status(db)
        valid_indexed_words = {
            hanzi for hanzi, senses in senses_map.items()
            if any(s.id not in incomplete_ids for s in senses)
        }

        # 2. Every (word, unit) pair actually used in this hsk_level's
        # curriculum that has no COMPLETE sense homed at or before that unit
        # yet -- a word can need coverage at several units now (once per
        # sense it's genuinely taught with), not just its first appearance.
        gaps = get_uncovered_word_units(db, hsk_level=HSK_LEVEL)

        if not gaps:
            print("All taught words are already covered by a complete sense!")
            return

        print(f"Found {len(gaps)} (word, unit) gap(s) needing AI definition lookup/repair.\n")

        rejected_cache = load_rejected_vocab_cache()
        if rejected_cache:
            print(f"Loaded {len(rejected_cache)} previously-rejected word(s) from "
                  f"{REJECTED_VOCAB_CACHE} -- these will be skipped without an AI call.\n")

        updated_count = 0
        skipped_non_standalone = []

        # 3. Fetch pinyin + contextual definition for each gap via CEDICT/Claude
        for tag, unit in gaps:
            # If an INCOMPLETE sense is already homed exactly at this unit,
            # this gap is that sense waiting to be filled in -- repair it in
            # place rather than registering a brand-new sense on top of it.
            incomplete_here = next(
                (s for s in senses_map.get(tag, [])
                 if s.id in incomplete_ids and s.unit is not None
                 and s.unit.unit_number == unit and s.unit.hsk_level == HSK_LEVEL),
                None,
            )

            # Already known to be a sub-character -- skip immediately
            if tag in rejected_cache:
                cached = rejected_cache[tag]
                print(f"  [skip-cached] '{tag}' was previously rejected as a sub-character "
                      f"of '{cached['parent_word']}' ({cached['reasoning']}) — not adding.")
                skipped_non_standalone.append((tag, unit, cached["parent_word"]))
                if try_recover_parent_word(db, cached["parent_word"], unit, senses_map, valid_indexed_words):
                    updated_count += 1
                continue

            # --- Check CEDICT first: authoritative, free, no API call for
            # the pinyin, and correctly stores compound pronunciations
            # (e.g. 学生 -> xue2sheng5) that a character-by-character
            # source like pypinyin would get wrong. CEDICT often returns
            # SEVERAL candidates (different glosses and/or readings) --
            # register_cedict_word picks whichever fits this unit's
            # sentence as the taught sense and stores the rest as untaught
            # reference senses. ---
            if lookup_word(tag):
                register_cedict_word(db, tag, unit, incomplete_here=incomplete_here)
                senses_map[tag] = get_senses_for_vocab(db, tag)
                valid_indexed_words.add(tag)
                updated_count += 1
                continue

            # --- Not a dictionary word -- is it actually MULTIPLE words? ---
            # This is the failure mode that let "太热了" (太 + 热 + 了, three
            # words) get treated as one vocab entry with a made-up combined
            # pronunciation. Catch it here, before ever asking Claude to
            # "define" something that isn't a real word.
            if len(tag) > 1:
                segments = segment_into_words(tag)
                if len(segments) > 1:
                    print(f"  [multi-word] '{tag}' isn't a single CEDICT word -- "
                          f"splits into {segments}. Re-pointing sentence tags to "
                          f"the individual words instead of defining '{tag}' as one word.")
                    resegment_bad_tag(db, tag, segments, unit)
                    skipped_non_standalone.append((tag, unit, "+".join(segments)))
                    continue

            sentence = find_example_sentence(db, unit, tag, hsk_level=HSK_LEVEL)
            if not sentence:
                print(f"  [warning] No example sentence found for '{tag}' in unit {unit}; "
                      f"asking Claude for a general definition.")

            # --- Ask Claude for everything ---
            analysis = analyze_vocab(tag, sentence)

            if not analysis.get("is_standalone", True):
                parent = analysis.get("parent_word")
                reasoning = analysis.get("reasoning", "")
                print(f"  [skip] '{tag}' looks like a sub-character of "
                      f"'{parent}' in unit {unit}, not standalone vocab "
                      f"({reasoning}) — not adding.")
                skipped_non_standalone.append((tag, unit, parent))
                append_rejected_vocab_entry(tag, unit, parent, reasoning)
                rejected_cache[tag] = {"unit": str(unit), "parent_word": parent, "reasoning": reasoning}

                if try_recover_parent_word(db, parent, unit, senses_map, valid_indexed_words):
                    updated_count += 1

                continue

            pinyin = analysis.get("pinyin", "UNKNOWN_PINYIN")
            english = analysis.get("definition", "UNKNOWN_ENGLISH")

            # Cross-check Claude's pinyin against an independent source
            # before writing it. Advisory only -- Claude might be right and
            # pypinyin wrong for a context-dependent reading -- but this
            # surfaces a disagreement in the pipeline's own output instead
            # of it being discovered by a student later. See
            # vocab_pinyin_utils.cross_check_pinyin's docstring for the
            # specific failure mode this catches.
            warning = cross_check_pinyin(tag, pinyin)
            if warning:
                print(f"  [pinyin-warning] {warning}")

            if incomplete_here is not None:
                update_incomplete_sense(db, incomplete_here, pinyin, english)
                print(f"  Filled in: {tag} ({pinyin}) -> {english} [Unit {unit}]")
            else:
                register_word_sense(db, tag, unit, pinyin, english, sentence=sentence)
                print(f"  Added: {tag} ({pinyin}) -> {english} [Unit {unit}]")
            senses_map[tag] = get_senses_for_vocab(db, tag)
            valid_indexed_words.add(tag)
            updated_count += 1

        if updated_count > 0:
            print("-" * 30)
            print(f"Successfully processed {updated_count} sense(s).")

        if skipped_non_standalone:
            print("-" * 30)
            print(f"Skipped {len(skipped_non_standalone)} tag(s) flagged as non-standalone "
                  f"sub-characters (review if any of these look wrong):")
            for tag, unit, parent in skipped_non_standalone:
                print(f"  - '{tag}' (unit {unit}) -> part of '{parent}'")


if __name__ == "__main__":
    sync_index_definitions()