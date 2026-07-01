"""
Extracts sentences and fill-in-the-blank questions from each unit of the
HSK1 textbook PDF using two Claude calls per unit:

  1. OCR: reads the PDF pages → plain-text transcription
  2. Agent 3 (sentences + FITB): extracts sentences and generates
     fill-in-the-blank questions

Vocab, grammar, and proper nouns come from index_output.json (generated
by parse_index.py) — NOT extracted here. Agent 3 receives the unit's
vocab and grammar lists so it can:
  - Only include sentences where every word has been taught
  - Know exactly which grammar markers to blank in FITB questions

All Claude calls use prompt caching + temperature=0.
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

INDEX_FILEPATH = "../data/clean"
INDEX_FILENAME = "index_output.json"

OCR_SOP_FILEPATH = "../SOPs"
OCR_SOP_FILENAME = "ocr_transcription.txt"

AGENT3_SOP_FILEPATH = "../SOPs"
AGENT3_SOP_FILENAME = "sentences_parsing.txt"

OUTPUT_FILEPATH = "../data/clean"
OUTPUT_FILENAME = "units_output.json"

RAW_TEXT_OUTPUT_FILEPATH = "../data/raw/raw_transcriptions"

UNIT_STARTS = [34, 42, 50, 60, 68, 76, 84, 92, 102, 110, 118, 124, 132]
LAST_UNIT_END_PAGE = 139
FIRST_UNIT_NUMBER = 3

MODEL = "claude-sonnet-4-6"
OCR_MAX_TOKENS = 8192
AGENT_MAX_TOKENS = 8192
FILES_API_BETA = "files-api-2025-04-14"
TEMPERATURE = 0

TESTING = False
CLEANUP_UPLOADED_FILES = True

# module-level override from main.py
UNITS_TO_PROCESS = None

# --------------------------------- SETUP ---------------------------------

load_dotenv()
api_key = os.environ.get("CLAUDE_API_KEY")
if not api_key:
    raise ValueError("CLAUDE_API_KEY not found.")

print(f"Successfully loaded key starting with: {api_key[:8]}")
client = anthropic.Anthropic(api_key=api_key)

# --------------------------------- HELPERS ---------------------------------

def load_text_file(filepath, filename):
    with open(os.path.join(filepath, filename), "r", encoding="utf-8") as f:
        return f.read()


def load_index():
    path = os.path.join(INDEX_FILEPATH, INDEX_FILENAME)
    if not os.path.exists(path):
        raise FileNotFoundError(f"{path} not found — run parse_index.py first.")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_unit_ranges():
    ranges = []
    for i, start_page in enumerate(UNIT_STARTS):
        unit_number = FIRST_UNIT_NUMBER + i
        end_page = UNIT_STARTS[i + 1] - 1 if i + 1 < len(UNIT_STARTS) else LAST_UNIT_END_PAGE
        ranges.append((unit_number, start_page, end_page))
    if TESTING:
        ranges = ranges[:1]
    import sys
    units_filter = getattr(sys.modules[__name__], 'UNITS_TO_PROCESS', None)
    if units_filter:
        ranges = [(u, s, e) for u, s, e in ranges if u in units_filter]
    return ranges


def build_unit_pdf_bytes(doc, start_page, end_page):
    unit_doc = fitz.open()
    unit_doc.insert_pdf(doc, from_page=start_page - 1, to_page=end_page - 1)
    buffer = io.BytesIO()
    unit_doc.save(buffer)
    unit_doc.close()
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
        print(f"  JSON parse failed, saved to {debug_path}")
        raise


def call_ocr(sop, pdf_bytes, unit_number):
    file_upload = client.beta.files.upload(
        file=(f"unit_{unit_number}.pdf", io.BytesIO(pdf_bytes), "application/pdf")
    )
    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=OCR_MAX_TOKENS,
            betas=[FILES_API_BETA],
            system=[{"type": "text", "text": sop, "cache_control": {"type": "ephemeral"}}],
            messages=[{
                "role": "user",
                "content": [
                    {"type": "document", "source": {"type": "file", "file_id": file_upload.id}},
                    {"type": "text", "text": "Transcribe this unit's pages following the instructions."},
                ],
            }],
        )
    finally:
        if CLEANUP_UPLOADED_FILES:
            try:
                client.beta.files.delete(file_upload.id)
            except Exception as e:
                print(f"  (warning: failed to delete file: {e})")
    return response.content[0].text.strip()


def call_agent3(sop, transcription, unit_number, vocab_list, grammar_list):
    vocab_hanzi    = [v["hanzi"] for v in vocab_list]
    grammar_markers = [g["hanzi"] for g in grammar_list]

    prompt = f"""Unit {unit_number} transcription:
{transcription}

