from fastapi import APIRouter, Depends, Body, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
import os
import re
import json
from dotenv import load_dotenv
import anthropic as anthropic_sdk

from pinyin_utils import strip_punct, to_numbered_pinyin, tones_match
from database import SessionLocal
from models.user import AcceptedAnswer
import crud

load_dotenv(os.path.join(os.path.dirname(__file__), '../../../.env'))

router = APIRouter()

anthropic_client = anthropic_sdk.Anthropic(api_key=os.environ.get("CLAUDE_API_KEY"))


# ----------------------------- DB DEPENDENCY -----------------------------

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ----------------------------- ANSWER NORMALIZATION -----------------------------
# Server-side port of the frontend clean() (DuolingoStyleQuestions.jsx). Cache
# keys are (expected_answer, cleaned_answer); if the backend cleaned answers
# differently from the frontend, keys wouldn't line up and the cache would
# rarely hit. Keep these two in sync -- if you change one, change the other.

_PUNCT_RE = re.compile(r"[.,\/#!$%\^&\*;:{}=\-_`~()。？！、，：；\"“”'‘’]")
_HANZI_GAP_RE = re.compile(r"([\u4e00-\u9fff])\s+([\u4e00-\u9fff])")
_ARTICLES_RE = re.compile(r"\b(a|an|the)\b")
_WS_RE = re.compile(r"\s+")


def clean(s: str) -> str:
    s = s.lower()
    s = _PUNCT_RE.sub("", s)
    s = _HANZI_GAP_RE.sub(r"\1\2", s)
    s = re.sub(r"\bim\b", "i am", s)
    s = re.sub(r"\byoure\b", "you are", s)
    s = re.sub(r"\bhes\b", "he is", s)
    s = re.sub(r"\bshes\b", "she is", s)
    s = _ARTICLES_RE.sub("", s)
    s = _WS_RE.sub(" ", s)
    return s.strip()


# ----------------------------- ACCEPTED-ANSWER CACHE -----------------------------
# Global, accepted-only cache keyed on (expected_answer, cleaned_answer). Shared
# by every AI grading branch (chinese->english AND english->chinese translation).
# A hit skips the AI call; we only ever write CORRECT verdicts, so a cached row
# can only let an answer through, never wrongly block one.

def cache_lookup(db: Session, expected: str, cleaned: str) -> bool:
    """True iff this (expected, cleaned) pair was previously AI-accepted."""
    return db.query(AcceptedAnswer).filter(
        AcceptedAnswer.expected_answer == expected,
        AcceptedAnswer.cleaned_answer == cleaned,
    ).first() is not None


def cache_store(db: Session, expected: str, cleaned: str):
    """Record an AI-accepted answer. Ignores duplicates (the unique constraint
    means a racing double-insert just no-ops)."""
    if not cleaned:
        return
    if cache_lookup(db, expected, cleaned):
        return
    try:
        db.add(AcceptedAnswer(expected_answer=expected, cleaned_answer=cleaned))
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[cache_store] skipped: {e}")


def _ai_listening_numeral_grade(expected: str, user_answer: str) -> bool:
    """Fallback used only when strict pinyin comparison fails AND a digit
    appears in either string. to_numbered_pinyin has no rule for converting a
    bare Arabic digit ('8') into its spoken pinyin ('ba1'), so a learner who
    correctly transcribes a sentence using digits instead of hanzi numerals
    (e.g. '8\u70b918\u5206' vs '\u516b\u70b9\u5341\u516b\u5206') fails the strict check as a false
    negative even though the transcription is right. This treats Arabic
    numerals as valid stand-ins for their hanzi numeral equivalents, but
    otherwise still requires an exact transcription match -- this is
    listening/transcription, not translation, so wording/word-order
    differences beyond numeral style should still fail."""
    response = anthropic_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=5,
        messages=[{
            "role": "user",
            "content": (
                "You are checking a Chinese listening-transcription exercise. The learner heard "
                "Mandarin audio and transcribed what they heard.\n\n"
                "Treat Arabic numerals (8, 18) as fully valid stand-ins for their hanzi numeral "
                "equivalents (\u516b, \u5341\u516b) -- either way of writing a number is acceptable. Ignore "
                "punctuation differences. Otherwise the transcription must match exactly in wording "
                "and word order: this is transcription, not translation, so do NOT accept "
                "paraphrases, synonyms, or reordering beyond numeral style.\n\n"
                f"Reference: {expected}\n"
                f"Learner's transcription: {user_answer}\n\n"
                "Reply with only YES (same, only numeral style differs) or NO (actually different)."
            )
        }]
    )
    return response.content[0].text.strip().upper().startswith("YES")


