"""
Extracts grammar tips from the OCR'd unit markdown (unchanged: same regex-based
section splitting as before -- that logic only touches OCR cache files, not
JSON, so nothing there needed to change) and matches each tip to the
sentences that demonstrate it.

What changed vs. the JSON version:
  - No more `remove_grammar_tips(units_output.json)` reset step -- there's no
    JSON to clear. Re-running this script is naturally idempotent:
    get_or_create_grammar_tip() is keyed on (unit, raw_text), so re-extracting
    the same tip text doesn't create a duplicate row or re-spend an API call
    reformatting it (see the `content_json in ("{}","", None)` guard), and
    link_sentence_grammar() is a no-op if the (sentence, tip) link already
    exists.
  - Sentences to match against come from db.get_sentences_for_unit(unit_number)
    instead of units_output.json[unit]["sentences"].
  - A tip's matches are written as SentenceGrammar rows -- explicitly
    many-to-many, so one tip can attach to many sentences AND one sentence
    can carry many tips, exactly like the old `grammar_tip: [tip, tip, ...]`
    list per sentence, just normalized instead of duplicated per sentence.
"""

import os
import json
import re
from pathlib import Path
from dotenv import load_dotenv
import anthropic
from typing import Optional

from app.core.config.textbook import OCR_PATH, GRAMMAR_TIP_SOP, REFORMAT_GRAMMAR_TIP_SOP
from app.core.config.shared import ENV_FILE

from app.textbook.db_utils import get_session, init_db, get_sentences_for_unit, get_or_create_grammar_tip, link_sentence_grammar

# ---------------------------------------------------------
# Configuration & Setup
# ---------------------------------------------------------
MODEL = "claude-haiku-4-5"
MAX_TOKENS = 1024
TEMPERATURE = 0
MAX_RETRIES = 2

# HSK level being processed this run (threaded the same way main.py already
# threads UNITS_TO_PROCESS / SOURCES_TO_PROCESS to sentence_parser.py).
HSK_LEVEL = int(os.environ.get("HSK_LEVEL", "1"))

load_dotenv(ENV_FILE)
api_key = os.environ.get("CLAUDE_API_KEY")
client = anthropic.Anthropic(api_key=api_key) if api_key else None


# ---------------------------------------------------------
# Step 1: Extract Grammar Tips from Markdown (unchanged)
# ---------------------------------------------------------
def discover_textbook_units(level_dir: Path) -> list:
    """Which unit numbers actually have cached textbook OCR for this
    hsk_level. Scans .../OCR_cache/hsk_textbook/{level}/ for unit{n}.md
    files. Replaces the old hardcoded range(3, 16), which was HSK1's
    specific unit numbering and won't hold for other levels."""
    if not level_dir.exists():
        return []
    units = []
    for f in level_dir.glob("unit*.md"):
        m = re.match(r"^unit(\d+)\.md$", f.name)
        if m:
            units.append(int(m.group(1)))
    return sorted(units)


