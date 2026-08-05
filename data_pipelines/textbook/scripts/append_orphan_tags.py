"""
Post-processing step: fill in missing vocab definitions and catch cases where
the tagging algorithm split a compound word incorrectly.

Workflow (unchanged logic, just DB-backed):
  1. Query which words SHOULD be indexed (from Vocab rows + all Sentence tags +
     FITB answers), combining sources in unit order
  2. Find gaps: words present in curriculum but missing from Vocab index or
     marked with UNKNOWN_* placeholders
  3. For each gap:
     a. Fetch pinyin from dictionary API (per-character fallback for compounds)
     b. Find an example sentence from DB and ask Claude for context
     c. Claude determines if the word is standalone or a sub-character of a
        larger compound
     d. If standalone: update/create Vocab row with definition
     e. If sub-character: cache rejection, try to recover the parent word
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
import requests
from typing import Optional

from app.core.config.shared import ENV_FILE
from app.core.config.textbook import REJECTED_VOCAB_CACHE

from app.textbook.database import (
    get_session, init_db, get_all_vocab_with_status, get_all_taught_words,
    find_example_sentence, update_vocab_entry,
)
from app.textbook.models import WordType

from app.textbook.models import Vocab

# --- Configuration ---
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
DICTIONARY_API_URL = f"{API_BASE_URL}/dictionary/"

load_dotenv(ENV_FILE)
api_key = os.environ.get("CLAUDE_API_KEY")
client = anthropic.Anthropic(api_key=api_key) if api_key else None
MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")


# --- Rejected Vocab Cache (unchanged) ---

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


# --- Dictionary API (unchanged) ---

_CL_SEGMENT_RE = re.compile(r"^CL:", re.IGNORECASE)


def clean_dictionary_english(english) -> str:
    if not isinstance(english, str) or not english:
        return english
    parts = [p.strip() for p in english.split(" / ")]
    kept = [p for p in parts if p and not _CL_SEGMENT_RE.match(p)]
    return " / ".join(kept) if kept else english


def clean_pinyin(pinyin: str) -> str:
    if not isinstance(pinyin, str):
        return pinyin
    return pinyin.strip().strip("[]").replace(" ", "")


def parse_dictionary_response(api_data):
    results = api_data.get("results", []) if isinstance(api_data, dict) else api_data
    if isinstance(results, list) and len(results) > 0:
        entry = results[0]
    elif isinstance(results, dict):
        entry = results
    else:
        return "UNKNOWN_PINYIN", "UNKNOWN_ENGLISH"

    pinyin = entry.get("pinyin", "UNKNOWN_PINYIN")
    raw_english = entry.get("english", "UNKNOWN_ENGLISH")
    pinyin = clean_pinyin(pinyin)

    if isinstance(raw_english, list):
        english = " / ".join(raw_english)
    else:
        english = raw_english
    english = clean_dictionary_english(english)

    return pinyin, english


def fetch_dictionary_entry(word: str):
    try:
        response = requests.get(f"{DICTIONARY_API_URL}{word}", timeout=5)
        if response.status_code == 200:
            return parse_dictionary_response(response.json())
        print(f"  [API {response.status_code}] Could not fetch definition for '{word}'")
        return "UNKNOWN_PINYIN", "UNKNOWN_ENGLISH"
    except requests.RequestException as e:
        print(f"  [Connection Error] API request failed for '{word}': {e}")
        return "UNKNOWN_PINYIN", "UNKNOWN_ENGLISH"


def fetch_pinyin_with_char_fallback(word: str):
    pinyin, english = fetch_dictionary_entry(word)
    if pinyin != "UNKNOWN_PINYIN":
        return pinyin, english

    if len(word) <= 1:
        return pinyin, english

    char_pinyins = []
    any_unknown = False
    for ch in word:
        ch_pinyin, _ = fetch_dictionary_entry(ch)
        if ch_pinyin == "UNKNOWN_PINYIN":
            any_unknown = True
            char_pinyins.append("?")
        else:
            char_pinyins.append(ch_pinyin)

    combined_pinyin = "".join(char_pinyins)
    if any_unknown:
        print(f"  [warning] Could not find pinyin for every character in '{word}' "
              f"(got '{combined_pinyin}') -- review manually.")
    else:
        print(f"  [fallback] '{word}' not found as a whole word; assembled pinyin "
              f"from individual characters: '{combined_pinyin}'")

    return combined_pinyin, english


# --- Claude Standalone Analysis (unchanged) ---

def analyze_vocab_in_sentence(word: str, sentence: str) -> dict:
    if client is None:
        print("  [warning] CLAUDE_API_KEY not configured; skipping AI disambiguation")
        return {
            "is_standalone": True,
            "definition": None,
            "parent_word": None,
            "reasoning": "Claude API client unavailable",
        }

    prompt = f"""You are a Chinese language curriculum expert.
