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
REJECTED_VOCAB_CACHE_FILE = os.path.join(BASE_DIR, "data/intermediate/hsk1-rejected-vocab-cache.txt")

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


def load_rejected_vocab_cache() -> dict:
    """Loads previously-rejected (non-standalone) words so they're never sent
    to Claude again. Format: one tab-separated line per word:
        word<TAB>unit<TAB>parent_word<TAB>reasoning
    Returns {word: {"unit": str, "parent_word": str, "reasoning": str}}.
    Missing file (first run) just means an empty cache -- not an error."""
    if not os.path.exists(REJECTED_VOCAB_CACHE_FILE):
        return {}

    cache = {}
    with open(REJECTED_VOCAB_CACHE_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            parts = line.split("\t")
            # Tolerate short/malformed lines rather than crashing the whole run
            word = parts[0] if len(parts) > 0 else ""
            unit = parts[1] if len(parts) > 1 else ""
            parent_word = parts[2] if len(parts) > 2 else ""
            reasoning = parts[3] if len(parts) > 3 else ""
            if word:
                cache[word] = {"unit": unit, "parent_word": parent_word, "reasoning": reasoning}
    return cache


def append_rejected_vocab_entry(word: str, unit, parent_word: str, reasoning: str):
    """Appends one rejected word to the cache file. Replaces newlines/tabs in
    reasoning so the tab-separated format doesn't break."""
    os.makedirs(os.path.dirname(REJECTED_VOCAB_CACHE_FILE), exist_ok=True)
    safe_reasoning = (reasoning or "").replace("\t", " ").replace("\n", " ").strip()
    safe_parent = (parent_word or "").replace("\t", " ").replace("\n", " ").strip()
    with open(REJECTED_VOCAB_CACHE_FILE, "a", encoding="utf-8") as f:
        f.write(f"{word}\t{unit}\t{safe_parent}\t{safe_reasoning}\n")


def try_recover_parent_word(parent: str, unit, existing_vocab_map: dict, valid_indexed_words: set, index_data: dict) -> bool:
    """When a tag is rejected as a sub-character of `parent` (e.g. tagging
    split '早饭' into '早' + '饭' separately), `parent` is often the word that
    SHOULD have been tagged. If the dictionary confirms `parent` is a real
    headword and it isn't already indexed, add it now instead of silently
    losing the word entirely.

    Must be called on EVERY rejection -- both a freshly-Claude-rejected tag
    and a cache-hit rejection -- otherwise a run that hits the rejected-vocab
    cache for every tag never attempts recovery at all and silently no-ops.

    IMPORTANT: a recovered word like '早饭' never appears as a "tag" anywhere
    in units_output.json/unit_vocab_tags.json (only its split characters '早'
    and '饭' do), so it can NEVER re-enter the normal missing_by_unit /
    needs_retry loop once it's been added once. This function is the only
    code path that ever looks at it again -- so if it's already indexed, this
    re-normalizes its pinyin/english in place (e.g. fixing stale
    'zao3 fan4' -> 'zao3fan4' spacing or stripping CC-CEDICT 'CL:' junk from
    an entry written by an older version of this script) rather than assuming
    "already there" means "already correct".

    Returns True if the index was newly added OR changed by normalization, so
    the caller can bump its updated_count."""
    if not parent:
        return False

    if parent in existing_vocab_map:
        entry = existing_vocab_map[parent]
        normalized_pinyin = clean_pinyin(entry.get("pinyin", ""))
        normalized_english = clean_dictionary_english(entry.get("english", ""))
        if normalized_pinyin != entry.get("pinyin") or normalized_english != entry.get("english"):
            print(f"  [normalized] '{parent}' had stale formatting -- "
                  f"pinyin '{entry.get('pinyin')}' -> '{normalized_pinyin}', "
                  f"english '{entry.get('english')}' -> '{normalized_english}'.")
            entry["pinyin"] = normalized_pinyin
            entry["english"] = normalized_english
            return True
        return False

    if parent in valid_indexed_words:
        # Indexed under grammar/proper_nouns rather than vocab, or otherwise
        # tracked outside existing_vocab_map -- nothing for us to normalize.
        return False

    parent_pinyin, parent_english = fetch_dictionary_entry(parent)
    if parent_pinyin == "UNKNOWN_PINYIN":
        print(f"  [note] parent word '{parent}' isn't in the dictionary either "
              f"-- not added. Worth checking the OCR/tagging for unit {unit} manually.")
        return False

    recovered_entry = {
        "hanzi": parent,
        "pinyin": parent_pinyin,
        "english": parent_english,
        "unit": unit,
    }
    index_data["vocab"].append(recovered_entry)
    existing_vocab_map[parent] = recovered_entry
    valid_indexed_words.add(parent)
    print(f"  [recovered] '{parent}' is a real dictionary word (tagging had split it) "
          f"-- added as vocab entry ({parent_pinyin}) -> {parent_english} [Unit {unit}].")
    return True


def fetch_dictionary_entry(word: str):
    """Single dictionary API call for one word/character. Returns (pinyin, english),
    both possibly the UNKNOWN_* placeholders on any failure."""
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
    """Looks up `word` as a whole first. If the dictionary doesn't have it as
    a single entry (common for multi-character phrases like '太热了' that
    aren't themselves a dictionary headword), falls back to looking up each
    character individually and joining their pinyin together -- better than
    leaving the whole word as UNKNOWN_PINYIN.

    Returns (pinyin, english). `english` is only ever the whole-word dictionary
    definition (there's no sane way to combine per-character definitions into
    one), so on fallback it stays whatever the whole-word lookup returned --
    that's fine here since sync_index_definitions prefers the Claude
    contextual definition over this anyway and only uses it when Claude's
    definition is unavailable.
    """
    pinyin, english = fetch_dictionary_entry(word)
    if pinyin != "UNKNOWN_PINYIN":
        return pinyin, english

    if len(word) <= 1:
        return pinyin, english  # nothing smaller to fall back to

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


_CL_SEGMENT_RE = re.compile(r"^CL:", re.IGNORECASE)


def clean_dictionary_english(english) -> str:
    """CC-CEDICT often bundles a measure-word/classifier segment into the same
    string, e.g. 'breakfast / CL:份[fen4],頓|顿[dun4],次[ci4],餐[can1]'. That's
    correct dictionary data but not something a beginner learner needs to see
    as their vocab definition, so strip any segment that's purely a
    classifier note, keeping the real definition(s)."""
    if not isinstance(english, str) or not english:
        return english
    parts = [p.strip() for p in english.split(" / ")]
    kept = [p for p in parts if p and not _CL_SEGMENT_RE.match(p)]
    return " / ".join(kept) if kept else english  # never return an empty string


def clean_pinyin(pinyin: str) -> str:
    """Defensive cleanup for stray bracket characters coming back from the
    dictionary API (e.g. 'fan4]' -> 'fan4') and removes all spaces so pinyin
    is stored in one consistent format (e.g. 'zao3fan4', not 'zao3 fan4')."""
    if not isinstance(pinyin, str):
        return pinyin
    return pinyin.strip().strip("[]").replace(" ", "")


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
    english = clean_dictionary_english(english)

    return pinyin, english


def find_example_sentence(units_data, unit, word):
    """
    Looks up a sentence for `word` within the given unit, checking two
    sources in units_output.json:
        units_data[unit]["sentences"]          -> [{"hanzi", "tags", ...}, ...]
        units_data[unit]["fill_in_the_blank"]   -> [{"answer", "full_sentence", ...}, ...]

    "sentences" entries explicitly list their vocab words in "tags", so we
    match on that rather than substring-searching "hanzi" (substring matching
    could false-positive, e.g. matching '你' inside '你们'). "fill_in_the_blank"
    entries have no "tags" field, but "answer" IS the word itself, so an exact
    match against "answer" plays the same role.

    Prefers the shortest matching sentence across both sources (simpler
    context = cleaner definition for a first introduction of the word).
    """
    unit_data = units_data.get(str(unit)) or units_data.get(unit)
    if not unit_data:
        return None

    candidates = [
        s["hanzi"]
        for s in unit_data.get("sentences", [])
        if word in s.get("tags", []) and s.get("hanzi")
    ]
    candidates += [
        fitb["full_sentence"]
        for fitb in unit_data.get("fill_in_the_blank", [])
        if fitb.get("answer") == word and fitb.get("full_sentence")
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
    # This has to come from three places:
    #   - unit_vocab_tags.json: your curated vocab list
    #   - units_output.json "sentences[].tags": words that actually appear in
    #     sentences -- ground truth for what a learner will be tested on
    #   - units_output.json "fill_in_the_blank[].answer": the blanked-out word
    #     itself. These have no "tags" field, but "answer" IS the word, and a
    #     word can appear ONLY here (e.g. '身体' in a fill-in-the-blank) without
    #     ever showing up in a sentence's tags or unit_vocab_tags.json -- the
    #     old version of this script had no way of catching that.
    #
    # Units are walked in ascending numeric order (not dict/insertion order)
    # so "first unit a word is seen in" is actually its earliest unit, since a
    # word can recur across many later units once introduced.
    word_units = {}  # word -> unit (first unit it's seen in)

    all_unit_numbers = sorted({
        int(u) for u in set(vocab_data.keys()) | set(units_data.keys())
        if u.isdigit()
    })

    for unit in all_unit_numbers:
        unit_str = str(unit)

        for tag in vocab_data.get(unit_str, []):
            word_units.setdefault(tag, unit)

        unit_data = units_data.get(unit_str, {})

        for sentence in unit_data.get("sentences", []):
            for tag in sentence.get("tags", []):
                word_units.setdefault(tag, unit)

        for fitb in unit_data.get("fill_in_the_blank", []):
            answer = fitb.get("answer")
            if answer:
                word_units.setdefault(answer, unit)

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

    rejected_cache = load_rejected_vocab_cache()
    if rejected_cache:
        print(f"Loaded {len(rejected_cache)} previously-rejected word(s) from "
              f"{REJECTED_VOCAB_CACHE_FILE} -- these will be skipped without an AI call.\n")

    if "vocab" not in index_data:
        index_data["vocab"] = []

    existing_vocab_map = {item["hanzi"]: item for item in index_data["vocab"]}
    updated_count = 0
    skipped_non_standalone = []

    # 3. Fetch pinyin + contextual definition for each missing/retry word
    for tag, unit in missing_by_unit:
        # Already known (from a prior run) to be a sub-character of a larger
        # word rather than standalone vocab -- skip immediately, no dictionary
        # call, no Claude call.
        if tag in rejected_cache:
            cached = rejected_cache[tag]
            print(f"  [skip-cached] '{tag}' was previously rejected as a sub-character "
                  f"of '{cached['parent_word']}' ({cached['reasoning']}) — not adding.")
            skipped_non_standalone.append((tag, unit, cached["parent_word"]))
            if try_recover_parent_word(cached["parent_word"], unit, existing_vocab_map, valid_indexed_words, index_data):
                updated_count += 1
            continue

        # --- pinyin: dictionary API, falling back to per-character lookup ---
        pinyin, dictionary_english = fetch_pinyin_with_char_fallback(tag)

        # --- contextual definition: find the sentence, ask Claude ---
        sentence = find_example_sentence(units_data, unit, tag)

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

                if try_recover_parent_word(parent, unit, existing_vocab_map, valid_indexed_words, index_data):
                    updated_count += 1

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