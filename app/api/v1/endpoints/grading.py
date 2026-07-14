from fastapi import APIRouter, Depends, Body, HTTPException
from fastapi.responses import JSONResponse
import os
from dotenv import load_dotenv
import anthropic as anthropic_sdk

from pinyin_utils import strip_punct, to_numbered_pinyin, tones_match

load_dotenv(os.path.join(os.path.dirname(__file__), '../../../.env'))

router = APIRouter()

anthropic_client = anthropic_sdk.Anthropic(api_key=os.environ.get("CLAUDE_API_KEY"))


# ----------------------------- CHINESE -> ENGLISH -----------------------------

@router.post("/api/grade_chinese_to_english")
async def grade_chinese_to_english(payload: dict):
    """
    Meaning-based grading of an English translation of a Chinese prompt, judged
    by Claude. Lenient about wording/articles/synonyms; only meaning matters.
    """
    user_answer = payload.get("user_answer", "").strip()
    expected = payload.get("expected_answer", "").strip()
    question = payload.get("question", "").strip()
    print(f"[grade ch->en] q={question!r} user={user_answer!r} expected={expected!r}")

    if not user_answer or not expected:
        return JSONResponse({"is_correct": False})

    try:
        response = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=5,
            messages=[{
                "role": "user",
                "content": (
                    "You are grading a Chinese-to-English translation exercise for a beginner learner. "
                    "The learner sees a Chinese word or sentence and types an English translation. "
                    "Mark the answer CORRECT if it conveys the meaning of the Chinese, even if the wording, "
                    "articles, punctuation, or phrasing differ from the reference. Be lenient about minor "
                    "grammar, synonyms, and word order. Mark INCORRECT only if the meaning is wrong or missing.\n\n"
                    f"Chinese: {question}\n"
                    f"Reference translation: {expected}\n"
                    f"Learner's answer: {user_answer}\n\n"
                    "Reply with only YES (correct) or NO (incorrect)."
                )
            }]
        )
        result = response.content[0].text.strip().upper()
        return JSONResponse({"is_correct": result.startswith("YES")})
    except Exception as e:
        print(f"Grading error: {e}")
        return JSONResponse({"is_correct": False})


# ----------------------------- ENGLISH -> CHINESE -----------------------------

@router.post("/api/grade_english_to_chinese")
async def grade_english_to_chinese(payload: dict):
    """
    Grades a typed Chinese answer by comparing pinyin instead of characters, so
    homophones written differently but pronounced identically (他/她/它 all ta1)
    count as correct.
    Expected payload: { "user_answer": "他是学生", "expected_answer": "她是学生" }
    """
    user_answer = payload.get("user_answer", "").strip()
    expected = payload.get("expected_answer", "").strip()

    if not user_answer or not expected:
        return JSONResponse({"is_correct": False})

    user_pinyin = to_numbered_pinyin(strip_punct(user_answer))
    expected_pinyin = to_numbered_pinyin(strip_punct(expected))

    is_correct = tones_match(user_pinyin, expected_pinyin)

    return JSONResponse({
        "is_correct": is_correct,
        "user_pinyin": user_pinyin,
        "expected_pinyin": expected_pinyin,
    })