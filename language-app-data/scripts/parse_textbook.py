"""
Extracts vocab/grammar/sentences/fill-in-the-blank data from each unit of a
Mandarin textbook PDF by calling the Claude API once per unit, then compiles
all units into a single JSON file keyed by unit number.
"""

import os
import json
import fitz  # PyMuPDF
import anthropic
from dotenv import load_dotenv

# --------------------------------- CONSTANTS ---------------------------------

# Where the source textbook PDF lives
TEXTBOOK_FILEPATH = "../data/raw"
TEXTBOOK_FILENAME = "hsk1_textbook.pdf"

# Where the SOP (system prompt instructions) text file lives
SOP_FILEPATH = "../SOPs"
SOP_FILENAME = "textbook_parsing.txt"

# Where the compiled mega JSON should be written
OUTPUT_FILEPATH = "../data/intermediate"
OUTPUT_FILENAME = "units_llm_output.json"

# Page each unit starts on. Units 1-2 are skipped (pinyin pronunciation basics,
# doesn't follow the later unit format). Unit 3 is UNIT_STARTS[0], unit 4 is
# UNIT_STARTS[1], etc. Each unit runs from its start page to (next unit's start
# page - 1), except the last unit, which runs through LAST_UNIT_END_PAGE.
UNIT_STARTS = [34, 42, 50, 60, 68, 76, 84, 92, 102, 110, 118, 124, 132]
LAST_UNIT_END_PAGE = 139
FIRST_UNIT_NUMBER = 3

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4096

# --------------------------------- SETUP ---------------------------------

load_dotenv()

api_key = os.environ.get("CLAUDE_API_KEY")
if not api_key:
    raise ValueError("API Key not found! Did you forget to set ANTHROPIC_API_KEY in the .env file?")

print(f"Successfully loaded key starting with: {api_key[:8]}")

client = anthropic.Anthropic(api_key=api_key)

# --------------------------------- HELPERS ---------------------------------

def load_sop(filepath: str, filename: str) -> str:
    full_path = os.path.join(filepath, filename)
    with open(full_path, "r", encoding="utf-8") as f:
        return f.read()


def extract_unit_text(doc: fitz.Document, start_page: int, end_page: int) -> str:
    """Extracts raw text for a 1-indexed, inclusive page range."""
    text_parts = []
    for page_num in range(start_page, end_page + 1):
        page = doc[page_num - 1]  # fitz pages are 0-indexed
        text_parts.append(page.get_text("text"))
    return "\n".join(text_parts)


def call_claude_for_unit(system_prompt: str, unit_text: str) -> dict:
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": unit_text}],
    )

    raw_text = response.content[0].text.strip()

    # Defensive cleanup in case the model wraps the JSON in a markdown fence
    # despite being told not to
    if raw_text.startswith("```"):
        raw_text = raw_text.strip("`")
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
        raw_text = raw_text.strip()

    return json.loads(raw_text)


def save_mega_json(mega_dict: dict, filepath: str, filename: str):
    full_path = os.path.join(filepath, filename)
    with open(full_path, "w", encoding="utf-8") as f:
        json.dump(mega_dict, f, ensure_ascii=False, indent=2)


# --------------------------------- MAIN LOOP ---------------------------------

def main():
    sop_text = load_sop(SOP_FILEPATH, SOP_FILENAME)

    textbook_full_path = os.path.join(TEXTBOOK_FILEPATH, TEXTBOOK_FILENAME)
    doc = fitz.open(textbook_full_path)

    mega_dict = {}
    failed_units = []

    for i, start_page in enumerate(UNIT_STARTS):
        unit_number = FIRST_UNIT_NUMBER + i

        # End page is one before the next unit's start page, or
        # LAST_UNIT_END_PAGE if this is the last unit in the list
        if i + 1 < len(UNIT_STARTS):
            end_page = UNIT_STARTS[i + 1] - 1
        else:
            end_page = LAST_UNIT_END_PAGE

        print(f"Unit {unit_number}: pages {start_page}-{end_page}")

        unit_text = extract_unit_text(doc, start_page, end_page)

        try:
            unit_json = call_claude_for_unit(sop_text, unit_text)
            mega_dict[str(unit_number)] = unit_json
            print(f"  -> success")
        except json.JSONDecodeError as e:
            print(f"  -> FAILED to parse JSON: {e}")
            failed_units.append(unit_number)
        except Exception as e:
            print(f"  -> FAILED: {e}")
            failed_units.append(unit_number)

        # Save progress after every unit, not just at the end, so a crash or
        # a failed unit partway through doesn't lose everything before it
        save_mega_json(mega_dict, OUTPUT_FILEPATH, OUTPUT_FILENAME)

    doc.close()

    print(f"\nDone. {len(mega_dict)}/{len(UNIT_STARTS)} units succeeded.")
    if failed_units:
        print(f"Failed units (need rerunning): {failed_units}")
    print(f"Output written to {os.path.join(OUTPUT_FILEPATH, OUTPUT_FILENAME)}")


if __name__ == "__main__":
    main()
