from fastapi import APIRouter, Depends, Body, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
import os
import re
import json
from dotenv import load_dotenv
import anthropic as anthropic_sdk
from pinyin_utils import strip_punct, to_numbered_pinyin, tones_match
from core.database import SessionLocal
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

def _strip_tones(pinyin_str: str) -> str:
    """Drop tone digits so we can compare base pinyin syllables only —
    this is what lets a correct-sound-but-wrong-character homophone
    (or a slightly mis-marked tone) through to the grammar check below,
    while still blocking answers that are missing or add words."""
    return re.sub(r"\d", "", pinyin_str)

def _ai_listening_leniency_grade(expected: str, user_answer: str, question: str = "") -> bool:
    """Fallback used only when strict pinyin comparison fails but the base
    pinyin (ignoring tones) matches, OR a digit is involved. This is where we
    forgive things a listener genuinely can't distinguish by ear alone --
    homophone pronoun/character swaps (他/她/它), a mismarked tone, digit vs.
    hanzi numerals -- while still rejecting a transcription that's missing
    words, has extra words, or changes the meaning. This is listening
    transcription, not translation: word order and content must still match,
    only the specific homophone/tone/numeral choice is forgiven."""
    response = anthropic_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=10,
        messages=[{
            "role": "user",
            "content": (
                "You are checking a Chinese listening-transcription exercise. The learner heard "
                "Mandarin audio and transcribed what they heard.\n\n"
                "The learner's transcription sounds the same or very close to the reference. Decide "
                "whether it should still be marked CORRECT. Mark correct ONLY if the difference is "
                "something a listener genuinely cannot distinguish by ear alone -- e.g. a homophone "
                "character swap (他/她/它, 在/再, etc.), a minor/ambiguous tone marking, or writing a "
                "number as a digit instead of a hanzi numeral (8 vs 八). Mark INCORRECT if the "
                "learner's version is missing a word, adds a word, changes word order, or changes "
                "the meaning -- those are real transcription errors, not just spelling-by-ear choices.\n\n"
                f"Reference transcription: {expected}\n"
                f"Learner's transcription: {user_answer}\n"
                + (f"Reference meaning: {question}\n" if question else "") +
                "\nReply with only YES (forgivable, mark correct) or NO (real error, mark incorrect)."
            )
        }]
    )
    return response.content[0].text.strip().upper().startswith("YES")


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

def _ai_grade_chinese_to_english(question: str, user_answer: str) -> bool:
    """Grade: is this a valid English translation of the Chinese?"""
    response = anthropic_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=5,
        messages=[{
            "role": "user",
            "content": (
                "You are grading a Chinese-to-English translation exercise. The learner "
                "sees a Chinese sentence and types an English translation.\n\n"
                "Judge whether the learner's answer is a valid, reasonable translation of "
                "the Chinese. Accept if it conveys the core meaning — be lenient with "
                "wording, phrasing, articles, and synonyms. Reject only if the meaning is "
                "actually wrong or missing.\n\n"
                f"Chinese: {question}\n"
                f"Learner's answer: {user_answer}\n\n"
                "Reply with only YES or NO."
            )
        }]
    )
    return response.content[0].text.strip().upper().startswith("YES")

def _ai_grade_english_to_chinese(question: str, user_answer: str) -> bool:
    """Grade: is this a valid Chinese translation of the English?"""
    response = anthropic_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=5,
        messages=[{
            "role": "user",
            "content": (
                "You are grading an English-to-Chinese translation exercise. The learner "
                "sees an English sentence and types a Chinese translation.\n\n"
                "Judge whether the learner's answer is a valid, reasonable translation of "
                "the English. Accept if it conveys the core meaning — be lenient with "
                "wording, phrasing, particles, and synonyms. Reject only if the meaning is "
                "actually wrong or missing.\n\n"
                f"English: {question}\n"
                f"Learner's answer: {user_answer}\n\n"
                "Reply with only YES or NO."
            )
        }]
    )
    return response.content[0].text.strip().upper().startswith("YES")

def cache_lookup_by_question(db: Session, question: str, cleaned: str) -> bool:
    """True iff we've AI-accepted this (question, cleaned_answer) pair before."""
    return db.query(AcceptedAnswer).filter(
        AcceptedAnswer.question == question,
        AcceptedAnswer.cleaned_answer == cleaned,
    ).first() is not None


def cache_store_by_question(db: Session, question: str, cleaned: str):
    """Record an AI-accepted answer keyed on the question."""
    if not cleaned or not question:
        return
    if cache_lookup_by_question(db, question, cleaned):
        return
    try:
        db.add(AcceptedAnswer(question=question, cleaned_answer=cleaned))
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[cache_store_by_question] skipped: {e}")


# ----------------------------- CHINESE -> ENGLISH -----------------------------

@router.post("/api/grade_chinese_to_english")
async def grade_chinese_to_english(payload: dict, db: Session = Depends(get_db)):
    """Grade: is this a valid English translation of the Chinese?
    Ignore the expected_answer entirely — compare against the question itself."""
    user_answer = payload.get("user_answer", "").strip()
    question = payload.get("question", "").strip()

    if not user_answer or not question:
        return JSONResponse({"is_correct": False})

    cleaned = clean(user_answer)

    # Cache: (question, cleaned_answer) pairs we've already accepted
    if cache_lookup_by_question(db, question, cleaned):
        return JSONResponse({"is_correct": True, "cached": True})

    try:
        is_correct = _ai_grade_chinese_to_english(question, user_answer)
        if is_correct:
            cache_store_by_question(db, question, cleaned)
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

        if not is_correct:
            cleaned = clean(user_answer)

            if cache_lookup(db, expected, cleaned):
                return JSONResponse({
                    "is_correct": True, "cached": True,
                    "user_pinyin": user_pinyin, "expected_pinyin": expected_pinyin,
                })

            same_base_pinyin = _strip_tones(user_pinyin) == _strip_tones(expected_pinyin)
            has_digit = bool(re.search(r"\d", user_answer) or re.search(r"\d", expected))

            # Only worth an AI call if the pinyin is at least in the right
            # ballpark (same syllables minus tones) or a digit/numeral is
            # involved -- otherwise this is a genuinely different sentence
            # and there's no point asking.
            if same_base_pinyin or has_digit:
                try:
                    if _ai_listening_leniency_grade(expected, user_answer, question):
                        is_correct = True
                        cache_store(db, expected, cleaned)
                except Exception as e:
                    print(f"Listening leniency grading error: {e}")

        return JSONResponse({
            "is_correct": is_correct,
            "user_pinyin": user_pinyin,
            "expected_pinyin": expected_pinyin,
        })

    # ---- translation branch: AI grade against question only, cached ----
    cleaned = clean(user_answer)

    if cache_lookup_by_question(db, question, cleaned):
        return JSONResponse({"is_correct": True, "cached": True})

    try:
        is_correct = _ai_grade_english_to_chinese(question, user_answer)
        if is_correct:
            cache_store_by_question(db, question, cleaned)
        return JSONResponse({"is_correct": is_correct})
    except Exception as e:
        print(f"Grading error: {e}")
        return JSONResponse({"is_correct": False})