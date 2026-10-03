"""
Extracts SENTENCES ONLY from the textbook AND workbook, per unit, and writes
them DIRECTLY to the textbook SQL database as BARE Sentence rows
(db_utils.upsert_sentence_bare).

SPLIT: this script used to also extract/solve/write FITB (fill-in-the-blank)
exercises. That responsibility has been moved OUT to fitb_parser.py, a
separate sibling script run right after this one in the pipeline.
Rationale: FITB entries are matched back to a Sentence row by content
(cjk_only(full_sentence) -> Sentence.hanzi), so FITB extraction should run
strictly after bare sentences exist in the DB for the unit, and keeping the
two concerns in one file made a pipeline stage do two structurally different
jobs (write Sentence rows vs. write FitbQuestion rows linked to them). Now:

  sentence_parser.py -> bare Sentence rows only
  fitb_parser.py     -> bare FitbQuestion rows only (reads existing
                         Sentence rows from the DB to resolve sentence_id;
                         does NOT depend on being in the same process/run
                         as sentence_parser.py, only on it having already
                         committed)

REWRITE (carried over from before the split): this script does no word
segmentation or vocab-gating. Tagging is tag_sentences.py's job (pipeline
stage run right after fitb_parser.py), using HanLP for segmentation and
AI-assisted sense resolution instead of a known-words allow-list. This
script's ONLY responsibility is: OCR the unit, extract candidate sentences
via the LLM, verify they're verbatim (not hallucinated), normalize literal
digit runs into hanzi, and write bare Sentence rows. No vocab gate means no
sentence is ever REJECTED here for using "unknown" vocab -- an unfamiliar
word in a real textbook sentence is exactly the situation tag_sentences.py
is built to handle (register it, evidenced by this sentence), not something
to silently drop.

Sentence.pinyin is left BLANK by this script ("") -- it's populated later
by tag_sentences.py once tags are resolved and each tag's actual reading is
known (see tag_sentences.tag_sentence, which joins each resolved tag's
pinyin into the sentence's pinyin field after tagging).
"""

import os
import io
import json
import base64
import re
import datetime
import argparse

import anthropic
from pypdf import PdfReader, PdfWriter
from app.core.config.shared import settings
from app.core.config.textbook import (
    TEXTBOOK_RAW_DIR,
    TEXTBOOK_INTERMEDIATE_DIR,
    SOP_PATH,
    OCR_PATH,
)
from app.textbook.db_utils import get_session, init_db, upsert_sentence_bare

# --------------------------------- CONSTANTS (unchanged) ---------------------------------
SENTENCE_PARSER_SOP_FILEPATH = SOP_PATH / "sentence_parser"

SENTENCE_FINDER_FILENAME = SENTENCE_PARSER_SOP_FILEPATH / "sentence_finder.txt"
FIX_SENTENCES_SOP_FILEPATH = SENTENCE_PARSER_SOP_FILEPATH / "fix_sentences.txt"


# NOTE: UNIT_STARTS/LAST_UNIT_END_PAGE/FIRST_UNIT_NUMBER below are HSK1's
# page layout. Once HSK2+ PDFs are loaded, these will almost certainly
# differ per level (different books, different page counts) -- this config
# will need to become per-hsk-level (e.g. a dict keyed by HSK_LEVEL) rather
# than a single flat literal per source. Left as-is structurally for now
# since only the file *paths* are known to have changed; flagging this so
# it isn't missed when HSK2 data actually gets loaded.
SOURCES = {
    "textbook": {
        # Raw PDFs are now split per level: .../data/raw/hsk_textbook/{level}.pdf
        "RAW_SUBDIR": "hsk_textbook",
        "OCR_SOP_FILENAME": SOP_PATH / "sentence_parser" / "ocr.txt",
        "UNIT_STARTS": [34, 42, 50, 60, 68, 76, 84, 92, 102, 110, 118, 124, 132],
        "LAST_UNIT_END_PAGE": 139,
        "FIRST_UNIT_NUMBER": 3,
    },
    "workbook": {
        # .../data/raw/hsk_workbook/{level}.pdf
        "RAW_SUBDIR": "hsk_workbook",
        "OCR_SOP_FILENAME": os.path.join("workbook_parser", "ocr.txt"),
        "UNIT_STARTS": [15, 23, 31, 39, 47, 55, 63, 71, 87, 96, 105, 113],
        "LAST_UNIT_END_PAGE": 120,
        "FIRST_UNIT_NUMBER": 4,
    },
}