# ----------------------------- AI GRADER (shared) -----------------------------
# Grades the learner's answer against the QUESTION's actual meaning (not just
# the stored `expected` string), and in the same call reports whether
# `expected` itself is a valid translation of `question`. This is what fixes
# bugs like a sentence's stored expected translation actually belonging to a
# different sentence (OCR/pipeline mismatch): grading no longer trusts
# `expected` blindly, it trusts the Chinese/English `question` shown to the
# learner and treats `expected` as a hint. A mismatch is logged via crud so
# the underlying data can be batch-reprocessed later, but never blocks or
# corrupts the learner's grade in the moment.

def _ai_grade_with_mismatch_check(direction: str, question: str, expected: str, user_answer: str) -> dict:
    """Returns {"is_correct": bool, "expected_matches_question": bool, "reasoning": str}.
    direction is 'ch->en' or 'en->ch' and only changes prompt wording -- the
    grading leniency (accept synonyms, word order, articles/particles; reject
    only on wrong/missing meaning) is the same both ways."""
    if direction == "ch->en":
        preamble = (
            "You are grading a Chinese-to-English translation exercise for a beginner learner. "
            "The learner sees a Chinese word or sentence and types an English translation."
        )
        prompt_lines = f"Chinese: {question}\nStored reference translation: {expected}\nLearner's answer: {user_answer}"
    else:
        preamble = (
            "You are grading an English-to-Chinese translation exercise for a beginner learner. "
            "The learner sees an English word or sentence and types a Chinese translation."
        )
        prompt_lines = f"English: {question}\nStored reference translation: {expected}\nLearner's answer: {user_answer}"

    response = anthropic_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=150,
        messages=[{
            "role": "user",
            "content": (
                f"{preamble}\n\n"
                "The 'stored reference translation' comes from an automated data pipeline and may "
                "occasionally be WRONG for this question (e.g. it belongs to a different sentence). "
                "Grade the learner's answer against what the question ITSELF actually means, not "
                "blindly against the stored reference. Mark correct if it conveys the meaning of the "
                "question, even if wording, articles, particles, punctuation, or phrasing differ. Be "
                "lenient about minor grammar, synonyms, and word order. Mark incorrect only if the "
                "meaning is wrong or missing.\n\n"
                f"{prompt_lines}\n\n"
                "Output ONLY valid JSON, no markdown, no preamble, matching exactly:\n"
                "{\n"
                '  "is_correct": true or false,\n'
                '  "expected_matches_question": true or false,\n'
                '  "reasoning": "brief 1-sentence explanation"\n'
                "}"
            )
        }]
    )

    raw_text = response.content[0].text
    json_match = re.search(r"\{.*\}", raw_text, re.DOTALL)
    if json_match:
        raw_text = json_match.group(0)

    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError:
        # Fall back to a conservative "mark incorrect, assume expected is fine"
        # rather than raising -- a malformed grader response shouldn't 500 the
        # request, and we don't want to log a mismatch we're not sure of.
        print(f"[grader] failed to parse JSON: {raw_text!r}")
        return {"is_correct": False, "expected_matches_question": True, "reasoning": "parse failure"}

    result.setdefault("is_correct", False)
    result.setdefault("expected_matches_question", True)
    result.setdefault("reasoning", "")
    return result


def _maybe_log_mismatch(db: Session, direction: str, question: str, expected: str, result: dict):
    """Best-effort logging -- must never break grading if it fails."""
    if result.get("expected_matches_question", True):
        return
    try:
        crud.log_mismatch(
            db,
            question=question,
            expected_answer=expected,
            direction=direction,
            reasoning=result.get("reasoning", ""),
        )
    except Exception as e:
        print(f"[log_mismatch] skipped: {e}")


# ----------------------------- CHINESE -> ENGLISH -----------------------------