def parse_grammar_tips() -> dict:
    output_data = {}
    problem_units = []

    section_pattern = re.compile(
        r'##\s*注释\s*Notes(.*?)##\s*练习\s*Exercises',
        re.DOTALL | re.IGNORECASE
    )
    section_marker_pattern = re.compile(r'##\s*\[Section:\s*Note\s*\d+\]\s*\n*')
    heading_pattern = re.compile(r'(?m)^(?:##\s*)?(\d+)\s+(?=[^\s\d:])')

    # Cached textbook OCR lives under .../OCR_cache/hsk_textbook/{level}/unit{n}.md
    # matching sentence_parser.py's run_ocr() cache path
    textbook_ocr_dir = OCR_PATH / "hsk_textbook" / str(HSK_LEVEL)
    unit_numbers = discover_textbook_units(textbook_ocr_dir)
    if not unit_numbers:
        print(f" [warning] No cached textbook OCR found under {textbook_ocr_dir} "
              f"-- run sentence_parser.py first.")

    for unit in unit_numbers:
        file_path = textbook_ocr_dir / f"unit{unit}.md"
        if not file_path.exists():
            print(f" [warning] File not found: {file_path.name}")
            continue

        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        match = section_pattern.search(content)
        if not match:
            print(f" [warning] Could not find Notes/Exercises boundaries in Unit {unit}")
            continue

        notes_text = section_marker_pattern.sub('', match.group(1)).strip()
        matches = list(heading_pattern.finditer(notes_text))

        accepted = []
        expected = 1
        for m in matches:
            num = int(m.group(1))
            if num == expected:
                accepted.append(m.start())
                expected += 1

        if not accepted:
            print(f" [warning] No tip headings found in Unit {unit}")
            problem_units.append(unit)
            continue

        tips = []
        for i, start in enumerate(accepted):
            end = accepted[i + 1] if i + 1 < len(accepted) else len(notes_text)
            tips.append(notes_text[start:end].strip())

        if accepted[0] > 20:
            print(f" [warning] Unit {unit}: {accepted[0]} chars before first "
                  f"recognized heading -- possible missed tip 1")
            problem_units.append(unit)

        output_data[str(unit)] = tips

    if problem_units:
        print(f"\n [flagged for manual review] Units: {sorted(set(problem_units))}")

    return output_data


# ---------------------------------------------------------
# Step 2: Claude API Reformatting (structured JSON) & Matching (unchanged)
# ---------------------------------------------------------
def _validate_reformatted(obj) -> bool:
    if not isinstance(obj, dict) or "sections" not in obj:
        return False
    if not isinstance(obj["sections"], list) or len(obj["sections"]) == 0:
        return False

    for sec in obj["sections"]:
        if not isinstance(sec, dict):
            return False
        if "title" not in sec or "body" not in sec:
            return False
        if "|" in sec.get("body", ""):
            return False

        table = sec.get("table")
        if table is not None:
            if not isinstance(table, dict) or "headers" not in table or "rows" not in table:
                return False
            headers = table["headers"]
            rows = table["rows"]
            if not isinstance(headers, list) or not isinstance(rows, list) or len(rows) == 0:
                return False
            ncols = len(headers)
            for row in rows:
                if not isinstance(row, list) or len(row) != ncols:
                    return False
                if all((cell is None or str(cell).strip() == "") for cell in row):
                    return False
    return True


def reformat_grammar_tip_text(reformat_sop_text: str, raw_tip: str) -> Optional[dict]:
    if client is None:
        print(" [error] CLAUDE_API_KEY not configured; skipping API call.")
        return None

    user_content = (
        f"Here is the raw grammar tip:\n\n{raw_tip}\n\n"
        f"Reformat it per the system instructions. Return JSON only."
    )

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
                system=reformat_sop_text,
                messages=[{"role": "user", "content": [{"type": "text", "text": user_content}]}],
            )
            raw_text = response.content[0].text.strip()
            raw_text = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw_text.strip())
            parsed = json.loads(raw_text)
            if _validate_reformatted(parsed):
                return parsed
            print(f"  [retry {attempt}/{MAX_RETRIES}] malformed structure, retrying...")
        except (json.JSONDecodeError, IndexError) as e:
            print(f"  [retry {attempt}/{MAX_RETRIES}] parse error: {e}")
        except Exception as e:
            print(f" [error] API call failed during reformatting: {e}")
            return None

    print(" [error] Failed to get well-formed reformat after retries; skipping this tip.")
    return None