OCR_CACHE_FILEPATH = OCR_PATH
FORCE_OCR = False

LLM_RESPONSES_FILEPATH = TEXTBOOK_INTERMEDIATE_DIR / "LLM_RESPONSES"

MODEL = "claude-sonnet-4-6"
HAIKU_MODEL = "claude-haiku-4-5"
OCR_MAX_TOKENS = 8192
AGENT_MAX_TOKENS = 8192
TEMPERATURE = 0

# module-level overrides from main.py
UNITS_TO_PROCESS = []
SOURCES_TO_PROCESS = None
# HSK level being processed this run (main.py passes this the same way it
# already passes UNITS_TO_PROCESS / SOURCES_TO_PROCESS -- via env override).
HSK_LEVEL = int(os.environ.get("HSK_LEVEL", "1"))

# --------------------------------- SETUP ---------------------------------

api_key = settings.CLAUDE_API_KEY
client = anthropic.Anthropic(api_key=api_key) if api_key else None

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


# --------------------------------- HELPERS (unchanged) ---------------------------------

def load_sop(filename: str) -> str:
    with open(SOP_PATH / filename, "r", encoding="utf-8") as f:
        return f.read()


def get_unit_page_ranges(source_cfg: dict):
    starts = source_cfg["UNIT_STARTS"]
    ranges = []
    for i, start in enumerate(starts):
        unit_number = source_cfg["FIRST_UNIT_NUMBER"] + i
        end = starts[i + 1] - 1 if i + 1 < len(starts) else source_cfg["LAST_UNIT_END_PAGE"]
        ranges.append((unit_number, start, end))
    return ranges


def split_unit_to_pdf_bytes(reader: PdfReader, start_page: int, end_page: int) -> bytes:
    writer = PdfWriter()
    for page_num in range(start_page - 1, end_page):
        writer.add_page(reader.pages[page_num])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


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