Vocab words taught in this unit (and all previous units):
{json.dumps(vocab_hanzi, ensure_ascii=False)}

Grammar markers for this unit (the ONLY words you may blank in fill-in-the-blank questions):
{json.dumps(grammar_markers, ensure_ascii=False)}"""

    response = client.messages.create(
        model=MODEL,
        max_tokens=AGENT_MAX_TOKENS,
        temperature=TEMPERATURE,
        system=[{"type": "text", "text": sop, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "max_tokens":
        raise RuntimeError(f"Agent 3 unit {unit_number} truncated — increase AGENT_MAX_TOKENS.")
    return parse_json_response(response.content[0].text, f"unit{unit_number}_sentences")


def save_raw_transcription(transcription, unit_number):
    os.makedirs(RAW_TEXT_OUTPUT_FILEPATH, exist_ok=True)
    with open(os.path.join(RAW_TEXT_OUTPUT_FILEPATH, f"unit_{unit_number}_raw.txt"), "w", encoding="utf-8") as f:
        f.write(transcription)


def save_output(data, filepath, filename):
    with open(os.path.join(filepath, filename), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# --------------------------------- MAIN ---------------------------------

def main():
    ocr_sop    = load_text_file(OCR_SOP_FILEPATH, OCR_SOP_FILENAME)
    agent3_sop = load_text_file(AGENT3_SOP_FILEPATH, AGENT3_SOP_FILENAME)
    index      = load_index()

    all_vocab   = index.get("vocab", [])
    all_grammar = index.get("grammar", [])

    doc = fitz.open(os.path.join(TEXTBOOK_FILEPATH, TEXTBOOK_FILENAME))
    unit_ranges = build_unit_ranges()

    if TESTING:
        print(f"TESTING mode — only unit {unit_ranges[0][0]}\n")

    # load existing output so partial reruns don't wipe other units
    output_path = os.path.join(OUTPUT_FILEPATH, OUTPUT_FILENAME)
    if os.path.exists(output_path):
        with open(output_path, "r", encoding="utf-8") as f:
            mega_dict = json.load(f)
    else:
        mega_dict = {}

    failed_units = []

    for unit_number, start_page, end_page in unit_ranges:
        print(f"\nUnit {unit_number}: pages {start_page}-{end_page}")
        pdf_bytes = build_unit_pdf_bytes(doc, start_page, end_page)

        # OCR
        try:
            transcription = call_ocr(ocr_sop, pdf_bytes, unit_number)
            save_raw_transcription(transcription, unit_number)
            print(f"  OCR ✓ ({len(transcription)} chars)")
        except Exception as e:
            print(f"  OCR FAILED: {e}")
            failed_units.append((unit_number, "ocr"))
            continue

        # filter index to words taught up to and including this unit
        unit_vocab   = [v for v in all_vocab   if v["unit"] <= unit_number]
        unit_grammar = [g for g in all_grammar if g["unit"] == unit_number]

        # agent 3: sentences + fill-in-the-blank
        try:
            result = call_agent3(agent3_sop, transcription, unit_number, unit_vocab, unit_grammar)
            sentences     = result.get("sentences", [])
            fitb          = result.get("fill_in_the_blank", [])
            print(f"  Agent 3 ✓ ({len(sentences)} sentences, {len(fitb)} fill-in-the-blank)")
        except Exception as e:
            print(f"  Agent 3 FAILED: {e}")
            failed_units.append((unit_number, "agent3"))
            sentences = []
            fitb      = []

        mega_dict[str(unit_number)] = {
            "sentences": sentences,
            "fill_in_the_blank": fitb,
        }
        save_output(mega_dict, OUTPUT_FILEPATH, OUTPUT_FILENAME)

    doc.close()

    succeeded = len(unit_ranges) - len(set(u for u, _ in failed_units))
    print(f"\nDone. {succeeded}/{len(unit_ranges)} units succeeded.")
    if failed_units:
        print(f"Failures: {failed_units}")
    print(f"Output: {output_path}")


if __name__ == "__main__":
    main()