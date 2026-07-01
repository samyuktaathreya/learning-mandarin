from fastapi import APIRouter, Depends, Body
from sqlalchemy.orm import Session
from database import SessionLocal, inverted_index, tags_to_unit_dict, unit_to_tags_dict, unit_questions, META_TAGS
from schemas.user import SessionResponse
import crud
from datetime import datetime
import random
from database import SessionLocal, inverted_index, tags_to_unit_dict, unit_to_tags_dict, unit_questions, META_TAGS, hsk1_dictionary

router = APIRouter()

# ----------------------------- CONSTANTS -----------------------------

MIN_UNIT = 3
NUM_OF_UNIT_TEST_QUESTIONS = 20
PERCENTAGE_TO_PASS_UNIT_TEST = 0.80
MAX_SAME_TAG_PER_SESSION = 2
GRADUATION_THRESHOLD = 3

SPEAKING_TYPES = {
    "speaking vocab",
    "speaking sentence",
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

TIER_2_UNLOCK = 2
TIER_3_UNLOCK = 4
TIER_1_DEPRIORITY_THRESHOLD = 2

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

PRIORITY_WEIGHTS = {
    qt: len(QUESTION_TYPE_PRIORITY) - i
    for i, qt in enumerate(QUESTION_TYPE_PRIORITY)
}

UNIT_TEST_QUESTION_TYPES = TIER_1_TYPES | TIER_2_TYPES | TIER_3_TYPES

# ----------------------------- DB DEPENDENCY -----------------------------

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ----------------------------- TIER HELPERS -----------------------------

def get_allowed_types(correct_count: int) -> set:
    if correct_count >= TIER_3_UNLOCK:
        return TIER_1_TYPES | TIER_2_TYPES | TIER_3_TYPES
    elif correct_count >= TIER_2_UNLOCK:
        return TIER_1_TYPES | TIER_2_TYPES
    else:
        return TIER_1_TYPES


def is_unit_graduated(tag_records: list, unit_tags: set) -> bool:
    record_map = {r.tag: r for r in tag_records}
    for tag in unit_tags:
        record = record_map.get(tag)
        if not record or record.correct_count < GRADUATION_THRESHOLD:
            return False
    return True

# ----------------------------- SRS HELPERS -----------------------------

def get_srs_strength_scores(db: Session, user_id: int, unit_min: int, unit_max: int, graduated_units: set):
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
        if unit not in graduated_units:
            continue

        delta_t = (now - record.last_practice).total_seconds() / 86400
        strength = 0.5 ** (delta_t / record.stability)
        scores.append({"tag": tag, "strength": strength, "stability": record.stability})

    return scores

# ----------------------------- SESSION GENERATORS -----------------------------

def generate_learning_session(db: Session, user_id: int, unit: int):
    records = crud.get_progress_by_user(db, user_id)
    unit_tags = unit_to_tags_dict.get(unit, set())
    unit_records = [r for r in records if r.tag in unit_tags]
    record_map = {r.tag: r for r in unit_records}

    candidates = []
    for tag, record in record_map.items():
        correct_count = record.correct_count
        allowed = get_allowed_types(correct_count)

        for qt in allowed:
            if correct_count >= TIER_1_DEPRIORITY_THRESHOLD and qt in TIER_1_TYPES:
                effective_count = max(correct_count, 10)
            else:
                effective_count = correct_count

            candidates.append({
                "tag": tag,
                "question_type": qt,
                "effective_count": effective_count,
                "correct_count": correct_count,
                "priority_weight": PRIORITY_WEIGHTS.get(qt, 1),
            })

    candidates.sort(key=lambda x: (x["effective_count"], -x["priority_weight"]))

    question_set = []
    used_ids = set()
    tag_counts = {}

    for item in candidates:
        if len(question_set) >= 10:
            break
        tag = item["tag"]
        question_type = item["question_type"]
        if tag_counts.get(tag, 0) >= MAX_SAME_TAG_PER_SESSION:
            continue

        questions = inverted_index.get(tag, [])
        available = [
            q for q in questions
            if q.get("unit") == unit
            and q["question_type"] == question_type
            and q["id"] not in used_ids
        ]

        if available:
            chosen = random.choice(available)
            question_set.append(chosen)
            used_ids.add(chosen["id"])
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    random.shuffle(question_set)
    return SessionResponse(user_id=user_id, session_type="practice_session", question_set=question_set)


def generate_srs_review_session(db: Session, user_id: int, graduated_units: set):
    scores = get_srs_strength_scores(db, user_id, MIN_UNIT, 99, graduated_units)
    scores.sort(key=lambda x: x["strength"])

    question_set = []
    used_ids = set()
    tag_counts = {}

    for item in scores:
        if len(question_set) >= 10:
            break
        tag = item["tag"]
        if tag_counts.get(tag, 0) >= MAX_SAME_TAG_PER_SESSION:
            continue

        for qt in QUESTION_TYPE_PRIORITY:
            questions = inverted_index.get(tag, [])
            available = [
                q for q in questions
                if q["question_type"] == qt
                and q["id"] not in used_ids
                and q.get("unit") in graduated_units
            ]
            if available:
                chosen = random.choice(available)
                question_set.append(chosen)
                used_ids.add(chosen["id"])
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
                break

    random.shuffle(question_set)
    return SessionResponse(user_id=user_id, session_type="practice_session", question_set=question_set)


def generate_unit_test(user_id: int, user_unit: int):
    eligible = [q for q in unit_questions.get(str(user_unit), []) if q["question_type"] in UNIT_TEST_QUESTION_TYPES]
    selected = random.sample(eligible, min(NUM_OF_UNIT_TEST_QUESTIONS, len(eligible)))
    return SessionResponse(user_id=user_id, session_type="unit_test", question_set=selected)

# ----------------------------- ENDPOINTS -----------------------------

@router.get("/api/generate_session/{user_id}", response_model=SessionResponse)
def generate_session(user_id: int, db: Session = Depends(get_db)):
    user = crud.get_user(db, user_id)
    user_unit = user.current_unit
    graduated_units = crud.get_graduated_units(db, user_id)

    if graduated_units:
        srs_scores = get_srs_strength_scores(db, user_id, MIN_UNIT, user_unit - 1, graduated_units)
        weak_srs = [s for s in srs_scores if s["strength"] < 0.70]
        if weak_srs:
            return generate_srs_review_session(db, user_id, graduated_units)

    unit_tags = unit_to_tags_dict.get(user_unit, set())
    all_records = crud.get_progress_by_user(db, user_id)
    unit_records = [r for r in all_records if r.tag in unit_tags]

    if is_unit_graduated(unit_records, unit_tags):
        return generate_unit_test(user_id, user_unit)

    return generate_learning_session(db, user_id, user_unit)


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
            if tag in META_TAGS or tag.startswith("unit_"):
                continue
            crud.update_after_answer(db, user_id, tag, is_correct[i])

    unit_test_result = "unit test not taken"

    if is_unit_test:
        num_correct = sum(is_correct)
        needed = PERCENTAGE_TO_PASS_UNIT_TEST * NUM_OF_UNIT_TEST_QUESTIONS
        if num_correct >= needed:
            user_unit = crud.get_user(db, user_id).current_unit
            crud.graduate_unit(db, user_id, user_unit)
            unit_test_result = "unit test passed"
        else:
            unit_test_result = "unit test failed"

    return {"user_id": user_id, "unit_test_result": unit_test_result}


@router.get("/api/debug/{user_id}")
def debug(user_id: int, db: Session = Depends(get_db)):
    user = crud.get_user(db, user_id)
    unit_tags = unit_to_tags_dict.get(user.current_unit, set())
    all_records = crud.get_progress_by_user(db, user_id)
    unit_records = [r for r in all_records if r.tag in unit_tags]
    graduated = is_unit_graduated(unit_records, unit_tags)
    session = generate_learning_session(db, user_id, user.current_unit)
    return {
        "current_unit": user.current_unit,
        "graduated_units": user.graduated_units,
        "unit_tags_count": len(unit_tags),
        "unit_ready_to_graduate": graduated,
        "questions_found": len(session.question_set),
        "sample_question_types": list(set(q["question_type"] for q in session.question_set)),
        "sample_correct_counts": [{"tag": r.tag, "correct_count": r.correct_count} for r in unit_records]
    }


@router.get("/api/progress/{user_id}")
def get_progress(user_id: int, db: Session = Depends(get_db)):
    user = crud.get_user(db, user_id)
    user_unit = user.current_unit
    graduated_units = crud.get_graduated_units(db, user_id)
    all_records = crud.get_progress_by_user(db, user_id)
    record_map = {r.tag: r for r in all_records}

    unit_progress = {}
    for unit_str in unit_questions.keys():
        unit = int(unit_str)
        unit_tags = unit_to_tags_dict.get(unit, set())
        if not unit_tags:
            continue

        total = len(unit_tags)
        graduated_tags = sum(
            1 for tag in unit_tags
            if record_map.get(tag) and record_map[tag].correct_count >= GRADUATION_THRESHOLD
        )
        avg_correct = (
            sum(record_map[tag].correct_count for tag in unit_tags if tag in record_map) / total
            if total > 0 else 0
        )

        unit_progress[unit_str] = {
            "unit": unit,
            "total_tags": total,
            "graduated_tags": graduated_tags,
            "progress_pct": round(graduated_tags / total * 100) if total > 0 else 0,
            "avg_correct_count": round(avg_correct, 1),
            "is_graduated": unit in graduated_units,
            "is_current": unit == user_unit,
        }

    # current unit word-level progress
    current_unit_tags = unit_to_tags_dict.get(user_unit, set())
    current_unit_words = sorted([
        {
            "tag": tag,
            "correct_count": record_map[tag].correct_count if tag in record_map else 0,
        }
        for tag in current_unit_tags
    ], key=lambda x: x["tag"])

    return {
        "user_id": user_id,
        "current_unit": user_unit,
        "graduated_units": list(graduated_units),
        "unit_progress": unit_progress,
        "current_unit_words": current_unit_words,
    }


@router.get("/api/lookup/{hanzi}")
def lookup(hanzi: str):
    if hanzi in hsk1_dictionary:
        entry = hsk1_dictionary[hanzi]
        return {"hanzi": hanzi, "pinyin": entry["pinyin"], "english": entry["english"]}
    
    # fallback to pypinyin for unknown characters
    from pypinyin import pinyin, Style
    try:
        result = pinyin(hanzi, style=Style.TONE3, heteronym=False)
        py = ''.join([s[0] for s in result]).lower()
    except Exception:
        py = None
    return {"hanzi": hanzi, "pinyin": py, "english": None}