@router.post("/api/grade_chinese_to_english")
async def grade_chinese_to_english(payload: dict, db: Session = Depends(get_db)):
    """Meaning-based grading of an English translation of a Chinese prompt,
    judged by Claude against the question itself. Cached: an identical
    (expected, cleaned answer) pair that was accepted before skips the AI call."""
    user_answer = payload.get("user_answer", "").strip()
    expected = payload.get("expected_answer", "").strip()
    question = payload.get("question", "").strip()

    if not user_answer or not expected:
        return JSONResponse({"is_correct": False})

    cleaned = clean(user_answer)

    if cache_lookup(db, expected, cleaned):
        return JSONResponse({"is_correct": True, "cached": True})

    try:
        result = _ai_grade_with_mismatch_check("ch->en", question, expected, user_answer)
        _maybe_log_mismatch(db, "ch->en", question, expected, result)
        if result["is_correct"]:
            cache_store(db, expected, cleaned)
        return JSONResponse({"is_correct": result["is_correct"]})
    except Exception as e:
        print(f"Grading error: {e}")
        return JSONResponse({"is_correct": False})


# ----------------------------- ENGLISH -> CHINESE -----------------------------

# Question types that are LISTENING transcription (strict pinyin, word order
# enforced, homophones accepted, no AI). Everything else routed here is treated
# as a translation and graded by AI for meaning.
LISTENING_TYPES = {"listening sentence", "listening vocab"}


@router.post("/api/grade_english_to_chinese")
async def grade_english_to_chinese(payload: dict, db: Session = Depends(get_db)):
    """Branches on question_type:
      - LISTENING (transcription): strict pinyin comparison -- word order
        enforced, homophones (他/她/它 -> ta1) accepted. Deterministic, no AI,
        no cache (already free).
      - TRANSLATION (everything else): meaning-based AI grade against the
        question itself, cached on (expected, cleaned answer), shared with
        the chinese->english cache.

    Expected payload: { user_answer, expected_answer, question_type, question? }
    """
    user_answer = payload.get("user_answer", "").strip()
    expected = payload.get("expected_answer", "").strip()
    question = payload.get("question", "").strip()
    question_type = payload.get("question_type", "")

    if not user_answer or not expected:
        return JSONResponse({"is_correct": False})

    # ---- listening branch: strict pinyin, no AI (except numeral fallback) ----
    if question_type in LISTENING_TYPES:
        user_pinyin = to_numbered_pinyin(strip_punct(user_answer))
        expected_pinyin = to_numbered_pinyin(strip_punct(expected))
        is_correct = tones_match(user_pinyin, expected_pinyin)

        # Known false-negative: to_numbered_pinyin can't convert bare Arabic
        # digits to pinyin, so a digit-written answer (8点18分) fails the
        # strict comparison against a hanzi-numeral reference (八点十八分)
        # even when it's the same transcription. Only pay for an AI call when
        # the strict check already failed AND a digit is actually involved --
        # normal listening grading stays free and instant.
        if not is_correct and (re.search(r"\d", user_answer) or re.search(r"\d", expected)):
            cleaned = clean(user_answer)
            if cache_lookup(db, expected, cleaned):
                return JSONResponse({
                    "is_correct": True, "cached": True,
                    "user_pinyin": user_pinyin, "expected_pinyin": expected_pinyin,
                })
            try:
                if _ai_listening_numeral_grade(expected, user_answer):
                    is_correct = True
                    cache_store(db, expected, cleaned)
            except Exception as e:
                print(f"Listening numeral grading error: {e}")

        return JSONResponse({
            "is_correct": is_correct,
            "user_pinyin": user_pinyin,
            "expected_pinyin": expected_pinyin,
        })

    # ---- translation branch: AI meaning grade against the question, cached ----
    # For zh answers, clean() mostly normalizes whitespace/punctuation (the
    # English-specific rules simply don't fire on Chinese text), which is fine
    # as a cache key.
    cleaned = clean(user_answer)

    if cache_lookup(db, expected, cleaned):
        return JSONResponse({"is_correct": True, "cached": True})

    try:
        result = _ai_grade_with_mismatch_check("en->ch", question, expected, user_answer)
        _maybe_log_mismatch(db, "en->ch", question, expected, result)
        if result["is_correct"]:
            cache_store(db, expected, cleaned)
        return JSONResponse({"is_correct": result["is_correct"]})
    except Exception as e:
        print(f"Grading error: {e}")
        return JSONResponse({"is_correct": False})