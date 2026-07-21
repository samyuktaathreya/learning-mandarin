from fastapi import APIRouter, Depends, Body, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
import os
import re
from dotenv import load_dotenv
import anthropic as anthropic_sdk

from pinyin_utils import strip_punct, to_numbered_pinyin, tones_match
from database import SessionLocal
from models.user import AcceptedAnswer

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


# ----------------------------- AI GRADER (shared) -----------------------------

def _ai_meaning_grade(direction: str, question: str, expected: str, user_answer: str) -> bool:
    """Meaning-based YES/NO grade from Claude. direction is 'ch->en' or
    'en->ch' and only changes the prompt wording; the leniency (accept synonyms,
    word order, articles/particles; reject only on wrong/missing meaning) is the
    same both ways."""
    if direction == "ch->en":
        preamble = (
            "You are grading a Chinese-to-English translation exercise for a beginner learner. "
            "The learner sees a Chinese word or sentence and types an English translation."
        )
        prompt_lines = f"Chinese: {question}\nReference translation: {expected}\nLearner's answer: {user_answer}"
    else:
        preamble = (
            "You are grading an English-to-Chinese translation exercise for a beginner learner. "
            "The learner sees an English word or sentence and types a Chinese translation."
        )
        prompt_lines = f"English: {question}\nReference translation: {expected}\nLearner's answer: {user_answer}"

    response = anthropic_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=5,
        messages=[{
            "role": "user",
            "content": (
                f"{preamble} "
                "Mark the answer CORRECT if it conveys the meaning of the prompt, even if the wording, "
                "articles, particles, punctuation, or phrasing differ from the reference. Be lenient about "
                "minor grammar, synonyms, and word order. Mark INCORRECT only if the meaning is wrong or missing.\n\n"
                f"{prompt_lines}\n\n"
                "Reply with only YES (correct) or NO (incorrect)."
            )
        }]
    )
    return response.content[0].text.strip().upper().startswith("YES")


# ----------------------------- CHINESE -> ENGLISH -----------------------------

@router.post("/api/grade_chinese_to_english")
async def grade_chinese_to_english(payload: dict, db: Session = Depends(get_db)):
    """Meaning-based grading of an English translation of a Chinese prompt,
    judged by Claude. Cached: an identical (expected, cleaned answer) pair that
    was accepted before skips the AI call."""
    user_answer = payload.get("user_answer", "").strip()
    expected = payload.get("expected_answer", "").strip()
    question = payload.get("question", "").strip()

    if not user_answer or not expected:
        return JSONResponse({"is_correct": False})

    cleaned = clean(user_answer)

    if cache_lookup(db, expected, cleaned):
        return JSONResponse({"is_correct": True, "cached": True})

    try:
        is_correct = _ai_meaning_grade("ch->en", question, expected, user_answer)
        if is_correct:
            cache_store(db, expected, cleaned)
        return JSONResponse({"is_correct": is_correct})
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
      - TRANSLATION (everything else): meaning-based AI grade, cached on
        (expected, cleaned answer), shared with the chinese->english cache.

    Expected payload: { user_answer, expected_answer, question_type, question? }
    """
    user_answer = payload.get("user_answer", "").strip()
    expected = payload.get("expected_answer", "").strip()
    question = payload.get("question", "").strip()
    question_type = payload.get("question_type", "")

    if not user_answer or not expected:
        return JSONResponse({"is_correct": False})

    # ---- listening branch: strict pinyin, no AI ----
    if question_type in LISTENING_TYPES:
        user_pinyin = to_numbered_pinyin(strip_punct(user_answer))
        expected_pinyin = to_numbered_pinyin(strip_punct(expected))
        return JSONResponse({
            "is_correct": tones_match(user_pinyin, expected_pinyin),
            "user_pinyin": user_pinyin,
            "expected_pinyin": expected_pinyin,
        })

    # ---- translation branch: AI meaning grade, cached ----
    # For zh answers, clean() mostly normalizes whitespace/punctuation (the
    # English-specific rules simply don't fire on Chinese text), which is fine
    # as a cache key.
    cleaned = clean(user_answer)

    if cache_lookup(db, expected, cleaned):
        return JSONResponse({"is_correct": True, "cached": True})

    try:
        is_correct = _ai_meaning_grade("en->ch", question, expected, user_answer)
        if is_correct:
            cache_store(db, expected, cleaned)
        return JSONResponse({"is_correct": is_correct})
    except Exception as e:
        print(f"Grading error: {e}")
        return JSONResponse({"is_correct": False})