def save_llm_response(source: str, unit_number: int, call_name: str, raw_text: str) -> str:
    responses_dir = LLM_RESPONSES_FILEPATH / f"hsk_{source}" / str(HSK_LEVEL)
    os.makedirs(str(responses_dir), exist_ok=True)
    path = os.path.join(str(responses_dir), f"unit{unit_number}_{call_name}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(raw_text)
    return path


def parse_json_response(raw_text: str, fallback, source: str, unit_number: int, call_name: str):
    saved = save_llm_response(source, unit_number, call_name, raw_text)
    try:
        return json.loads(extract_json_block(raw_text))
    except json.JSONDecodeError as e:
        print(f"  [warning] {call_name} JSON parse failed ({source} unit {unit_number}): {e}")
        print(f"  [warning] raw response saved to: {saved}")
        return fallback


def cjk_only(s: str) -> str:
    return "".join(_CJK_RE.findall(s))


_CONTENT_RE = re.compile(r"[一-鿿]|\d+")


def content_only(s: str) -> str:
    return "".join(_CONTENT_RE.findall(s))


def remove_parentheses(text: str) -> str:
    """Removes full-width and half-width parentheses from a string."""
    if not text:
        return text
    return re.sub(r'[\uff08\uff09\(\)]', '', text)


# --------------------------------- VALIDATION (unchanged) ---------------------------------

_PUNCTUATION_EQUIVALENTS = {
    "\uff0c": ",", "\u3002": ".", "\uff1f": "?", "\uff01": "!", "\uff1a": ":", "\uff1b": ";",
    "\uff08": "(", "\uff09": ")", "\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'",
    "\u3000": " ",
}

_PAREN_ANNOTATION_RE = re.compile(r"[\uff08(][^\uff09)]*[\uff09)]")
_LATIN_RUN_RE = re.compile(r"[A-Za-z0-9]+\s*[:\uff1a]?")


def normalize_for_match(s: str) -> str:
    s = _PAREN_ANNOTATION_RE.sub("", s)
    s = _LATIN_RUN_RE.sub("", s)
    s = s.replace("|", "")
    for full, half in _PUNCTUATION_EQUIVALENTS.items():
        s = s.replace(full, half)
    return re.sub(r"[ \t]+", "", s)


def filter_verbatim_sentences(sentences: dict, ocr_markdown: str, label: str):
    lines = [normalize_for_match(line) for line in ocr_markdown.split("\n")]
    verified, dropped = {}, []
    for zh, en in sentences.items():
        if any(normalize_for_match(zh) in line for line in lines):
            verified[zh] = en
        else:
            dropped.append(zh)
    if dropped:
        print(f"  [verbatim] {label}: dropped {len(dropped)} non-verbatim sentence(s):")
        for zh in dropped:
            print(f"    - {zh}")
    return verified, len(dropped)


# --------------------------------- HALLUCINATION PRE-FILTER (programmatic) ---------------------------------

# Ellipses ("…" or "..") and blank placeholders ("_") in either the hanzi or
# english attribute are telltale signs of a truncated/garbled OCR->LLM
# extraction rather than a real sentence.
_ELLIPSIS_RE = re.compile(r"\.\.|\u2026")
_UNDERSCORE_BLANK_RE = re.compile(r"_")

# Multiple-choice stubs like "(A)", "(B)", "(C)" (half- or full-width
# parens) leaking through from workbook exercise text, not real sentences.
_MULTIPLE_CHOICE_RE = re.compile(r"[\uff08(]\s*[A-Za-z]\s*[\uff09)]")


def filter_hallucination_candidates(sentences: dict, label: str = "") -> tuple:
    """Programmatic (non-LLM) pre-filter for sentence candidates that are
    almost certainly hallucinated/garbage rather than real textbook
    sentences:
      - hanzi attribute is only a single hanzi character (or empty)
      - hanzi or english attribute contains an ellipsis ("..." / "…") or a
        blank placeholder ("_")
      - hanzi or english attribute contains multiple-choice markers like
        "(A)", "(B)", "(C)"
    Runs BEFORE the sentences are sent to Haiku for the fix-up pass, so
    Haiku never has to waste a call on something this cheap to catch.
    """
    kept, dropped = {}, []
    for zh, en in sentences.items():
        en = en or ""
        reason = None
        if len(cjk_only(zh)) <= 1:
            reason = "single hanzi character"
        elif _ELLIPSIS_RE.search(zh) or _ELLIPSIS_RE.search(en):
            reason = "ellipsis"
        elif _UNDERSCORE_BLANK_RE.search(zh) or _UNDERSCORE_BLANK_RE.search(en):
            reason = "blank placeholder"
        elif _MULTIPLE_CHOICE_RE.search(zh) or _MULTIPLE_CHOICE_RE.search(en):
            reason = "multiple-choice marker"

        if reason:
            dropped.append((zh, reason))
        else:
            kept[zh] = en
    if dropped:
        print(f"  [hallucination-filter] {label}: dropped {len(dropped)} sentence(s):")
        for zh, reason in dropped:
            print(f"    - ({reason}) {zh}")
    return kept, len(dropped)


# --------------------------------- NUMBER NORMALIZATION ---------------------------------

_DIGIT_HANZI = "\u96f6\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d"
_DIGIT_RUN_RE = re.compile(r"\d+")

_MEASURE_WORDS = {
    "\u4e2a", "\u53e3", "\u5c81", "\u5757", "\u672c", "\u676f\u5b50",
    "\u5f20", "\u5206\u949f", "\u70b9", "\u4e9b", "\u5e74",
}


def _number_to_hanzi(n: int) -> str:
    if n == 0:
        return "\u96f6"
    digits = str(n)
    length = len(digits)
    units = ["", "\u5341", "\u767e", "\u5343"]
    parts = []
    for i, ch in enumerate(digits):
        d = int(ch)
        power = length - i - 1
        if d == 0:
            if power != 0 and any(c != "0" for c in digits[i + 1:]):
                parts.append("\u96f6")
            continue
        if d == 1 and power == 1 and i == 0 and length == 2:
            parts.append(units[power])
        else:
            parts.append(_DIGIT_HANZI[d] + units[power])
    return "".join(parts)


def digit_run_to_hanzi(run: str) -> str:
    if len(run) >= 4:
        return "".join(_DIGIT_HANZI[int(d)] for d in run)
    return _number_to_hanzi(int(run))


def normalize_number_text(text: str) -> str:
    def replace(m):
        run = m.group()
        hanzi = digit_run_to_hanzi(run)
        end = m.end()
        next_text = text[end:end + 3]
        if hanzi == "\u4e8c" and any(next_text.startswith(mw) for mw in _MEASURE_WORDS):
            return "\u4e24"
        return hanzi
    return _DIGIT_RUN_RE.sub(replace, text)


# --------------------------------- AGENT CALLS (unchanged) ---------------------------------

def run_ocr(pdf_bytes: bytes, ocr_sop: str, source: str, unit_number: int) -> str:
    cache_dir = OCR_CACHE_FILEPATH / f"hsk_{source}" / str(HSK_LEVEL)
    os.makedirs(str(cache_dir), exist_ok=True)
    cache_path = os.path.join(str(cache_dir), f"unit{unit_number}.md")
    if not FORCE_OCR and os.path.exists(cache_path):
        print(f"  [cache] using cached OCR: {cache_path}")
        with open(cache_path, "r", encoding="utf-8") as f:
            return f.read()

    if client is None:
        print("  [warning] CLAUDE_API_KEY not configured; skipping OCR")
        return ""

    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")
    response = client.messages.create(
        model=MODEL,
        max_tokens=OCR_MAX_TOKENS,
        temperature=TEMPERATURE,
        system=ocr_sop,
        messages=[{
            "role": "user",
            "content": [
                {"type": "document",
                 "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64}},
                {"type": "text", "text": "Transcribe this unit per the SOP."},
            ],
        }],
    )
    ocr_md = extract_text_from_response(response)
    with open(cache_path, "w", encoding="utf-8") as f:
        f.write(ocr_md)
    return ocr_md


def run_text_agent(ocr_markdown: str, sop: str, source: str, unit_number: int,
                    call_name: str, fallback, extra_content: str = ""):
    if client is None or not ocr_markdown:
        return fallback
    content = f"Here is the OCR result for this unit:\n\n{ocr_markdown}"
    if extra_content:
        content += f"\n\n{extra_content}"
    response = client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=AGENT_MAX_TOKENS,
        temperature=TEMPERATURE,
        system=sop,
        messages=[{"role": "user", "content": content}],
    )
    return parse_json_response(extract_text_from_response(response), fallback,
                                source, unit_number, call_name)


def run_fix_sentences_agent(sentences: dict, sop: str, source: str, unit_number: int,
                             hsk_level: int) -> dict:
    """Sends this unit's surviving sentences (whole unit, not one-by-one) to
    Haiku along with the fix_sentences SOP, so it can catch/correct any
    remaining hallucination issues the programmatic pre-filter can't (e.g.
    a mistranslated or mismatched english attribute) without another OCR
    pass. The SOP is prefixed with the HSK level of the material being
    processed, since hsk_level varies per-call depending on which
    unit/source is currently being handled (it is NOT a fixed constant --
    it's threaded through from HSK_LEVEL for whichever unit this call is
    for)."""
    if client is None or not sentences:
        return sentences

    system_prompt = f"This is HSK{hsk_level} material.\n\n{sop}"
    content = ("Here are this unit's candidate sentences (hanzi -> english) "
               "as a JSON object. Fix any that need it and return the full "
               "corrected JSON object in the same {hanzi: english} shape:\n\n"
               + json.dumps(sentences, ensure_ascii=False, indent=2))
    response = client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=AGENT_MAX_TOKENS,
        temperature=TEMPERATURE,
        system=system_prompt,
        messages=[{"role": "user", "content": content}],
    )
    fixed = parse_json_response(extract_text_from_response(response), sentences,
                                 source, unit_number, "fix_sentences")
    if not isinstance(fixed, dict):
        print(f"  [warning] fix_sentences ({source} unit {unit_number}) returned "
              f"non-dict output; keeping pre-fix sentences")
        return sentences
    return fixed


