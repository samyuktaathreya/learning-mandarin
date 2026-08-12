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
    get_session, init_db, get_all_vocab_with_status, get_all_taught_words,
    find_example_sentence, update_vocab_entry,
)
from app.textbook.models import WordType
from app.textbook.models import Vocab, Sentence, SentenceVocab, Question
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
    
# --- Parent Word Recovery ---

def try_recover_parent_word(db, parent: str, unit, vocab_map: dict, valid_indexed_words: set) -> bool:
    """When a tag is rejected as a sub-character of `parent`, try to add the
    parent word to the index if it's a real word and not already indexed.
    Returns True if the index was modified."""
    if not parent:
        return False

    if parent in vocab_map:
        existing = vocab_map[parent]
        normalized_pinyin = clean_pinyin(existing.pinyin or "")
        if normalized_pinyin != (existing.pinyin or ""):
            print(f"  [normalized] '{parent}' had stale formatting -- "
                  f"pinyin '{existing.pinyin}' -> '{normalized_pinyin}'.")
            existing.pinyin = normalized_pinyin
            db.flush()
            return True
        return False

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
                if seg not in vocab_map and seg not in valid_indexed_words:
                    ensure_word_registered(db, seg, unit)
                    vocab_map[seg] = db.query(Vocab).filter(Vocab.hanzi == seg).first()
                    valid_indexed_words.add(seg)
                    modified = True
            return modified

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

    update_vocab_entry(db, parent, parent_pinyin, parent_english, unit, hsk_level=HSK_LEVEL)
    vocab_map[parent] = db.query(Vocab).filter(Vocab.hanzi == parent).first()
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
    """Makes sure `word` has a proper Vocab row. CEDICT first (authoritative,
    free, no API call) -- Claude only as a fallback for words CEDICT
    doesn't have (names, very colloquial terms, genuine textbook-specific
    compounds)."""
    existing = db.query(Vocab).filter(Vocab.hanzi == word).first()
    if (existing and existing.pinyin and existing.pinyin != "UNKNOWN_PINYIN"
            and existing.english and existing.english != "UNKNOWN_ENGLISH"):
        return  # already properly registered, nothing to do

    cedict_entry = lookup_word(word)
    if cedict_entry:
        pinyin, raw_english = cedict_entry["pinyin"], cedict_entry["english"]
        warning = cross_check_pinyin(word, pinyin)
        if warning:
            print(f"  [pinyin-warning] {warning}")

        sentence_for_def = find_example_sentence(db, unit, word, hsk_level=HSK_LEVEL)
        english = resolve_primary_definition(word, raw_english, sentence_for_def)
        if english != raw_english:
            print(f"  [definition-refined] '{word}': CEDICT said '{raw_english}' -> "
                  f"using '{english}' based on curriculum sentence.")

        update_vocab_entry(db, word, pinyin, english, unit, hsk_level=HSK_LEVEL)
        print(f"  [cedict] registered '{word}' ({pinyin}) -> {english}")
        return

    sentence = find_example_sentence(db, unit, word, hsk_level=HSK_LEVEL)
    analysis = analyze_vocab(word, sentence)
    pinyin = analysis.get("pinyin", "UNKNOWN_PINYIN")
    english = analysis.get("definition", "UNKNOWN_ENGLISH")
    warning = cross_check_pinyin(word, pinyin)
    if warning:
        print(f"  [pinyin-warning] {warning}")
    update_vocab_entry(db, word, pinyin, english, unit, hsk_level=HSK_LEVEL)
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

        for position, tag in enumerate(new_tags):
            tag_vocab = db.query(Vocab).filter(Vocab.hanzi == tag).first()
            if tag_vocab is None:
                continue  # shouldn't happen -- ensure_word_registered ran above for `segments`
            db.add(SentenceVocab(sentence_id=sentence_id, vocab_id=tag_vocab.id, position=position))
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
    print(f"Checking for missing or incomplete definitions in Vocab table (HSK level {HSK_LEVEL})...\n")

    init_db()
    with get_session() as db:
        # 1. Map existing vocab and identify needs_retry entries
        vocab_map, needs_retry = get_all_vocab_with_status(db)
        valid_indexed_words = set(vocab_map.keys())

        # 2. Build the full set of (word, unit) pairs that SHOULD be indexed
        # for this hsk_level. Repairing HSK1 shouldn't pull in HSK2's
        # not-yet-loaded curriculum as "gaps".
        word_units = get_all_taught_words(db, hsk_level=HSK_LEVEL)

        # 3. Diff against what's already validly indexed
        missing_by_unit = [
            (tag, unit) for tag, unit in word_units.items()
            if tag not in valid_indexed_words or tag in needs_retry
        ]

        if not missing_by_unit:
            print("All taught words are already present and fully defined in Vocab!")
            return

        print(f"Found {len(missing_by_unit)} words needing AI definition lookup/repair.\n")

        rejected_cache = load_rejected_vocab_cache()
        if rejected_cache:
            print(f"Loaded {len(rejected_cache)} previously-rejected word(s) from "
                  f"{REJECTED_VOCAB_CACHE} -- these will be skipped without an AI call.\n")

        updated_count = 0
        skipped_non_standalone = []

        # 4. Fetch pinyin + contextual definition for each missing/retry word via Claude
        for tag, unit in missing_by_unit:
            # Already known to be a sub-character -- skip immediately
            if tag in rejected_cache:
                cached = rejected_cache[tag]
                print(f"  [skip-cached] '{tag}' was previously rejected as a sub-character "
                      f"of '{cached['parent_word']}' ({cached['reasoning']}) — not adding.")
                skipped_non_standalone.append((tag, unit, cached["parent_word"]))
                if try_recover_parent_word(db, cached["parent_word"], unit, vocab_map, valid_indexed_words):
                    updated_count += 1
                continue

            # --- Check CEDICT first: authoritative, free, no API call, and
            # correctly stores compound pronunciations (e.g. 学生 ->
            # xue2sheng5) that a character-by-character source like
            # pypinyin would get wrong. ---
            cedict_entry = lookup_word(tag)
            if cedict_entry:
                pinyin, raw_english = cedict_entry["pinyin"], cedict_entry["english"]
                warning = cross_check_pinyin(tag, pinyin)
                if warning:
                    print(f"  [pinyin-warning] {warning}")

                sentence_for_def = find_example_sentence(db, unit, tag, hsk_level=HSK_LEVEL)
                english = resolve_primary_definition(tag, raw_english, sentence_for_def)
                if english != raw_english:
                    print(f"  [definition-refined] '{tag}': CEDICT said '{raw_english}' -> "
                          f"using '{english}' based on curriculum sentence.")

                if update_vocab_entry(db, tag, pinyin, english, unit, hsk_level=HSK_LEVEL):
                    vocab_map[tag] = db.query(Vocab).filter(Vocab.hanzi == tag).first()
                    print(f"  [cedict] Added/Updated: {tag} ({pinyin}) -> {english} "
                          f"[Unit {unit}] (no AI call needed)")
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

                if try_recover_parent_word(db, parent, unit, vocab_map, valid_indexed_words):
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

            # Update or create the vocab entry
            if update_vocab_entry(db, tag, pinyin, english, unit, hsk_level=HSK_LEVEL):
                vocab_map[tag] = db.query(Vocab).filter(Vocab.hanzi == tag).first()
                if tag in vocab_map and vocab_map[tag]:
                    print(f"  Added/Updated: {tag} ({pinyin}) -> {english} [Unit {unit}]")
                    updated_count += 1

        if updated_count > 0:
            print("-" * 30)
            print(f"Successfully processed {updated_count} entries in Vocab table.")

        if skipped_non_standalone:
            print("-" * 30)
            print(f"Skipped {len(skipped_non_standalone)} tag(s) flagged as non-standalone "
                  f"sub-characters (review if any of these look wrong):")
            for tag, unit, parent in skipped_non_standalone:
                print(f"  - '{tag}' (unit {unit}) -> part of '{parent}'")


if __name__ == "__main__":
    sync_index_definitions()