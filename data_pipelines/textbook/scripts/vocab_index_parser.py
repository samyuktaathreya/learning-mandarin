"""
Parses the textbook vocabulary index PDF into structured vocab data and
writes it DIRECTLY to the textbook SQL database (Unit + Vocab rows) --
no more index_output.json / hsk1_dictionary.json / word_to_pinyin.json /
word_to_unit.json intermediates. Those files' entire purpose was to hand
data to the next script or to a consumer that queried them by hanzi; both
of those are now just DB reads (see db.get_word_to_pinyin_map /
get_word_to_unit_map, used by sentence_parser.py).

Pipeline (unchanged from the JSON version):
  1. OCR the index PDF (cached in OCR_cache) -> markdown tables
  2. Extraction agent -> JSON entries {hanzi, pinyin, pos, english, unit, section}
  3. Code: classify type (vocab / grammar / proper_noun), convert diacritic
     pinyin to numeric, dedupe (first-seen wins, lowest unit)
  4. Merge in language-app-data/added_vocab/hsk1.txt -- hand-added entries
  5. Upsert every record into `vocab_senses` (+ implicitly `vocab`, `units`)
     via db.upsert_vocab_sense -- each distinct (hanzi, unit, english)
     listing in the printed index becomes its own taught SENSE rather than
     being collapsed to a single row per hanzi, so a word retaught later
     with a genuinely different meaning keeps both meanings on file instead
     of the earlier one silently overwriting the later one (or vice versa).

NOTE ON GRAMMAR CLASSIFICATION: unchanged -- see classify_type().
"""

import os
import base64
import re
import json
import datetime
import time
import anthropic
from dotenv import load_dotenv
from app.core.config.shared import ENV_FILE
from app.core.config.textbook import (
    TEXTBOOK_RAW_DIR,
    SOP_PATH,
    OCR_PATH,
)

from app.textbook.db_utils import get_session, init_db, upsert_vocab_sense
from app.textbook.models import WordType

# --------------------------------- CONSTANTS ---------------------------------

OCR_SOP_FILENAME = os.path.join("vocab", "ocr.txt")
EXTRACTOR_SOP_FILENAME = os.path.join("vocab", "index_extractor.txt")

# HSK level being processed this run. Raw PDFs are now split per level under
# .../data/raw/hsk_textbook_index/{hsk_level}.pdf (see main.py, which sets
# HSK_LEVEL as an env override the same way it already does for
# UNITS_TO_PROCESS / SOURCES_TO_PROCESS). Defaults to 1 to match prior behavior.
HSK_LEVEL = int(os.environ.get("HSK_LEVEL", "1"))

# Raw index PDFs now live one level deeper, split by HSK level:
#   .../data/raw/hsk_textbook_index/1.pdf, .../hsk_textbook_index/2.pdf, ...
INDEX_PDF_FILEPATH = TEXTBOOK_RAW_DIR / "hsk_textbook_index"
INDEX_PDF_FILENAME = f"{HSK_LEVEL}.pdf"

# OCR cache is likewise split by level so hsk2's index OCR doesn't clobber
# hsk1's cached markdown: .../OCR_cache/hsk_textbook_index/{hsk_level}.md
# .../OCR_cache/hsk_textbook_index/{level}/index.md
OCR_CACHE_FILEPATH = OCR_PATH / "hsk_textbook_index" / str(HSK_LEVEL)
OCR_CACHE_FILENAME = "index.md"
FORCE_OCR = False

# LLM raw-response dumps stay on disk for debugging -- these were never part
# of the app's data model, just crash forensics, so they're untouched.
from app.core.config.textbook import TEXTBOOK_INTERMEDIATE_DIR
LLM_RESPONSES_FILEPATH = TEXTBOOK_INTERMEDIATE_DIR / "LLM_RESPONSES"

MODEL = "claude-sonnet-4-6"
OCR_MAX_TOKENS = 8192
AGENT_MAX_TOKENS = 8192
TEMPERATURE = 0

GRAMMAR_POS_PREFIXES = ("part", "aux", "助")

# --------------------------------- SETUP ---------------------------------

load_dotenv(ENV_FILE)
api_key = os.environ.get("CLAUDE_API_KEY")
client = anthropic.Anthropic(api_key=api_key) if api_key else None


# --------------------------------- HELPERS (unchanged) ---------------------------------

def load_sop(filename: str) -> str:
    print(f"  [load_sop] reading {SOP_PATH / filename}")
    with open(SOP_PATH / filename, "r", encoding="utf-8") as f:
        content = f.read()
    print(f"  [load_sop] read {len(content)} chars")
    return content


def extract_text_from_response(response) -> str:
    return "".join(b.text for b in response.content if b.type == "text").strip()