Analyze the target word/character "{word}" as used in the following sentence:
"{sentence}"

Task:
1. Determine if "{word}" is used as an INDEPENDENT, STANDALONE vocabulary word/meaning in this sentence.
2. OR if "{word}" is merely a component character of a LARGER compound word/name (e.g., '卫' inside '大卫', or '视' inside '电视').

Output ONLY valid JSON matching this exact format. No markdown, no preambles:
{{
    "is_standalone": true or false,
    "parent_word": "The larger compound word if false, otherwise null",
    "definition": "A concise, natural English definition for the target word as used in THIS sentence context (null if is_standalone is false)",
    "reasoning": "Brief 1-sentence explanation"
}}
"""

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=256,
            system="You extract contextual vocabulary definitions and filter out sub-word characters for Chinese language learning.",
            messages=[{"role": "user", "content": prompt}],
        )

        raw_text = response.content[0].text
        json_match = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if json_match:
            raw_text = json_match.group(0)

        return json.loads(raw_text)

    except json.JSONDecodeError as e:
        print(f"  [Error] Failed to parse Claude JSON for '{word}': {e}")
    except Exception as e:
        print(f"  [Error] Claude API call failed for '{word}': {e}")

    return {
        "is_standalone": True,
        "definition": None,
        "parent_word": None,
        "reasoning": "Fallback due to error",
    }


# --- Parent Word Recovery (unchanged logic, DB-backed) ---

def try_recover_parent_word(db, parent: str, unit, vocab_map: dict, valid_indexed_words: set) -> bool:
    """When a tag is rejected as a sub-character of `parent`, try to add the
    parent word to the index if it's a real dictionary word and not already
    indexed. Returns True if the index was modified."""
    if not parent:
        return False

    if parent in vocab_map:
        existing = vocab_map[parent]
        normalized_pinyin = clean_pinyin(existing.pinyin or "")
        normalized_english = clean_dictionary_english(existing.english or "")
        if normalized_pinyin != (existing.pinyin or "") or normalized_english != (existing.english or ""):
            print(f"  [normalized] '{parent}' had stale formatting -- "
                  f"pinyin '{existing.pinyin}' -> '{normalized_pinyin}', "
                  f"english '{existing.english}' -> '{normalized_english}'.")
            existing.pinyin = normalized_pinyin
            existing.english = normalized_english
            db.flush()
            return True
        return False

    if parent in valid_indexed_words:
        return False

    parent_pinyin, parent_english = fetch_dictionary_entry(parent)
    if parent_pinyin == "UNKNOWN_PINYIN":
        print(f"  [note] parent word '{parent}' isn't in the dictionary either "
              f"-- not added. Worth checking the tagging for unit {unit} manually.")
        return False

    update_vocab_entry(db, parent, parent_pinyin, parent_english, unit)
    vocab_map[parent] = db.query(Vocab).filter(Vocab.hanzi == parent).first()
    valid_indexed_words.add(parent)
    print(f"  [recovered] '{parent}' is a real dictionary word (tagging had split it) "
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

        print(f"Found {len(missing_by_unit)} words needing definition lookup/repair.\n")

        rejected_cache = load_rejected_vocab_cache()
        if rejected_cache:
            print(f"Loaded {len(rejected_cache)} previously-rejected word(s) from "
                  f"{REJECTED_VOCAB_CACHE} -- these will be skipped without an AI call.\n")

        updated_count = 0
        skipped_non_standalone = []

        # 4. Fetch pinyin + contextual definition for each missing/retry word
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

            # --- pinyin: dictionary API, falling back to per-character lookup ---
            pinyin, dictionary_english = fetch_pinyin_with_char_fallback(tag)

            # --- contextual definition: find the sentence, ask Claude ---
            sentence = find_example_sentence(db, unit, tag)
            english = dictionary_english

            if sentence:
                analysis = analyze_vocab_in_sentence(tag, sentence)

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

                if analysis.get("definition"):
                    english = analysis["definition"]
            else:
                print(f"  [warning] No example sentence found for '{tag}' in unit {unit}; "
                      f"using dictionary definition as-is.")

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