def get_matching_sentences(sop_text: str, structured_tip: dict, hanzi_list: list) -> list:
    if client is None:
        return []

    plain_text_parts = []
    for sec in structured_tip["sections"]:
        plain_text_parts.append(sec["title"])
        plain_text_parts.append(sec["body"])
        if sec.get("table"):
            plain_text_parts.append(" | ".join(sec["table"]["headers"]))
            for row in sec["table"]["rows"]:
                plain_text_parts.append(" | ".join(str(c) for c in row))
    plain_text = "\n".join(plain_text_parts)

    user_content = (
        f"Here is the Grammar Tip:\n\n{plain_text}\n\n"
        f"Here is the list of Hanzi sentences for this unit:\n"
        f"{json.dumps(hanzi_list, ensure_ascii=False)}\n\n"
        f"Output ONLY a JSON list of strings containing the exact hanzi sentences from the list above that demonstrate this grammar tip.\n"
        f"Example format: [\"sentence 1\", \"sentence 2\"]"
    )

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
            system=sop_text,
            messages=[{"role": "user", "content": [{"type": "text", "text": user_content}]}],
        )
        response_text = response.content[0].text
        match = re.search(r'\[.*\]', response_text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        print(f" [warning] Could not parse JSON from response: {response_text}")
        return []
    except Exception as e:
        print(f" [error] API call failed during matching: {e}")
        return []


# ---------------------------------------------------------
# Step 3: Main Execution Flow (DB-writing)
# ---------------------------------------------------------
def main():
    init_db()
    print(f"1. Extracting grammar tips from OCR cache (HSK level {HSK_LEVEL})...")
    unit_tips = parse_grammar_tips()

    if not unit_tips:
        print("No grammar tips extracted. Exiting.")
        return

    print("2. Loading SOPs...")
    if not GRAMMAR_TIP_SOP.exists():
        print(f" [error] Matching SOP file not found at {GRAMMAR_TIP_SOP}")
        return
    with open(GRAMMAR_TIP_SOP, 'r', encoding='utf-8') as f:
        sop_text = f.read()

    if not REFORMAT_GRAMMAR_TIP_SOP.exists():
        print(f" [error] Reformat SOP file not found at {REFORMAT_GRAMMAR_TIP_SOP}")
        return
    with open(REFORMAT_GRAMMAR_TIP_SOP, 'r', encoding='utf-8') as f:
        reformat_sop_text = f.read()

    print("3. Reformatting tips and matching to sentences via Claude...")
    with get_session() as db:
        for unit_str, tips in unit_tips.items():
            unit_number = int(unit_str)
            sentence_rows = get_sentences_for_unit(db, unit_number, hsk_level=HSK_LEVEL)
            if not sentence_rows:
                print(f" [warning] Unit {unit_str} (HSK{HSK_LEVEL}): no sentences in DB yet "
                      f"(run sentence_parser.py first). Skipping.")
                continue

            hanzi_list = [s.hanzi for s in sentence_rows]
            sentence_by_hanzi = {s.hanzi: s for s in sentence_rows}
            print(f"\nProcessing Unit {unit_str} ({len(tips)} tips, {len(hanzi_list)} sentences)...")

            for idx, raw_tip in enumerate(tips, 1):
                print(f"  -> Reformatting Tip {idx}...")
                structured_tip = reformat_grammar_tip_text(reformat_sop_text, raw_tip)
                if structured_tip is None:
                    continue

                tip_row = get_or_create_grammar_tip(db, unit_number, raw_tip, structured_tip,
                                                     hsk_level=HSK_LEVEL)

                print(f"  -> Matching Tip {idx}...")
                matched_hanzi = get_matching_sentences(sop_text, structured_tip, hanzi_list)

                if not matched_hanzi:
                    print("     No matches found.")
                    continue

                print(f"     Found {len(matched_hanzi)} matching sentence(s).")
                for matched_sentence in matched_hanzi:
                    sentence_row = sentence_by_hanzi.get(matched_sentence)
                    if sentence_row is None:
                        # agent hallucinated a sentence not in the list we gave it
                        continue
                    link_sentence_grammar(db, sentence_row.id, tip_row.id)

    print("\n✅ Done! Grammar tips and sentence links written directly to the DB.")


if __name__ == "__main__":
    main()