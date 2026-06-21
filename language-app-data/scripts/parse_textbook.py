"""
Extracts vocab/grammar/sentences/fill-in-the-blank data from each unit of a
Mandarin textbook PDF using two separate Claude calls per unit:

  1. OCR step: uploads the unit's pages as a PDF and gets back a faithful
     plain-text transcription (Claude reads the pages directly, no local
     OCR needed).
  2. JSON step: takes that plain-text transcription (no PDF/vision needed
     here) and structures it into vocab/grammar/sentences/fill_in_the_blank.

Splitting these means a bad result can be traced to a specific step: if the
transcription is wrong, that's an OCR/reading issue; if the transcription
looks right but the JSON is wrong, that's a structuring/instruction issue.
Each unit's raw transcription is also saved to disk so it can be inspected
directly.

All units are compiled into a single JSON file keyed by unit number.
"""

import os
import io
import json
import fitz  # PyMuPDF
import anthropic
from dotenv import load_dotenv

# --------------------------------- CONSTANTS ---------------------------------

# Where the source textbook PDF lives
TEXTBOOK_FILEPATH = "../data/raw"
TEXTBOOK_FILENAME = "hsk1_textbook.pdf"

# Where the two SOP (system prompt instructions) text files live
OCR_SOP_FILEPATH = "../SOPs"
OCR_SOP_FILENAME = "ocr_transcription.txt"

JSON_SOP_FILEPATH = "../SOPs"
JSON_SOP_FILENAME = "textbook_parsing.txt"

# Where the compiled mega JSON should be written
OUTPUT_FILEPATH = "../data/clean"
OUTPUT_FILENAME = "units_output.json"

# Where each unit's raw OCR transcription gets saved, for inspection/debugging
RAW_TEXT_OUTPUT_FILEPATH = "../data/raw/raw_transcriptions"

# Page each unit starts on. Units 1-2 are skipped (pinyin pronunciation basics,
# doesn't follow the later unit format). Unit 3 is UNIT_STARTS[0], unit 4 is
# UNIT_STARTS[1], etc. Each unit runs from its start page to (next unit's start
# page - 1), except the last unit, which runs through LAST_UNIT_END_PAGE.
UNIT_STARTS = [34, 42, 50, 60, 68, 76, 84, 92, 102, 110, 118, 124, 132]
LAST_UNIT_END_PAGE = 139
FIRST_UNIT_NUMBER = 3

MODEL = "claude-sonnet-4-6"
OCR_MAX_TOKENS = 8192   # transcription of a full unit can be long
JSON_MAX_TOKENS = 8192
FILES_API_BETA = "files-api-2025-04-14"

# When True, only processes the first unit in the list. Flip to False once
# you've confirmed a single unit comes back correctly.
TESTING = False

# Deletes each unit's uploaded file from your Anthropic account after the OCR
# step, so you don't accumulate textbook pages in file storage.
CLEANUP_UPLOADED_FILES = True

# --------------------------------- SETUP ---------------------------------

load_dotenv()

api_key = os.environ.get("CLAUDE_API_KEY")
if not api_key:
    raise ValueError("API Key not found! Did you forget to set CLAUDE_API_KEY in the .env file?")

print(f"Successfully loaded key starting with: {api_key[:8]}")

client = anthropic.Anthropic(api_key=api_key)

# --------------------------------- HELPERS ---------------------------------

def load_text_file(filepath: str, filename: str) -> str:
    full_path = os.path.join(filepath, filename)
    with open(full_path, "r", encoding="utf-8") as f:
        return f.read()


def build_unit_ranges():
    """Computes (unit_number, start_page, end_page) for every unit."""
    ranges = []
    for i, start_page in enumerate(UNIT_STARTS):
        unit_number = FIRST_UNIT_NUMBER + i
        if i + 1 < len(UNIT_STARTS):
            end_page = UNIT_STARTS[i + 1] - 1
        else:
            end_page = LAST_UNIT_END_PAGE
        ranges.append((unit_number, start_page, end_page))

    if TESTING:
        ranges = ranges[:1]

    return ranges


def build_unit_pdf_bytes(doc: fitz.Document, start_page: int, end_page: int) -> bytes:
    """
    Builds a standalone mini-PDF containing only this unit's pages (1-indexed,
    inclusive) and returns it as raw bytes, ready to upload.
    """
    unit_doc = fitz.open()  # new empty PDF
    unit_doc.insert_pdf(doc, from_page=start_page - 1, to_page=end_page - 1)

    buffer = io.BytesIO()
    unit_doc.save(buffer)
    unit_doc.close()

    return buffer.getvalue()


