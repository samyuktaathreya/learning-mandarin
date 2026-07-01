"""
Reads the vocabulary index pages from the HSK1 textbook PDF and extracts
all vocab, grammar markers, and proper nouns into index_output.json.

This replaces agents 1 and 2 from parse_textbook.py. The index is a
single structured table — much more reliable than extracting vocab
from individual unit pages.

Run this once (or when you get a new textbook). Output is used by:
  - parse_textbook.py (to pass unit vocab to agent 3)
  - create_questions.py (to generate vocab/grammar questions)
  - build_dictionary.py (to build the lookup dictionary)
"""

import os
import io
import json
import fitz
import anthropic
from dotenv import load_dotenv

# --------------------------------- CONSTANTS ---------------------------------

TEXTBOOK_FILEPATH = "../data/raw"
TEXTBOOK_FILENAME = "hsk1_textbook.pdf"

# page range of the index (1-indexed, inclusive) — update if your textbook differs
INDEX_START_PAGE = 140
INDEX_END_PAGE   = 145  # adjust to cover all index pages

SOP_FILEPATH = "../SOPs"
SOP_FILENAME = "index_parsing.txt"

OUTPUT_FILEPATH = "../data/clean"
OUTPUT_FILENAME = "index_output.json"

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 16000
FILES_API_BETA = "files-api-2025-04-14"
TEMPERATURE = 0

CLEANUP_UPLOADED_FILES = True

MIN_UNIT = 3

# --------------------------------- SETUP ---------------------------------

load_dotenv()
api_key = os.environ.get("CLAUDE_API_KEY")
if not api_key:
    raise ValueError("CLAUDE_API_KEY not found.")

client = anthropic.Anthropic(api_key=api_key)


# --------------------------------- HELPERS ---------------------------------

def load_sop(filepath, filename):
    with open(os.path.join(filepath, filename), "r", encoding="utf-8") as f:
        return f.read()


def extract_index_pdf_bytes(textbook_path, start_page, end_page):
    doc = fitz.open(textbook_path)
    index_doc = fitz.open()
    index_doc.insert_pdf(doc, from_page=start_page - 1, to_page=end_page - 1)
    buffer = io.BytesIO()
    index_doc.save(buffer)
    index_doc.close()
    doc.close()
    return buffer.getvalue()


def parse_json_response(raw, debug_label):
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        debug_path = f"failed_{debug_label}.txt"
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(raw)
        print(f"JSON parse failed, saved to {debug_path}")
        raise


def save_output(data, filepath, filename):
    path = os.path.join(filepath, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


# --------------------------------- MAIN ---------------------------------

def main():
    sop = load_sop(SOP_FILEPATH, SOP_FILENAME)
    textbook_path = os.path.join(TEXTBOOK_FILEPATH, TEXTBOOK_FILENAME)

    print(f"Extracting index pages {INDEX_START_PAGE}-{INDEX_END_PAGE}...")
    pdf_bytes = extract_index_pdf_bytes(textbook_path, INDEX_START_PAGE, INDEX_END_PAGE)

    print("Uploading to Claude...")
    file_upload = client.beta.files.upload(
        file=("hsk1_index.pdf", io.BytesIO(pdf_bytes), "application/pdf")
    )

    try:
        print("Calling Claude (temperature=0)...")
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
            betas=[FILES_API_BETA],
            system=[{"type": "text", "text": sop, "cache_control": {"type": "ephemeral"}}],
            messages=[{
                "role": "user",
                "content": [
                    {"type": "document", "source": {"type": "file", "file_id": file_upload.id}},
                    {"type": "text", "text": "Extract all entries from both tables in this vocabulary index following the instructions."},
                ],
            }],
        )
    finally:
        if CLEANUP_UPLOADED_FILES:
            try:
                client.beta.files.delete(file_upload.id)
            except Exception as e:
                print(f"(warning: failed to delete uploaded file: {e})")

    if response.stop_reason == "max_tokens":
        raise RuntimeError("Response truncated — increase MAX_TOKENS.")

    result = parse_json_response(response.content[0].text, "index")

    vocab        = result.get("vocab", [])
    grammar      = result.get("grammar", [])
    proper_nouns = result.get("proper_nouns", [])

    for section in ["vocab", "grammar", "proper_nouns"]:
        for item in result.get(section, []):
            if item.get("unit", MIN_UNIT) < MIN_UNIT:
                item["unit"] = MIN_UNIT

    print(f"\nExtracted:")
    print(f"  {len(vocab)} vocab words")
    print(f"  {len(grammar)} grammar markers")
    print(f"  {len(proper_nouns)} proper nouns")

    path = save_output(result, OUTPUT_FILEPATH, OUTPUT_FILENAME)
    print(f"\nWritten to {path}")


if __name__ == "__main__":
    main()