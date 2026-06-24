from fastapi import APIRouter, Depends, Body
from sqlalchemy.orm import Session
from database import SessionLocal, inverted_index, tags_to_unit_dict, unit_to_tags_dict, unit_questions
from schemas.user import SessionResponse
import crud
from datetime import datetime
import random

router = APIRouter()

# ----------------------------- CONSTANTS -----------------------------

MIN_UNIT = 3  # first unit with questions
NUM_OF_UNIT_TEST_QUESTIONS = 20
PERCENTAGE_TO_PASS_UNIT_TEST = 0.80

UNIT_TEST_QUESTION_TYPES = {
    "translate english sentence to chinese",
    "translate english word to chinese",
    "transcribe word to pinyin",
    "listening vocab",
    "listening sentence",
    "fill in the blank"
}

# ----------------------------- DB DEPENDENCY -----------------------------

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ----------------------------- SRS HELPERS -----------------------------

def get_strength_scores_for_range(db: Session, user_id: int, unit_min: int, unit_max: int):
    records = crud.get_progress_by_user(db, user_id)
    now = datetime.utcnow()
    scores = []

    for record in records:
        tag = record.tag

        if tag not in tags_to_unit_dict:
            continue

        unit = tags_to_unit_dict[tag]

        if unit < unit_min or unit > unit_max:
            continue

        delta_t = (now - record.last_practice).total_seconds() / 86400
        strength = 0.5 ** (delta_t / record.stability)

        scores.append({
            "tag": tag,
            "strength": strength,
            "stability": record.stability
        })

    return scores

# ----------------------------- SESSION GENERATORS -----------------------------

def generate_questions(db: Session, user_id: int, num_questions: int, unit_min: int, unit_max: int):
    scores = get_strength_scores_for_range(db, user_id, unit_min, unit_max)
    scores.sort(key=lambda x: x["strength"])  # weakest first

    question_set = []
    used_ids = set()

    for item in scores:
        if len(question_set) >= num_questions:
            break

        tag = item["tag"]
        questions = inverted_index.get(tag, [])
        questions = [q for q in questions if unit_min <= q.get("unit", 0) <= unit_max]
        available = [q for q in questions if q["id"] not in used_ids]

        if available:
            random.shuffle(available)
            for q in available:
                if len(question_set) >= num_questions:
                    break
                question_set.append(q)
                used_ids.add(q["id"])

    random.shuffle(question_set)

    return SessionResponse(
        user_id=user_id,
        session_type="practice_session",
        question_set=question_set
    )


def generate_mixed_session(db: Session, user_id: int, user_unit: int):
    review = generate_questions(db, user_id, 3, MIN_UNIT, user_unit - 1).question_set
    current = generate_questions(db, user_id, 7, user_unit, user_unit).question_set

    combined = review + current
    random.shuffle(combined)

    return SessionResponse(
        user_id=user_id,
        session_type="practice_session",
        question_set=combined
    )


def generate_unit_test(user_id: int, user_unit: int):
    eligible = [
        q for q in unit_questions.get(str(user_unit), [])
        if q["question_type"] in UNIT_TEST_QUESTION_TYPES
    ]

    selected = random.sample(eligible, min(NUM_OF_UNIT_TEST_QUESTIONS, len(eligible)))

    return SessionResponse(
        user_id=user_id,
        session_type="unit_test",
        question_set=selected
    )

# ----------------------------- ENDPOINTS -----------------------------

@router.get("/api/generate_session/{user_id}", response_model=SessionResponse)
def generate_session(user_id: int, db: Session = Depends(get_db)):
    user = crud.get_user(db, user_id)
    user_unit = user.current_unit

    # first real unit — no review possible
    if user_unit == MIN_UNIT:
        current_scores = get_strength_scores_for_range(db, user_id, MIN_UNIT, MIN_UNIT)
        weak = [s for s in current_scores if s["strength"] < 0.85]
        if weak:
            return generate_questions(db, user_id, 10, MIN_UNIT, MIN_UNIT)
        return generate_unit_test(user_id, user_unit)

    # check past stability
    past_scores = get_strength_scores_for_range(db, user_id, MIN_UNIT, user_unit - 1)
    if past_scores:
        avg_past = sum(s["strength"] for s in past_scores) / len(past_scores)
        if avg_past < 0.70:
            return generate_questions(db, user_id, 10, MIN_UNIT, user_unit - 1)

    # check current unit
    current_scores = get_strength_scores_for_range(db, user_id, user_unit, user_unit)
    weak = [s for s in current_scores if s["strength"] < 0.85]

    if weak:
        return generate_mixed_session(db, user_id, user_unit)

    return generate_unit_test(user_id, user_unit)


@router.patch("/api/submit_session/{user_id}")
def submit_session(
    user_id: int,
    list_of_question_data: list[dict] = Body(...),
    is_correct: list[bool] = Body(...),
    is_unit_test: bool = Body(...),
    db: Session = Depends(get_db)
):
    for i, question_data in enumerate(list_of_question_data):
        for tag in question_data.get("tags", []):
            crud.update_stability(db, user_id, tag, is_correct[i])

    unit_test_result = "unit test not taken"

    if is_unit_test:
        num_correct = sum(is_correct)
        needed = PERCENTAGE_TO_PASS_UNIT_TEST * NUM_OF_UNIT_TEST_QUESTIONS
        if num_correct >= needed:
            user_unit = crud.get_user(db, user_id).current_unit
            crud.update_user_unit(db, user_id, user_unit + 1)
            unit_test_result = "unit test passed"
        else:
            unit_test_result = "unit test failed"

    return {
        "user_id": user_id,
        "unit_test_result": unit_test_result
    }


@router.get("/api/debug/{user_id}")
def debug(user_id: int, db: Session = Depends(get_db)):
    user = crud.get_user(db, user_id)
    scores = get_strength_scores_for_range(db, user_id, MIN_UNIT, MIN_UNIT)
    return {
        "current_unit": user.current_unit,
        "num_scores": len(scores),
        "unit_3_question_count": len(unit_questions.get("3", [])),
        "unit_3_question_types": list(set(q["question_type"] for q in unit_questions.get("3", []))),
        "inverted_index_size": len(inverted_index),
        "sample_tags_unit_3": list(unit_to_tags_dict.get(3, set()))[:10],
        "sample_tag_mappings": {tag: tags_to_unit_dict.get(tag) for tag in ["speaking_vocab", "speaking_sentence", "unit_3", "什么", "你"]},
        "sample_strength_records": [{"tag": r.tag, "stability": r.stability, "last_practice": str(r.last_practice)} for r in crud.get_progress_by_user(db, user_id)[:5]],
        "raw_record_count": len(crud.get_progress_by_user(db, user_id)),
        "tags_in_dict_count": sum(1 for r in crud.get_progress_by_user(db, user_id) if r.tag in tags_to_unit_dict),
        "tags_in_unit_3": sum(1 for r in crud.get_progress_by_user(db, user_id) if tags_to_unit_dict.get(r.tag) == 3),
        "generate_questions_debug": {
            "scores_count": len(get_strength_scores_for_range(db, user_id, MIN_UNIT, MIN_UNIT)),
            "questions_found": len(generate_questions(db, user_id, 10, MIN_UNIT, MIN_UNIT).question_set),
            "available_per_tag": [
                {
                    "tag": item["tag"],
                    "available": len([q for q in inverted_index.get(item["tag"], []) if q.get("unit") == MIN_UNIT])
                }
                for item in sorted(get_strength_scores_for_range(db, user_id, MIN_UNIT, MIN_UNIT), key=lambda x: x["strength"])[:10]
            ]
        }
    }