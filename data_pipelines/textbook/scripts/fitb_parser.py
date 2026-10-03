"""
Extracts FILL-IN-THE-BLANK (FITB) exercises from the textbook AND workbook,
per unit, and writes them DIRECTLY to the textbook SQL database as
FitbQuestion rows.

SPLIT: this script used to live inside sentence_parser.py. It has been
pulled out into its own pipeline stage because it's a structurally
different job (write FitbQuestion rows linked to a Sentence, vs. write the
Sentence rows themselves) and because it depends on sentence_parser.py
having ALREADY committed bare Sentence rows for this unit -- FITB entries
are matched back to a Sentence row by content (cjk_only(full_sentence) ->
Sentence.hanzi), read fresh from the DB rather than from in-memory state
shared with sentence_parser.py. Run this AFTER sentence_parser.py for the
same HSK level/unit range.

Like sentence_parser.py, this script does no word segmentation or
vocab-gating -- that's tag_sentences.py's job. OCR is shared via the same
on-disk cache sentence_parser.py already populated for the unit (or
generated fresh here if missing).

PROGRAMMATIC FITB FILTERS (new):
  - answer-must-be-hanzi: if a FITB question's answer is not made up
    entirely of hanzi characters (e.g. it leaked pinyin, a latin word, a
    stray digit, or punctuation), the question is dropped. A FITB answer
    is supposed to be the actual word from the sentence, and this pipeline
    only teaches/tests hanzi.
  - no-pinyin-diacritics-in-question: if the rendered question text (the
    blanked sentence with the other answers filled back in, plus the
    optional translation suffix) contains a pinyin syllable with a tone
    diacritic (e.g. "ā/á/ǎ/à"), the question is dropped. That's a sign
    pinyin leaked into what should be a hanzi-only prompt.
"""

import os
import io
import json
import base64
import re
import datetime
import argparse
import unicodedata

import anthropic
from pypdf import PdfReader, PdfWriter
from app.core.config.shared import settings
from app.core.config.textbook import (
    TEXTBOOK_RAW_DIR,
    TEXTBOOK_INTERMEDIATE_DIR,
    SOP_PATH,
    OCR_PATH,
)
from app.textbook.db_utils import get_session, init_db, get_or_create_unit
from app.textbook.models import FitbQuestion, Sentence

# --------------------------------- CONSTANTS (unchanged from sentence_parser) ---------------------------------
SENTENCE_PARSER_SOP_FILEPATH = SOP_PATH / "sentence_parser"

FITB_FINDER_FILENAME = SENTENCE_PARSER_SOP_FILEPATH / "fitb_finder.txt"
FITB_SOLVER_FILENAME = SENTENCE_PARSER_SOP_FILEPATH / "fitb_solver.md"
FITB_CHECKER_FILENAME = SENTENCE_PARSER_SOP_FILEPATH / "fitb_checker.md"

