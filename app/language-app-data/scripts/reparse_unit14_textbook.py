"""
Reruns extraction for a single unit and merges the result into the existing
compiled JSON output, instead of rerunning the whole textbook.
"""

import os
import json
import fitz

from parse_textbook import (
    TEXTBOOK_FILEPATH, TEXTBOOK_FILENAME,
    OCR_SOP_FILEPATH, OCR_SOP_FILENAME,
    JSON_SOP_FILEPATH, JSON_SOP_FILENAME,
    OUTPUT_FILEPATH, OUTPUT_FILENAME,
    UNIT_STARTS, LAST_UNIT_END_PAGE, FIRST_UNIT_NUMBER,
    load_text_file, build_unit_pdf_bytes, call_claude_for_ocr,
    call_claude_for_json, save_raw_transcription,
)

UNIT_TO_RERUN = 14


def get_unit_page_range(unit_number: int):
    i = unit_number - FIRST_UNIT_NUMBER
    start_page = UNIT_STARTS[i]
    if i + 1 < len(UNIT_STARTS):
        end_page = UNIT_STARTS[i + 1] - 1
    else:
        end_page = LAST_UNIT_END_PAGE
    return start_page, end_page


def main():
    ocr_sop_text = load_text_file(OCR_SOP_FILEPATH, OCR_SOP_FILENAME)
    json_sop_text = load_text_file(JSON_SOP_FILEPATH, JSON_SOP_FILENAME)

    textbook_full_path = os.path.join(TEXTBOOK_FILEPATH, TEXTBOOK_FILENAME)
    doc = fitz.open(textbook_full_path)

    start_page, end_page = get_unit_page_range(UNIT_TO_RERUN)
    print(f"Unit {UNIT_TO_RERUN}: pages {start_page}-{end_page}")

    pdf_bytes = build_unit_pdf_bytes(doc, start_page, end_page)
    doc.close()

    transcription = call_claude_for_ocr(ocr_sop_text, pdf_bytes, UNIT_TO_RERUN)
    save_raw_transcription(transcription, UNIT_TO_RERUN)
    print(f"  -> OCR success ({len(transcription)} chars)")

    unit_json = call_claude_for_json(json_sop_text, transcription)
    print("  -> JSON structuring success")

    # Load existing compiled output and merge in just this unit, instead of
    # blowing away the units that already succeeded
    output_path = os.path.join(OUTPUT_FILEPATH, OUTPUT_FILENAME)
    with open(output_path, "r", encoding="utf-8") as f:
        mega_dict = json.load(f)

    mega_dict[str(UNIT_TO_RERUN)] = unit_json

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(mega_dict, f, ensure_ascii=False, indent=2)

    print(f"Unit {UNIT_TO_RERUN} merged into {output_path}")


if __name__ == "__main__":
    main()