def extract_json_block(text: str) -> str:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\[.*?\]|\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        return fence.group(1)
    for oc, cc in [("[", "]"), ("{", "}")]:
        start = text.find(oc)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == oc:
                depth += 1
            elif text[i] == cc:
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return text


def save_llm_response(call_name: str, raw_text: str) -> str:
    # .../LLM_RESPONSES/hsk_textbook_index/vocab_index_hsk{level}_{call_name}.txt
    # .../LLM_RESPONSES/hsk_textbook_index/{level}/{call_name}.txt
    responses_dir = LLM_RESPONSES_FILEPATH / "hsk_textbook_index" / str(HSK_LEVEL)
    os.makedirs(str(responses_dir), exist_ok=True)
    path = os.path.join(str(responses_dir), f"{call_name}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(raw_text)
    return path

# ------------------------- PINYIN: DIACRITIC -> NUMERIC (unchanged) -------------------------
from data_pipelines.textbook.scripts.vocab_pinyin_utils import diacritic_to_numeric  


# --------------------------------- AGENT CALLS (unchanged) ---------------------------------

def run_index_ocr() -> str:
    os.makedirs(str(OCR_CACHE_FILEPATH), exist_ok=True)
    cache_path = os.path.join(str(OCR_CACHE_FILEPATH), OCR_CACHE_FILENAME)
    if not FORCE_OCR and os.path.exists(cache_path):
        print(f"  [cache] using cached index OCR: {cache_path}")
        with open(cache_path, "r", encoding="utf-8") as f:
            return f.read()

    pdf_path = os.path.join(str(INDEX_PDF_FILEPATH), INDEX_PDF_FILENAME)
    if not os.path.exists(pdf_path):
        print(f"  [warning] index PDF not found at {pdf_path}; skipping OCR")
        return ""
    if client is None:
        print("  [warning] CLAUDE_API_KEY not configured; skipping OCR")
        return ""

    with open(pdf_path, "rb") as f:
        pdf_b64 = base64.standard_b64encode(f.read()).decode("utf-8")

    response = client.messages.create(
        model=MODEL,
        max_tokens=OCR_MAX_TOKENS,
        temperature=TEMPERATURE,
        system=load_sop(OCR_SOP_FILENAME),
        messages=[{
            "role": "user",
            "content": [
                {"type": "document",
                 "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64}},
                {"type": "text", "text": "Transcribe this vocabulary index per the SOP."},
            ],
        }],
    )
    ocr_md = extract_text_from_response(response)
    with open(cache_path, "w", encoding="utf-8") as f:
        f.write(ocr_md)
    return ocr_md


def run_extractor(ocr_markdown: str) -> list:
    if client is None:
        raise RuntimeError("CLAUDE_API_KEY not set — extractor cannot run")
    if not ocr_markdown:
        raise RuntimeError("OCR markdown is empty — nothing to extract from")

    print(f"  [extractor] OCR markdown length: {len(ocr_markdown)} chars")
    print(f"  [extractor] sending request to {MODEL}...")
    t0 = time.time()

    response = client.messages.create(
        model=MODEL,
        max_tokens=AGENT_MAX_TOKENS,
        temperature=TEMPERATURE,
        system=load_sop(EXTRACTOR_SOP_FILENAME),
        messages=[{"role": "user",
                   "content": f"Here is the OCR result of the vocabulary index:\n\n{ocr_markdown}"}],
    )
    print(f"  [extractor] response received after {time.time() - t0:.1f}s")
    print(f"  [extractor] stop_reason: {response.stop_reason}, "
          f"output_tokens: {response.usage.output_tokens}")

    raw = extract_text_from_response(response)
    print(f"  [extractor] extracted text length: {len(raw)} chars")

    saved = save_llm_response("extractor", raw)
    try:
        parsed = json.loads(extract_json_block(raw))
        print(f"  [extractor] parsed {len(parsed)} JSON entries")
        return parsed
    except json.JSONDecodeError as e:
        raise ValueError(f"Index extractor JSON parse failed ({e}); raw saved to {saved}")

# --------------------------------- PROCESSING ---------------------------------

def classify_type(entry: dict) -> WordType:
    if entry.get("section") == "proper_noun":
        return WordType.proper_noun
    pos = (entry.get("pos") or "").strip().lower()
    if pos.startswith(GRAMMAR_POS_PREFIXES):
        return WordType.grammar
    return WordType.vocab


_GLOSS_NORM_RE = re.compile(r"[^\w]+")


def _normalize_gloss(english: str) -> str:
    """Loose normalization for comparing two glosses -- punctuation/case/
    whitespace differences shouldn't count as "a different meaning", only a
    substantively different english string should."""
    return _GLOSS_NORM_RE.sub(" ", (english or "").strip().lower()).strip()


def process_entries(raw_entries: list) -> list[dict]:
    """Classify + convert pinyin, and decide which raw index rows become
    separate SENSES vs. which are just the same meaning re-listed at a
    later unit."""
    by_hanzi: dict[str, list[dict]] = {}
    skipped = []
    for entry in raw_entries:
        hanzi = (entry.get("hanzi") or "").strip()
        pinyin_raw = (entry.get("pinyin") or "").strip()
        if not hanzi or "[unclear]" in hanzi or "[unclear]" in pinyin_raw:
            skipped.append(entry)
            continue
        try:
            unit = int(entry.get("unit"))
        except (TypeError, ValueError):
            skipped.append(entry)
            continue
        record = {
            "hanzi": hanzi,
            "pinyin": diacritic_to_numeric(pinyin_raw),
            "english": (entry.get("english") or "").strip(),
            "unit": unit,
            "type": classify_type(entry),
        }

        existing_records = by_hanzi.setdefault(hanzi, [])
        norm = _normalize_gloss(record["english"])
        dup = next((r for r in existing_records if _normalize_gloss(r["english"]) == norm), None)
        if dup is not None:
            if unit < dup["unit"]:
                dup["unit"] = unit
            if record["pinyin"] and not dup["pinyin"]:
                dup["pinyin"] = record["pinyin"]
            continue

        existing_records.append(record)

    if skipped:
        print(f"  [warning] skipped {len(skipped)} unusable index row(s) (unclear/invalid unit):")
        for e in skipped:
            print(f"    - {e}")

    records = [r for recs in by_hanzi.values() for r in recs]
    records.sort(key=lambda r: r["unit"])
    return records


# --------------------------------- DEBUG JSON WRITER ---------------------------------

def write_debug_json(records: list, error_msg: str):
    """Writes a parsed snapshot to ../debug/vocab_index_parser/ for debugging purposes."""
    now = datetime.datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")
    datetime_file_str = now.strftime("%Y%m%d_%H%M%S")
    
    level_index = {}
    for r in records:
        hanzi = r["hanzi"]
        if hanzi not in level_index:
            level_index[hanzi] = []
            
        # Serialize enums nicely for JSON
        word_type = r["type"].name if hasattr(r["type"], "name") else str(r["type"])
        
        level_index[hanzi].append({
            "pinyin": r["pinyin"],
            "english": r["english"],
            "unit": r["unit"],
            "type": word_type
        })
        
    debug_data = {
        "run_info": {
            "hsk_levels_ran_for": [HSK_LEVEL],
            "date_of_run": date_str,
            "time_of_run": time_str,
            "error_msg": error_msg
        },
        "index": {
            str(HSK_LEVEL): level_index
        }
    }
    
    # Path: ../debug/vocab_index_parser/ relative to this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.abspath(os.path.join(script_dir, "..", "debug", "vocab_index_parser"))
    os.makedirs(out_dir, exist_ok=True)
    
    out_file = os.path.join(out_dir, f"{datetime_file_str}.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(debug_data, f, ensure_ascii=False, indent=4)
        
    print(f"  [debug] Wrote runtime debug info to {out_file}")


# --------------------------------- MAIN EXECUTION ---------------------------------

def main():
    start_time = time.time()
    records = []
    error_msg = ""
    
    try:
        init_db()
        print(f"Parsing vocabulary index (HSK level {HSK_LEVEL})...")
        ocr_md = run_index_ocr()
        raw_entries = run_extractor(ocr_md)

        if not raw_entries:
            print("  [warning] no raw entries extracted and no added vocab found; nothing to write")
            return

        print(f"  extracted {len(raw_entries)} raw rows from index")
        records = process_entries(raw_entries)

        with get_session() as db:
            counts = {WordType.vocab: 0, WordType.grammar: 0, WordType.proper_noun: 0}
            senses_created = 0
            for r in records:
                sense = upsert_vocab_sense(
                    db,
                    hanzi=r["hanzi"],
                    pinyin=r["pinyin"],
                    english=r["english"],
                    unit_number=r["unit"],
                    word_type=r["type"],
                    hsk_level=HSK_LEVEL,
                )
                senses_created += 1
                counts[r["type"]] += 1

        print(f"  vocab: {counts[WordType.vocab]}, grammar: {counts[WordType.grammar]}, "
              f"proper_nouns: {counts[WordType.proper_noun]}")
        print(f"Done. Wrote {senses_created} sense record(s) directly to the textbook DB "
              f"(HSK level {HSK_LEVEL}).")
              
    except Exception as e:
        error_msg = str(e)
        print(f"  [error] Exception during run: {error_msg}")
        raise
        
    finally:
        # Guarantee the debug JSON is written even if an error is thrown
        write_debug_json(records, error_msg)
        
        # Calculate and print total execution time
        elapsed_time = time.time() - start_time
        print(f"  [info] Total execution time: {elapsed_time:.2f} seconds.")


if __name__ == "__main__":
    main()