# Same per-source page layout as sentence_parser.py -- FITB extraction OCRs
# the same unit pages (and reuses the same OCR cache when present).
SOURCES = {
    "textbook": {
        "RAW_SUBDIR": "hsk_textbook",
        "OCR_SOP_FILENAME": SOP_PATH / "sentence_parser" / "ocr.txt",
        "UNIT_STARTS": [34, 42, 50, 60, 68, 76, 84, 92, 102, 110, 118, 124, 132],
        "LAST_UNIT_END_PAGE": 139,
        "FIRST_UNIT_NUMBER": 3,
    },
    "workbook": {
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
HSK_LEVEL = int(os.environ.get("HSK_LEVEL", "1"))

# --------------------------------- SETUP ---------------------------------

api_key = settings.CLAUDE_API_KEY
client = anthropic.Anthropic(api_key=api_key) if api_key else None

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_FULLWIDTH_SPACE_RE = re.compile(r'\u3000+')


# --------------------------------- HELPERS (shared w/ sentence_parser) ---------------------------------

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


def remove_parentheses(text: str) -> str:
    """Removes full-width and half-width parentheses from a string."""
    if not text:
        return text
    return re.sub(r'[\uff08\uff09\(\)]', '', text)


def remove_fullwidth_spaces(text: str) -> str:
    """Removes full-width spaces (　, U+3000) commonly appearing in OCR'd Chinese text."""
    if not text:
        return text
    return _FULLWIDTH_SPACE_RE.sub('', text)


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


def filter_verbatim_fitb(fitb_list: list, ocr_markdown: str, label: str):
    lines = [normalize_for_match(line) for line in ocr_markdown.split("\n")]
    verified, dropped = [], []
    for entry in fitb_list:
        blanked = entry.get("fill in the blank", "")
        segments = [normalize_for_match(seg) for seg in blanked.split("___")]
        found = False
        for line in lines:
            pos, ok = 0, True
            for seg in segments:
                if not seg:
                    continue
                idx = line.find(seg, pos)
                if idx == -1:
                    ok = False
                    break
                pos = idx + len(seg)
            if ok:
                found = True
                break
        if found:
            verified.append(entry)
        else:
            dropped.append(blanked)
    if dropped:
        print(f"  [verbatim] {label}: dropped {len(dropped)} non-verbatim FITB entr(y/ies):")
        for b in dropped:
            print(f"    - {b}")
    return verified, len(dropped)


_BLANK_PLACEHOLDER_RE = re.compile(
    r"\uff08\s*\u3000*\s*\uff09"
    r"|\(\s*\u3000*\s*\)"
    r"|_{2,}"
)


def normalize_fitb_blanks(fitb_list: list, label: str = "") -> list:
    normalized = []
    n_fixed = 0
    n_malformed = 0
    for entry in fitb_list:
        if not isinstance(entry, dict):
            n_malformed += 1
            if label:
                print(f"  [fitb-warning] {label}: dropping malformed (non-dict) FITB "
                      f"entry from solver output: {entry!r}")
            continue
        blanked = entry.get("fill in the blank", "")
        fixed, count = _BLANK_PLACEHOLDER_RE.subn("___", blanked)
        if count and fixed != blanked:
            n_fixed += 1
        entry = {**entry, "fill in the blank": fixed}
        normalized.append(entry)
    if n_fixed and label:
        print(f"  [fitb-normalize] {label}: rewrote non-'___' blank placeholder(s) "
              f"in {n_fixed} entr(y/ies) to '___'")
    if n_malformed and label:
        print(f"  [fitb-warning] {label}: dropped {n_malformed} malformed FITB entr(y/ies) total")
    return normalized


# --------------------------------- FITB -> QUESTIONS (unchanged) ---------------------------------

def _answers_are_words(answers: list, full: str, blanked: str) -> bool:
    for a in answers:
        a_str = (a or "").strip()
        if not a_str:
            return False
        needle = cjk_only(a_str) or a_str
        if needle not in cjk_only(full) and a_str not in full:
            return False
    return True


def expand_fitb(entry: dict) -> list:
    blanked = entry.get("fill in the blank", "")
    answers = entry.get("answer", [])
    translation = (entry.get("translation") or "").strip()
    full = entry.get("full_sentence_answer", "")
    segments = blanked.split("___")
    n_blanks_found = len(segments) - 1
    if n_blanks_found != len(answers) or not answers:
        print(f"  [fitb-warning] blank/answer count mismatch, skipping: {blanked}")
        return []
    if not _answers_are_words(answers, full, blanked):
        print(f"  [fitb-warning] answer not found in full sentence (likely a leaked "
              f"word-bank label, not a word); dropping: {blanked}")
        return []
    questions = []
    for i in range(len(answers)):
        parts = []
        for j, seg in enumerate(segments):
            parts.append(seg)
            if j < len(answers):
                parts.append("___" if j == i else answers[j])
        q_text = "".join(parts)
        if translation:
            q_text += f" ({translation})"
        questions.append({"question": q_text, "answer": answers[i], "full_sentence": full})
    return questions


# --------------------------------- PROGRAMMATIC FITB VALIDATION (new) ---------------------------------

# An answer must be made up ENTIRELY of hanzi characters -- no pinyin,
# latin letters, digits, or stray punctuation. Anything else means the
# solver leaked something that isn't actually the taught word.
_HANZI_ONLY_RE = re.compile(r"^[\u4e00-\u9fff]+$")


def is_hanzi_only(s: str) -> bool:
    return bool(_HANZI_ONLY_RE.fullmatch((s or "").strip()))


# Pinyin tone-marked vowels (both cases, all four tones + the ü variants).
# Their presence in question text means pinyin leaked into what should be
# a hanzi-only fill-in-the-blank prompt.
_PINYIN_DIACRITIC_RE = re.compile(
    "[" +
    "\u0101\u00e1\u01ce\u00e0"  # ā á ǎ à
    "\u0113\u00e9\u011b\u00e8"  # ē é ě è
    "\u012b\u00ed\u01d0\u00ec"  # ī í ǐ ì
    "\u014d\u00f3\u01d2\u00f2"  # ō ó ǒ ò
    "\u016b\u00fa\u01d4\u00f9"  # ū ú ǔ ù
    "\u01d6\u01d8\u01da\u01dc"  # ǖ ǘ ǚ ǜ
    "\u0100\u00c1\u01cd\u00c0"  # Ā Á Ǎ À
    "\u0112\u00c9\u011a\u00c8"  # Ē É Ě È
    "\u012a\u00cd\u01cf\u00cc"  # Ī Í Ǐ Ì
    "\u014c\u00d3\u01d1\u00d2"  # Ō Ó Ǒ Ò
    "\u016a\u00da\u01d3\u00d9"  # Ū Ú Ǔ Ù
    "\u01d5\u01d7\u01d9\u01db"  # Ǖ Ǘ Ǚ Ǜ
    "]"
)


def has_pinyin_diacritics(s: str) -> bool:
    # Also catch decomposed forms (base vowel + combining tone mark) by
    # normalizing to NFC first, so "a" + combining macron collapses to "ā"
    # before the check.
    normalized = unicodedata.normalize("NFC", s or "")
    return bool(_PINYIN_DIACRITIC_RE.search(normalized))


def clean_fitb_spaces(questions: list, label: str = "") -> list:
    """Removes full-width spaces (　) and cleans up spacing in FITB
    questions and full sentences. Also ensures English translation is
    present in the question field if available."""
    cleaned = []
    for q in questions:
        q["question"] = remove_fullwidth_spaces(q["question"]).strip()
        q["full_sentence"] = remove_fullwidth_spaces(q["full_sentence"]).strip()
        q["answer"] = remove_fullwidth_spaces(q["answer"]).strip()
        cleaned.append(q)
    return cleaned


def filter_answer_not_hanzi(questions: list, label: str = "") -> tuple:
    """Drops any FITB question whose answer isn't made up entirely of
    hanzi characters."""
    kept, dropped = [], []
    for q in questions:
        if is_hanzi_only(q["answer"]):
            kept.append(q)
        else:
            dropped.append(q["answer"])
    if dropped:
        print(f"  [fitb-filter] {label}: dropped {len(dropped)} question(s) with a "
              f"non-hanzi answer:")
        for a in dropped:
            print(f"    - {a!r}")
    return kept, len(dropped)


def filter_question_pinyin_diacritics(questions: list, label: str = "") -> tuple:
    """Drops any FITB question whose rendered question text contains a
    pinyin syllable with a tone diacritic."""
    kept, dropped = [], []
    for q in questions:
        if has_pinyin_diacritics(q["question"]):
            dropped.append(q["question"])
        else:
            kept.append(q)
    if dropped:
        print(f"  [fitb-filter] {label}: dropped {len(dropped)} question(s) containing "
              f"pinyin diacritics:")
        for qt in dropped:
            print(f"    - {qt!r}")
    return kept, len(dropped)


def filter_duplicate_fitb(questions: list, label: str = "") -> tuple:
    """Removes duplicate FITB questions, keyed on (question, answer) pairs.
    A duplicate is the exact same blank + answer combination, even if the
    full_sentence differs. First occurrence is kept, later duplicates
    dropped."""
    kept, dropped_count = [], 0
    seen = set()
    for q in questions:
        sig = (q["question"], q["answer"])
        if sig not in seen:
            seen.add(sig)
            kept.append(q)
        else:
            dropped_count += 1
    if dropped_count:
        print(f"  [fitb-filter] {label}: dropped {dropped_count} duplicate FITB question(s)")
    return kept, dropped_count


# --------------------------------- AGENT CALLS ---------------------------------

def run_ocr(pdf_bytes: bytes, ocr_sop: str, source: str, unit_number: int) -> str:
    # Reuses the same on-disk cache path sentence_parser.py writes to, so
    # if that stage already ran for this unit we don't pay for OCR twice.
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


def run_fix_fitb_agent(fitb_questions: list, sop: str, source: str, unit_number: int,
                        hsk_level: int) -> list:
    """Sends this unit's FITB questions to Haiku for validation and
    correction, similar to sentence_parser's fix_sentences_agent. The SOP is
    prefixed with context about the HSK level being checked. Sends full
    question objects and receives back corrected full objects."""
    if client is None or not fitb_questions:
        return fitb_questions

    system_prompt = f"You are checking fill-in-the-blank sentences from the hsk level {hsk_level} curriculum.\n\n{sop}"
    content = ("Here are this unit's fill-in-the-blank questions as a JSON list. "
               "Check and fix any issues, then return the corrected JSON list in the same format:\n\n"
               + json.dumps(fitb_questions, ensure_ascii=False, indent=2))
    response = client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=AGENT_MAX_TOKENS,
        temperature=TEMPERATURE,
        system=system_prompt,
        messages=[{"role": "user", "content": content}],
    )
    fixed = parse_json_response(extract_text_from_response(response), fitb_questions,
                                 source, unit_number, "fitb_checker")
    if not isinstance(fixed, list):
        print(f"  [warning] fitb_checker ({source} unit {unit_number}) returned "
              f"non-list output; keeping pre-check FITB questions")
        return fitb_questions
    return fixed


# --------------------------------- PIPELINE (DB-writing) ---------------------------------

def process_unit(db, source: str, unit_number: int, start_page: int, end_page: int,
                  reader: PdfReader, sops: dict) -> dict:
    """Extraction/filtering pipeline, ending by writing FitbQuestion rows
    to the DB, linked to Sentence rows already committed by
    sentence_parser.py for this unit/source."""
    label = f"{source} unit {unit_number}"
    print(f"Processing {label} (pages {start_page}-{end_page})...")

    pdf_bytes = split_unit_to_pdf_bytes(reader, start_page, end_page)
    ocr_md = run_ocr(pdf_bytes, sops["ocr"], source, unit_number)

    counts = {}
    fitb_candidates = run_text_agent(ocr_md, sops["fitb_finder"], source, unit_number,
                                      "fitb_finder", fallback=[])
    counts["fitb_candidates"] = len(fitb_candidates)
    if fitb_candidates:
        extra = ("Here are the candidate fill-in-the-blank sentences to solve:\n\n"
                 + json.dumps(fitb_candidates, ensure_ascii=False, indent=2))
        fitb = run_text_agent(ocr_md, sops["fitb_solver"], source, unit_number,
                               "fitb_solver", fallback=[], extra_content=extra)
    else:
        fitb = []
    counts["fitb_solved"] = len(fitb)
    fitb = normalize_fitb_blanks(fitb, label)
    fitb, counts["fitb_dropped_verbatim"] = filter_verbatim_fitb(fitb, ocr_md, label)

    fitb_questions = [q for entry in fitb for q in expand_fitb(entry)]

    # --- Programmatic Deduplication & Parentheses Removal for FITB Questions ---
    cleaned_fitb = []
    seen_fitb = set()
    for q in fitb_questions:
        q_text_clean = remove_parentheses(q["question"]).strip()
        q_ans_clean = remove_parentheses(q["answer"]).strip()
        q_full_clean = remove_parentheses(q["full_sentence"]).strip()

        sig = (q_text_clean, q_ans_clean)
        if sig not in seen_fitb:
            seen_fitb.add(sig)
            cleaned_fitb.append({
                "question": q_text_clean,
                "answer": q_ans_clean,
                "full_sentence": q_full_clean
            })

    fitb_questions = cleaned_fitb
    counts["fitb_after_dedup_1"] = len(fitb_questions)

    # --- Remove full-width spaces and clean up spacing ---
    fitb_questions = clean_fitb_spaces(fitb_questions, label)
    counts["fitb_after_space_cleanup"] = len(fitb_questions)

    # --- Run AI check/correction pass over the unit's FITB questions ---
    fitb_questions = run_fix_fitb_agent(fitb_questions, sops["fitb_checker"], source, unit_number, HSK_LEVEL)
    counts["fitb_after_ai_check"] = len(fitb_questions)

    # --- Deduplicate again in case AI corrections made questions identical ---
    fitb_questions, counts["fitb_dropped_duplicates_2"] = filter_duplicate_fitb(
        fitb_questions, label
    )

    # --- Drop questions whose answer isn't pure hanzi ---
    fitb_questions, counts["fitb_dropped_answer_not_hanzi"] = filter_answer_not_hanzi(
        fitb_questions, label
    )

    # --- Drop questions whose text leaked pinyin with diacritics ---
    fitb_questions, counts["fitb_dropped_pinyin_diacritics"] = filter_question_pinyin_diacritics(
        fitb_questions, label
    )

    counts["fitb_final"] = len(fitb_questions)

    # --- resolve sentence_id against Sentence rows already committed by
    # sentence_parser.py for this unit/source, and write FITB rows ---
    unit_row = get_or_create_unit(db, unit_number, hsk_level=HSK_LEVEL)
    existing_sentences = (
        db.query(Sentence)
        .filter(Sentence.unit_id == unit_row.id, Sentence.source == source)
        .all()
    )
    sentence_by_content = {cjk_only(s.hanzi): s for s in existing_sentences}
    collected_fitb = []  # For debug JSON

    unmatched = 0
    for q in fitb_questions:
        sentence_id = None
        match = sentence_by_content.get(cjk_only(q["full_sentence"]))
        if match:
            sentence_id = match.id
        else:
            unmatched += 1

        collected_fitb.append({
            "question": q["question"],
            "answer": q["answer"],
            "full_sentence": q["full_sentence"],
            "source": source
        })

        existing = (
            db.query(FitbQuestion)
            .filter(FitbQuestion.unit_id == unit_row.id,
                    FitbQuestion.question == q["question"],
                    FitbQuestion.answer == q["answer"])
            .first()
        )
        if existing:
            continue

        db.add(FitbQuestion(
            sentence_id=sentence_id,
            unit_id=unit_row.id,
            question=q["question"],
            answer=q["answer"],
            full_sentence=q["full_sentence"],
        ))
    db.flush()

    counts["fitb_unmatched_sentence"] = unmatched
    if unmatched:
        print(f"  [warning] {label}: {unmatched} FITB question(s) could not be matched "
              f"to a Sentence row (run sentence_parser.py first if you haven't)")

    return {
        "unit": unit_number,
        "counts": counts,
        "fitb_questions": collected_fitb
    }


def run_source(source: str) -> list:
    cfg = SOURCES[source]
    sops = {
        "ocr": load_sop(cfg["OCR_SOP_FILENAME"]),
        "fitb_finder": load_sop(FITB_FINDER_FILENAME),
        "fitb_solver": load_sop(FITB_SOLVER_FILENAME),
        "fitb_checker": load_sop(FITB_CHECKER_FILENAME),
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
    # same run.
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
                "unit": n, "counts": {}, "fitb_questions": [],
                "error": str(ex),
            })
    return results