# --------------------------------- PIPELINE (DB-writing) ---------------------------------

def process_unit(db, source: str, unit_number: int, start_page: int, end_page: int,
                  reader: PdfReader, sops: dict) -> dict:
    """Extraction/filtering pipeline, ending by writing BARE Sentence rows
    to the DB -- no tags, no vocab gate, no FITB (see fitb_parser.py)."""
    label = f"{source} unit {unit_number}"
    print(f"Processing {label} (pages {start_page}-{end_page})...")

    pdf_bytes = split_unit_to_pdf_bytes(reader, start_page, end_page)
    ocr_md = run_ocr(pdf_bytes, sops["ocr"], source, unit_number)

    counts = {}
    sentences = run_text_agent(ocr_md, sops["sentence_finder"], source, unit_number,
                                "sentence_finder", fallback={})
    counts["sentences_extracted"] = len(sentences)
    sentences, counts["sentences_dropped_verbatim"] = filter_verbatim_sentences(sentences, ocr_md, label)

    # Programmatic pre-filter: drop the cheap, obvious hallucination cases
    # (single-hanzi fragments, ellipses/blanks, multiple-choice stubs)
    # before spending a Haiku call on them.
    sentences, counts["sentences_dropped_hallucination_check"] = filter_hallucination_candidates(
        sentences, label
    )

    # Haiku pass over what's left, per unit, using the fix_sentences SOP to
    # catch/correct any remaining hallucination issues.
    sentences = run_fix_sentences_agent(sentences, sops["fix_sentences"], source, unit_number, HSK_LEVEL)

    # --- Programmatic Deduplication & Parentheses Removal ---
    cleaned_sentences = {}
    dupes_dropped = 0
    for zh, en in sentences.items():
        zh_clean = remove_parentheses(zh).strip()
        en_clean = remove_parentheses(en).strip() if en else en

        if not zh_clean:
            continue

        if zh_clean not in cleaned_sentences:
            cleaned_sentences[zh_clean] = en_clean
        else:
            dupes_dropped += 1

    sentences = cleaned_sentences
    counts["sentences_dropped_duplicates"] = dupes_dropped
    counts["sentences_final"] = len(sentences)

    # --- write BARE sentences straight to the DB (number-normalized text) --
    collected_sentences = []  # For debug JSON
    seen_sentences = {}  # Track normalized (hanzi, english) pairs to avoid duplicates

    for zh, en in sentences.items():
        normalized_hanzi = normalize_number_text(zh)

        # Deduplicate based on normalized hanzi + english pair
        sig = (normalized_hanzi, en or "")
        if sig in seen_sentences:
            continue  # Skip duplicate
        seen_sentences[sig] = True

        upsert_sentence_bare(
            db,
            unit_number=unit_number,
            hsk_level=HSK_LEVEL,
            hanzi=normalized_hanzi,
            english=en,
            pinyin="",  # filled in later by tag_sentences.py once tags are resolved
            source=source,
        )

        # Keep track of info sent to the DB for the debug file
        collected_sentences.append({
            "hanzi": normalized_hanzi,
            "english": en,
            "pinyin": "",
            "source": source
        })

    return {
        "unit": unit_number,
        "counts": counts,
        "sentences": collected_sentences,
    }


