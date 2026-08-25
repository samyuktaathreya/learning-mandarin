import os
import re
import json
import anthropic as anthropic_sdk
from sqlalchemy.orm import Session
from dotenv import load_dotenv
from core.config.shared import settings

from pinyin_utils import strip_punct, to_numbered_pinyin, tones_match
from session.models import AcceptedAnswer
from session.crud import log_mismatch
from app.core.logger import logger

anthropic_client = anthropic_sdk.Anthropic(api_key=settings.CLAUDE_API_KEY)

LISTENING_TYPES = {"listening sentence", "listening vocab"}

# ----------------------------- ANSWER NORMALIZATION -----------------------------

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
    return re.sub(r"\d", "", pinyin_str)


# ----------------------------- ACCEPTED-ANSWER CACHE -----------------------------

def cache_lookup(db: Session, expected: str, cleaned: str) -> bool:
    return db.query(AcceptedAnswer).filter(
        AcceptedAnswer.expected_answer == expected,
        AcceptedAnswer.cleaned_answer == cleaned,
    ).first() is not None

def cache_store(db: Session, expected: str, cleaned: str):
    if not cleaned:
        return
    if cache_lookup(db, expected, cleaned):
        return
    try:
        db.add(AcceptedAnswer(expected_answer=expected, cleaned_answer=cleaned))
        db.commit()
    except Exception as e:
        db.rollback()
        logger.debug(f"[cache_store] skipped: {e}")

def cache_lookup_by_question(db: Session, question: str, cleaned: str) -> bool:
    return db.query(AcceptedAnswer).filter(
        AcceptedAnswer.question == question,
        AcceptedAnswer.cleaned_answer == cleaned,
    ).first() is not None

def cache_store_by_question(db: Session, question: str, cleaned: str):
    if not cleaned or not question:
        return
    if cache_lookup_by_question(db, question, cleaned):
        return
    try:
        db.add(AcceptedAnswer(question=question, cleaned_answer=cleaned))
        db.commit()
    except Exception as e:
        db.rollback()
        logger.debug(f"[cache_store_by_question] skipped: {e}")


# ----------------------------- AI GRADER CORE -----------------------------

def _ai_listening_leniency_grade(expected: str, user_answer: str, question: str = "") -> bool:
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

def _ai_grade_chinese_to_english(question: str, user_answer: str) -> bool:
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

def _ai_grade_with_mismatch_check(direction: str, question: str, expected: str, user_answer: str) -> dict:
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
        logger.debug(f"[grader] failed to parse JSON: {raw_text!r}")
        return {"is_correct": False, "expected_matches_question": True, "reasoning": "parse failure"}

    result.setdefault("is_correct", False)
    result.setdefault("expected_matches_question", True)
    result.setdefault("reasoning", "")
    return result

def _maybe_log_mismatch(db: Session, direction: str, question: str, expected: str, result: dict):
    if result.get("expected_matches_question", True):
        return
    try:
        log_mismatch(
            db,
            question=question,
            expected_answer=expected,
            direction=direction,
            reasoning=result.get("reasoning", ""),
        )
    except Exception as e:
        logger.debug(f"[log_mismatch] skipped: {e}")


# ----------------------------- SERVICE WRAPPERS -----------------------------

def evaluate_chinese_to_english(db: Session, user_answer: str, question: str) -> dict:
    cleaned = clean(user_answer)

    if cache_lookup_by_question(db, question, cleaned):
        return {"is_correct": True, "cached": True}

    try:
        is_correct = _ai_grade_chinese_to_english(question, user_answer)
        if is_correct:
            cache_store_by_question(db, question, cleaned)
        return {"is_correct": is_correct}
    except Exception as e:
        logger.debug(f"Grading error: {e}")
        return {"is_correct": False}

def evaluate_english_to_chinese(db: Session, user_answer: str, expected: str, question: str, question_type: str) -> dict:
    if question_type in LISTENING_TYPES:
        user_pinyin = to_numbered_pinyin(strip_punct(user_answer))
        expected_pinyin = to_numbered_pinyin(strip_punct(expected))
        is_correct = tones_match(user_pinyin, expected_pinyin)

        if not is_correct:
            cleaned = clean(user_answer)
            if cache_lookup(db, expected, cleaned):
                return {
                    "is_correct": True, "cached": True,
                    "user_pinyin": user_pinyin, "expected_pinyin": expected_pinyin,
                }

            same_base_pinyin = _strip_tones(user_pinyin) == _strip_tones(expected_pinyin)
            has_digit = bool(re.search(r"\d", user_answer) or re.search(r"\d", expected))

            if same_base_pinyin or has_digit:
                try:
                    if _ai_listening_leniency_grade(expected, user_answer, question):
                        is_correct = True
                        cache_store(db, expected, cleaned)
                except Exception as e:
                    logger.debug(f"Listening leniency grading error: {e}")

        return {
            "is_correct": is_correct,
            "user_pinyin": user_pinyin,
            "expected_pinyin": expected_pinyin,
        }

    # translation branch
    cleaned = clean(user_answer)
    if cache_lookup_by_question(db, question, cleaned):
        return {"is_correct": True, "cached": True}

    try:
        is_correct = _ai_grade_english_to_chinese(question, user_answer)
        if is_correct:
            cache_store_by_question(db, question, cleaned)
        return {"is_correct": is_correct}
    except Exception as e:
        logger.debug(f"Grading error: {e}")
        return {"is_correct": False}