# --------------------------------- DEBUG JSON WRITER ---------------------------------

def write_debug_json(parsed_data: dict, error_msg: str):
    """Writes a structured parsed snapshot to ../debug/fitb_parser/ for debugging purposes."""
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

    script_dir = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.abspath(os.path.join(script_dir, "..", "debug", "fitb_parser"))
    os.makedirs(out_dir, exist_ok=True)

    out_file = os.path.join(out_dir, f"{datetime_file_str}.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(debug_payload, f, ensure_ascii=False, indent=4)

    print(f"  [debug] Wrote runtime debug info to {out_file}")


def run_pipeline():
    parsed_data = {str(HSK_LEVEL): {}}
    error_msg = ""
    all_results = []
    failed = []

    try:
        init_db()
        sources = SOURCES_TO_PROCESS or list(SOURCES.keys())
        for s in sources:
            all_results.extend(run_source(s))

        for r in all_results:
            unit_str = str(r["unit"])
            if unit_str not in parsed_data[str(HSK_LEVEL)]:
                parsed_data[str(HSK_LEVEL)][unit_str] = {"fitb_questions": []}
            parsed_data[str(HSK_LEVEL)][unit_str]["fitb_questions"].extend(r["fitb_questions"])

        failed = [r for r in all_results if r.get("error")]
        succeeded = len(all_results) - len(failed)
        print(f"Done. Wrote {succeeded} unit-source result(s) directly to the textbook DB "
              f"(HSK level {HSK_LEVEL}).")
        for r in all_results:
            if r.get("error"):
                print(f"  {r['unit']}: ❌ FAILED -- {r['error']}")
                continue
            c = r["counts"]
            print(f"  {r['unit']}: {c.get('fitb_final', 0)} FITB questions")

        if failed:
            error_msg = f"{len(failed)} unit(s) failed: {[r['unit'] for r in failed]}"

    except Exception as e:
        error_msg = str(e)
        print(f"  [error] Exception during run: {error_msg}")
        raise

    finally:
        write_debug_json(parsed_data, error_msg)

    if failed:
        raise RuntimeError(error_msg)


def parse_args():
    parser = argparse.ArgumentParser(description="Extract FITB exercises from textbook+workbook units.")
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