def run_source(source: str) -> list:
    cfg = SOURCES[source]
    sops = {
        "ocr": load_sop(cfg["OCR_SOP_FILENAME"]),
        "sentence_finder": load_sop(SENTENCE_FINDER_FILENAME),
        "fix_sentences": load_sop(FIX_SENTENCES_SOP_FILEPATH),
    }

    pdf_path = os.path.join(str(TEXTBOOK_RAW_DIR), cfg["RAW_SUBDIR"], f"{HSK_LEVEL}.pdf")
    if not os.path.exists(pdf_path):
        print(f"  [warning] {source} PDF not found at {pdf_path}; skipping")
        return []

    reader = PdfReader(pdf_path)
    unit_ranges = get_unit_page_ranges(cfg)
    if UNITS_TO_PROCESS:
        unit_ranges = [u for u in unit_ranges if u[0] in UNITS_TO_PROCESS]

    # One session PER UNIT -- each unit commits independently, so a later
    # unit's failure can't roll back units that already succeeded in this
    # same run (previously all units in a source shared one big
    # transaction and a single failure discarded everything).
    results = []
    for n, s, e in unit_ranges:
        try:
            with get_session() as db:
                result = process_unit(db, source, n, s, e, reader, sops)
            results.append(result)
        except Exception as ex:
            print(f"  [error] {source} unit {n} failed: {ex} "
                  f"-- units already committed before this one are safe; continuing")
            results.append({
                "unit": n, "counts": {}, "sentences": [],
                "error": str(ex),
            })
    return results


