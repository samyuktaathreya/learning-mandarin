import json
import os
import re
from pathlib import Path

import anthropic
import requests
from dotenv import load_dotenv

BASE_DIR = "/Users/spanishatlas/Documents/GitHub/learning-mandarin/app/language-app-data"
UNITS_FILE = os.path.join(BASE_DIR, "data/clean/units_output.json")
VOCAB_FILE = os.path.join(BASE_DIR, "data/clean/unit_vocab_tags.json")
INDEX_FILE = os.path.join(BASE_DIR, "data/clean/index_output.json")

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
DICTIONARY_API_URL = f"{API_BASE_URL}/dictionary/"

# --- Claude client setup (mirrors your OCR script) ---
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
api_key = os.environ.get("CLAUDE_API_KEY")
client = anthropic.Anthropic(api_key=api_key) if api_key else None
# Haiku is plenty for this — it's a short word+sentence -> JSON classification
# task, not something that needs a frontier model. Cheaper and faster too.
# NOTE: double check this model string is still current in your Anthropic
# console/docs before relying on it long-term.
MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")


def clean_pinyin(pinyin: str) -> str:
    """Defensive cleanup for stray bracket characters coming back from the
    dictionary API (e.g. 'fan4]' -> 'fan4')."""
    if not isinstance(pinyin, str):
        return pinyin
    return pinyin.strip().strip("[]").strip()


def parse_dictionary_response(api_data):
    """
    Parses response structure:
    {"word":"你好","results":[{"simplified":"你好","traditional":"你好","pinyin":"ni3 hao3","english":["hello; hi"]}]}
    """
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

    return pinyin, english


def find_example_sentence(units_data, unit, word):
    """
    Looks up a sentence for `word` within the given unit, using the
    units_output.json schema:
        units_data[unit]["sentences"] -> [{"hanzi", "english", "tags", "pinyin"}, ...]

    Each sentence explicitly lists the vocab words it contains in "tags",
    so we match on that rather than substring-searching "hanzi" (substring
    matching could false-positive, e.g. matching '你' inside '你们').

    Prefers the shortest matching sentence (simpler context = cleaner
    definition for a first introduction of the word).
    """
    unit_data = units_data.get(str(unit)) or units_data.get(unit)
    if not unit_data:
        return None

    candidates = [
        s["hanzi"]
        for s in unit_data.get("sentences", [])
        if word in s.get("tags", []) and s.get("hanzi")
    ]
    if not candidates:
        return None

    candidates.sort(key=len)
    return candidates[0]


def analyze_vocab_in_sentence(word: str, sentence: str) -> dict:
    """Uses Claude to determine if a target word/character is a standalone vocabulary item
    in the context of the sentence, or if it is just a sub-character of a larger word,
    and to produce a contextual English definition.
    """
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

        # Safely extract JSON even if Claude wraps it in markdown fences
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