def call_claude_for_ocr(ocr_system_prompt: str, pdf_bytes: bytes, unit_number: int) -> str:
    """Uploads a unit's pages and returns a plain-text transcription."""
    file_upload = client.beta.files.upload(
        file=(f"unit_{unit_number}.pdf", io.BytesIO(pdf_bytes), "application/pdf")
    )

    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=OCR_MAX_TOKENS,
            betas=[FILES_API_BETA],
            system=ocr_system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {"type": "file", "file_id": file_upload.id},
                        },
                        {
                            "type": "text",
                            "text": "Transcribe this unit's pages following the instructions.",
                        },
                    ],
                }
            ],
        )
    finally:
        if CLEANUP_UPLOADED_FILES:
            try:
                client.beta.files.delete(file_upload.id)
            except Exception as cleanup_err:
                print(f"  (warning: failed to delete uploaded file {file_upload.id}: {cleanup_err})")

    return response.content[0].text.strip()


def call_claude_for_json(json_system_prompt: str, transcription: str) -> dict:
    response = client.messages.create(
        model=MODEL,
        max_tokens=JSON_MAX_TOKENS,
        system=json_system_prompt,
        messages=[{"role": "user", "content": transcription}],
    )

    if response.stop_reason == "max_tokens":
        raise RuntimeError(
            "Response truncated by max_tokens — increase JSON_MAX_TOKENS and retry."
        )

    raw_text = response.content[0].text.strip()

    if raw_text.startswith("```"):
        raw_text = raw_text.strip("`")
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
        raw_text = raw_text.strip()

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        # Save what we got so you can inspect it instead of just seeing a traceback
        debug_path = "failed_json_raw.txt"
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(raw_text)
        print(f"  (raw response saved to {debug_path} for inspection)")
        raise


def save_raw_transcription(transcription: str, unit_number: int):
    os.makedirs(RAW_TEXT_OUTPUT_FILEPATH, exist_ok=True)
    full_path = os.path.join(RAW_TEXT_OUTPUT_FILEPATH, f"unit_{unit_number}_raw.txt")
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(transcription)


def save_mega_json(mega_dict: dict, filepath: str, filename: str):
    full_path = os.path.join(filepath, filename)
    with open(full_path, "w", encoding="utf-8") as f:
        json.dump(mega_dict, f, ensure_ascii=False, indent=2)


# --------------------------------- MAIN LOOP ---------------------------------

def main():
    ocr_sop_text = load_text_file(OCR_SOP_FILEPATH, OCR_SOP_FILENAME)
    json_sop_text = load_text_file(JSON_SOP_FILEPATH, JSON_SOP_FILENAME)

    textbook_full_path = os.path.join(TEXTBOOK_FILEPATH, TEXTBOOK_FILENAME)
    doc = fitz.open(textbook_full_path)

    unit_ranges = build_unit_ranges()
    if TESTING:
        print(f"TESTING mode is on -- only processing unit {unit_ranges[0][0]}\n")

    mega_dict = {}
    failed_units = []  # list of (unit_number, step) tuples

    for unit_number, start_page, end_page in unit_ranges:
        print(f"Unit {unit_number}: pages {start_page}-{end_page}")

        pdf_bytes = build_unit_pdf_bytes(doc, start_page, end_page)

        # --- Step 1: OCR / transcription ---
        try:
            transcription = call_claude_for_ocr(ocr_sop_text, pdf_bytes, unit_number)
            save_raw_transcription(transcription, unit_number)
            print(f"  -> OCR success ({len(transcription)} chars, saved to "
                  f"{RAW_TEXT_OUTPUT_FILEPATH}/unit_{unit_number}_raw.txt)")
        except Exception as e:
            print(f"  -> OCR step FAILED: {e}")
            failed_units.append((unit_number, "ocr"))
            save_mega_json(mega_dict, OUTPUT_FILEPATH, OUTPUT_FILENAME)
            continue

        # --- Step 2: JSON structuring ---
        try:
            unit_json = call_claude_for_json(json_sop_text, transcription)
            mega_dict[str(unit_number)] = unit_json
            print(f"  -> JSON structuring success")
        except json.JSONDecodeError as e:
            print(f"  -> JSON step FAILED to parse: {e}")
            failed_units.append((unit_number, "json"))
        except Exception as e:
            print(f"  -> JSON step FAILED: {e}")
            failed_units.append((unit_number, "json"))

        # Save progress after every unit, not just at the end, so a crash or
        # a failed unit partway through doesn't lose everything before it
        save_mega_json(mega_dict, OUTPUT_FILEPATH, OUTPUT_FILENAME)

    doc.close()

    print(f"\nDone. {len(mega_dict)}/{len(unit_ranges)} units succeeded.")
    if failed_units:
        print(f"Failed units (need rerunning): {failed_units}")
    print(f"Output written to {os.path.join(OUTPUT_FILEPATH, OUTPUT_FILENAME)}")


if __name__ == "__main__":
    main()