# --------------------------------- DEBUG JSON WRITER ---------------------------------

def write_debug_json(parsed_data: dict, error_msg: str):
    """Writes a structured parsed snapshot to ../debug/sentence_parser/ for debugging purposes."""
    now = datetime.datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")
    datetime_file_str = now.strftime("%Y%m%d_%H%M%S")

    debug_payload = {
        "run_info": {
            "hsk_levels_ran_for": [HSK_LEVEL],
            "date_of_run": date_str,
            "time_of_run": time_str,
            "error_msg": error_msg
        },
        "index": parsed_data
    }

    # Path: ../debug/sentence_parser/ relative to this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.abspath(os.path.join(script_dir, "..", "debug", "sentence_parser"))
    os.makedirs(out_dir, exist_ok=True)

    out_file = os.path.join(out_dir, f"{datetime_file_str}.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(debug_payload, f, ensure_ascii=False, indent=4)

    print(f"  [debug] Wrote runtime debug info to {out_file}")


def run_pipeline():
    # Will track the data structured: { hsk_level: { unit_number: { sentences: [] } } }
    parsed_data = {str(HSK_LEVEL): {}}
    error_msg = ""
    all_results = []
    failed = []

    try:
        init_db()
        sources = SOURCES_TO_PROCESS or list(SOURCES.keys())
        for s in sources:
            all_results.extend(run_source(s))

        # Aggregate debug data hierarchically
        for r in all_results:
            unit_str = str(r["unit"])
            if unit_str not in parsed_data[str(HSK_LEVEL)]:
                parsed_data[str(HSK_LEVEL)][unit_str] = {"sentences": []}
            parsed_data[str(HSK_LEVEL)][unit_str]["sentences"].extend(r["sentences"])

        failed = [r for r in all_results if r.get("error")]
        succeeded = len(all_results) - len(failed)
        print(f"Done. Wrote {succeeded} unit-source result(s) directly to the textbook DB "
              f"(HSK level {HSK_LEVEL}). Sentences are BARE (untagged) -- run fitb_parser.py "
              f"and tag_sentences.py next.")
        for r in all_results:
            if r.get("error"):
                print(f"  {r['unit']}: ❌ FAILED -- {r['error']}")
                continue
            c = r["counts"]
            print(f"  {r['unit']}: {c['sentences_final']} sentences")

        if failed:
            error_msg = f"{len(failed)} unit(s) failed: {[r['unit'] for r in failed]}"

    except Exception as e:
        error_msg = str(e)
        print(f"  [error] Exception during run: {error_msg}")
        raise

    finally:
        write_debug_json(parsed_data, error_msg)

    # Still fail the run (nonzero exit for main.py's abort-on-failure logic)
    # if any individual unit failed -- but only AFTER every other unit's
    # work has already been committed and the debug JSON written.
    if failed:
        raise RuntimeError(error_msg)


def parse_args():
    parser = argparse.ArgumentParser(description="Extract sentences from textbook+workbook units.")
    parser.add_argument("--hsk-level", type=int, default=None,
                         help="Override HSK_LEVEL (defaults to the HSK_LEVEL env var, or 1).")
    parser.add_argument("--unit", type=int, default=None,
                         help="Only process this unit number.")
    parser.add_argument("--source", choices=list(SOURCES.keys()), default=None,
                         help="Only process this source (e.g. 'workbook').")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if args.hsk_level is not None:
        HSK_LEVEL = args.hsk_level
    if args.unit is not None:
        UNITS_TO_PROCESS = [args.unit]
    if args.source is not None:
        SOURCES_TO_PROCESS = [args.source]

    run_pipeline()