def sync_index_definitions():
    """
    Independently re-discovers any words in unit_vocab_tags.json that do not
    exist in index_output.json (OR have placeholder UNKNOWN definitions),
    fetches accurate pinyin from the dictionary API, and asks Claude for a
    contextual English definition based on the sentence the word actually
    appears in (using units_output.json).
    """
    print("Checking for missing or incomplete definitions in index_output.json...\n")

    try:
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            index_data = json.load(f)
    except FileNotFoundError:
        print(f"Error: Could not find {INDEX_FILE}")
        return

    try:
        with open(VOCAB_FILE, "r", encoding="utf-8") as f:
            vocab_data = json.load(f)
    except FileNotFoundError:
        print(f"Error: Could not find {VOCAB_FILE}")
        return

    try:
        with open(UNITS_FILE, "r", encoding="utf-8") as f:
            units_data = json.load(f)
    except FileNotFoundError:
        print(f"[warning] Could not find {UNITS_FILE} — will fall back to "
              f"dictionary-only definitions (no sentence context available).")
        units_data = {}

    # 1. Map existing valid indexed words vs words needing a retry
    valid_indexed_words = set()
    needs_retry = set()

    for section in ["vocab", "grammar", "proper_nouns"]:
        for item in index_data.get(section, []):
            hanzi = item.get("hanzi")
            pinyin = item.get("pinyin", "")
            english = item.get("english", "")

            if "UNKNOWN_PINYIN" in pinyin or "UNKNOWN_ENGLISH" in english:
                needs_retry.add(hanzi)
            else:
                valid_indexed_words.add(hanzi)

    # 2. Build the full set of (word, unit) pairs that SHOULD be indexed.
    # This has to come from two places:
    #   - unit_vocab_tags.json: your curated vocab list
    #   - units_output.json sentence "tags": the words that actually appear
    #     in sentences, which is the ground truth for "what will the learner
    #     be asked about." A word can appear in a sentence's tags without
    #     ever having been added to unit_vocab_tags.json (e.g. '分'), and
    #     the old version of this script had no way of catching that since
    #     it only ever looked at unit_vocab_tags.json.
    word_units = {}  # word -> unit (first unit it's seen in)

    sorted_units = sorted([int(u) for u in vocab_data.keys() if u.isdigit()])
    for unit in sorted_units:
        unit_str = str(unit)
        for tag in vocab_data.get(unit_str, []):
            word_units.setdefault(tag, unit)

    for unit_str, unit_data in units_data.items():
        if not unit_str.isdigit():
            continue
        unit = int(unit_str)
        for sentence in unit_data.get("sentences", []):
            for tag in sentence.get("tags", []):
                word_units.setdefault(tag, unit)

    # 3. Diff against what's already validly indexed
    missing_by_unit = []
    for tag, unit in word_units.items():
        if tag not in valid_indexed_words or tag in needs_retry:
            missing_by_unit.append((tag, unit))
            valid_indexed_words.add(tag)
            needs_retry.discard(tag)

    if not missing_by_unit:
        print("All words in unit_vocab_tags.json are already present and fully defined in index_output.json!")
        return

    print(f"Found {len(missing_by_unit)} words needing definition lookup/repair.\n")

    if "vocab" not in index_data:
        index_data["vocab"] = []

    existing_vocab_map = {item["hanzi"]: item for item in index_data["vocab"]}
    updated_count = 0
    skipped_non_standalone = []

    # 3. Fetch pinyin + contextual definition for each missing/retry word
    for tag, unit in missing_by_unit:
        # --- pinyin: still from the dictionary API, as before ---
        try:
            response = requests.get(f"{DICTIONARY_API_URL}{tag}", timeout=5)
            if response.status_code == 200:
                pinyin, dictionary_english = parse_dictionary_response(response.json())
            else:
                print(f"  [API {response.status_code}] Could not fetch definition for '{tag}'")
                pinyin, dictionary_english = "UNKNOWN_PINYIN", "UNKNOWN_ENGLISH"
        except requests.RequestException as e:
            print(f"  [Connection Error] API request failed for '{tag}': {e}")
            pinyin, dictionary_english = "UNKNOWN_PINYIN", "UNKNOWN_ENGLISH"

        # --- contextual definition: find the sentence, ask Claude ---
        sentence = find_example_sentence(units_data, unit, tag)

        english = dictionary_english
        if sentence:
            analysis = analyze_vocab_in_sentence(tag, sentence)

            if not analysis.get("is_standalone", True):
                parent = analysis.get("parent_word")
                print(f"  [skip] '{tag}' looks like a sub-character of "
                      f"'{parent}' in unit {unit}, not standalone vocab "
                      f"({analysis.get('reasoning', '')}) — not adding.")
                skipped_non_standalone.append((tag, unit, parent))
                continue

            if analysis.get("definition"):
                english = analysis["definition"]
        else:
            print(f"  [warning] No example sentence found for '{tag}' in unit {unit}; "
                  f"using dictionary definition as-is.")

        # Overwrite existing entry if updating, else append
        if tag in existing_vocab_map:
            existing_vocab_map[tag]["pinyin"] = pinyin
            existing_vocab_map[tag]["english"] = english
            existing_vocab_map[tag]["unit"] = unit
            print(f"  Updated existing entry: {tag} ({pinyin}) -> {english} [Unit {unit}]")
        else:
            new_entry = {
                "hanzi": tag,
                "pinyin": pinyin,
                "english": english,
                "unit": unit,
            }
            index_data["vocab"].append(new_entry)
            existing_vocab_map[tag] = new_entry
            print(f"  Added new entry: {tag} ({pinyin}) -> {english} [Unit {unit}]")

        updated_count += 1

    # 4. Save sorted output back to index_output.json
    if updated_count > 0:
        index_data["vocab"] = sorted(
            index_data["vocab"], key=lambda x: x.get("pinyin", "")
        )

        with open(INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump(index_data, f, ensure_ascii=False, indent=2)

        print("-" * 30)
        print(f"Successfully processed {updated_count} entries in {INDEX_FILE}.")

    if skipped_non_standalone:
        print("-" * 30)
        print(f"Skipped {len(skipped_non_standalone)} tag(s) flagged as non-standalone "
              f"sub-characters (review unit_vocab_tags.json if any of these look wrong):")
        for tag, unit, parent in skipped_non_standalone:
            print(f"  - '{tag}' (unit {unit}) -> part of '{parent}'")


if __name__ == "__main__":
    sync_index_definitions()