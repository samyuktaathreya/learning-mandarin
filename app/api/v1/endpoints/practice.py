from fastapi import APIRouter, Depends, Body
from sqlalchemy.orm import Session
from database import SessionLocal, inverted_index, tags_to_unit_dict, unit_to_tags_dict, unit_questions, META_TAGS
from schemas.user import SessionResponse
import crud
from datetime import datetime
import random

router = APIRouter()

# ----------------------------- CONSTANTS -----------------------------

MIN_UNIT = 3
NUM_OF_UNIT_TEST_QUESTIONS = 20
PERCENTAGE_TO_PASS_UNIT_TEST = 0.80
MAX_SAME_TAG_PER_SESSION = 2  # cap same vocab tag appearing in one session

# priority order — index 0 = highest priority
QUESTION_TYPE_PRIORITY = [
    "listening sentence",
    "speaking sentence",
    "speaking vocab",
    "listening vocab",
    "transcribe word to pinyin",
    "translate english sentence to chinese",
    "translate english word to chinese",
    "fill in the blank",
    "translate chinese sentence to english",
    "translate chinese word to english",
]

# weights derived from priority — highest priority gets highest weight
PRIORITY_WEIGHTS = {
    qt: len(QUESTION_TYPE_PRIORITY) - i
    for i, qt in enumerate(QUESTION_TYPE_PRIORITY)
}

TIER_1_TYPES = {
    "listening vocab",
    "translate chinese word to english",
    "fill in the blank",
    "transcribe word to pinyin",
    "speaking vocab",
}
TIER_2_TYPES = {
    "translate chinese sentence to english",
    "listening sentence",
    "speaking sentence",
    "translate english word to chinese",
}
TIER_3_TYPES = {
    "translate english sentence to chinese",
}

TIER_2_STABILITY_THRESHOLD = 4
TIER_3_STABILITY_THRESHOLD = 16

UNIT_TEST_QUESTION_TYPES = TIER_1_TYPES | TIER_2_TYPES | TIER_3_TYPES

# ----------------------------- DB DEPENDENCY -----------------------------

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ----------------------------- SRS HELPERS -----------------------------

def get_allowed_types_for_stability(stability: float) -> set:
    if stability >= TIER_3_STABILITY_THRESHOLD:
        return TIER_1_TYPES | TIER_2_TYPES | TIER_3_TYPES
    elif stability >= TIER_2_STABILITY_THRESHOLD:
        return TIER_1_TYPES | TIER_2_TYPES
    else:
        return TIER_1_TYPES


def get_strength_scores_for_range(db: Session, user_id: int, unit_min: int, unit_max: int):
    """
    Returns one score entry per (tag, question_type) combo in the unit range.
    Uses the tag's stability for that specific question type.
    """
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

        # check tier unlock based on this specific (tag, question_type) stability
        allowed = get_allowed_types_for_stability(record.stability)
        if record.question_type not in allowed:
            continue

        # learning phase: always show until stability >= 4 (2 correct answers)
        if record.stability < 4:
            strength = 0.0  # always weak, always comes up
        else:
            delta_t = (now - record.last_practice).total_seconds() / 86400
            strength = 0.5 ** (delta_t / record.stability)

        scores.append({
            "tag": tag,
            "question_type": record.question_type,
            "strength": strength,
            "stability": record.stability,
            "priority_weight": PRIORITY_WEIGHTS.get(record.question_type, 1),
        })

    return scores

# ----------------------------- SESSION GENERATORS -----------------------------

def generate_questions(db: Session, user_id: int, num_questions: int, unit_min: int, unit_max: int):
    scores = get_strength_scores_for_range(db, user_id, unit_min, unit_max)

    # sort by strength ascending (weakest first), break ties by priority weight descending
    scores.sort(key=lambda x: (x["strength"], -x["priority_weight"]))

    question_set = []
    used_ids = set()
    tag_counts = {}  # track how many times each vocab tag appears

    for item in scores:
        if len(question_set) >= num_questions:
            break

        tag = item["tag"]
        question_type = item["question_type"]

        # cap same vocab tag per session
        if tag_counts.get(tag, 0) >= MAX_SAME_TAG_PER_SESSION:
            continue

        # find a matching question in the inverted index
        questions = inverted_index.get(tag, [])
        available = [
            q for q in questions
            if unit_min <= q.get("unit", 0) <= unit_max
            and q["question_type"] == question_type
            and q["id"] not in used_ids
        ]

        if available:
            chosen = random.choice(available)
            question_set.append(chosen)
            used_ids.add(chosen["id"])
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

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

    if user_unit == MIN_UNIT:
        current_scores = get_strength_scores_for_range(db, user_id, MIN_UNIT, MIN_UNIT)
        weak = [s for s in current_scores if s["strength"] < 0.85]
        if weak:
            return generate_questions(db, user_id, 10, MIN_UNIT, MIN_UNIT)
        return generate_unit_test(user_id, user_unit)

    past_scores = get_strength_scores_for_range(db, user_id, MIN_UNIT, user_unit - 1)
    if past_scores:
        avg_past = sum(s["strength"] for s in past_scores) / len(past_scores)
        if avg_past < 0.70:
            return generate_questions(db, user_id, 10, MIN_UNIT, user_unit - 1)

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
        question_type = question_data.get("question_type", "")
        for tag in question_data.get("tags", []):
            if tag in META_TAGS or tag.startswith("unit_"):
                continue
            crud.update_stability(db, user_id, tag, question_type, is_correct[i])

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
        "inverted_index_size": len(inverted_index),
        "sample_scores": scores[:5],
        "questions_found": len(generate_questions(db, user_id, 10, MIN_UNIT, MIN_UNIT).question_set),
    }