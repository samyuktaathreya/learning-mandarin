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
from app.textbook.models import Vocab

# --- Configuration ---
load_dotenv(ENV_FILE)
api_key = os.environ.get("CLAUDE_API_KEY")
client = anthropic.Anthropic(api_key=api_key) if api_key else None
MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")


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
    """Normalizes pinyin formatting to ensure consistency (e.g. removing stray spaces)."""
    if not isinstance(pinyin, str):
        return pinyin
    return pinyin.strip().strip("[]").replace(" ", "")


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
    "pinyin": "The pinyin for the word (do not include spaces between syllables for a single compound word)",
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
        # Just check for formatting staleness
        if normalized_pinyin != (existing.pinyin or ""):
            print(f"  [normalized] '{parent}' had stale formatting -- "
                  f"pinyin '{existing.pinyin}' -> '{normalized_pinyin}'.")
            existing.pinyin = normalized_pinyin
            db.flush()
            return True
        return False

    if parent in valid_indexed_words:
        return False

    # Call Claude for the parent word (without a specific sentence context)
    analysis = analyze_vocab(parent)
    parent_pinyin = analysis.get("pinyin", "UNKNOWN_PINYIN")
    parent_english = analysis.get("definition", "UNKNOWN_ENGLISH")

    if parent_pinyin == "UNKNOWN_PINYIN" or parent_english == "UNKNOWN_ENGLISH":
        print(f"  [note] parent word '{parent}' couldn't be defined by AI either "
              f"-- not added. Worth checking the tagging for unit {unit} manually.")
        return False

    update_vocab_entry(db, parent, parent_pinyin, parent_english, unit)
    vocab_map[parent] = db.query(Vocab).filter(Vocab.hanzi == parent).first()
    valid_indexed_words.add(parent)
    print(f"  [recovered] '{parent}' is a valid word (tagging had split it) "
          f"-- added as vocab entry ({parent_pinyin}) -> {parent_english} [Unit {unit}].")
    return True


# --- Main ---

def sync_index_definitions():
    print("Checking for missing or incomplete definitions in Vocab table...\n")

    init_db()
    with get_session() as db:
        # 1. Map existing vocab and identify needs_retry entries
        vocab_map, needs_retry = get_all_vocab_with_status(db)
        valid_indexed_words = set(vocab_map.keys())

        # 2. Build the full set of (word, unit) pairs that SHOULD be indexed
        word_units = get_all_taught_words(db)

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

            sentence = find_example_sentence(db, unit, tag)
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

            # Update or create the vocab entry
            if update_vocab_entry(db, tag, pinyin, english, unit):
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