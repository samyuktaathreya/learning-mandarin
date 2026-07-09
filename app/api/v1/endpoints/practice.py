from fastapi import APIRouter, Depends, Body
from sqlalchemy.orm import Session
from database import SessionLocal, inverted_index, tags_to_unit_dict, unit_to_tags_dict, unit_questions, META_TAGS, hsk1_dictionary
from schemas.user import SessionResponse
import crud
from datetime import datetime
import random

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

# --- mixed-session tuning ---
VARIETY_WEIGHT = 6.0            # how hard type-variety fights count-priority (higher = more varied)
TYPE_DECAY = 0.6               # each pick of a type multiplies its variety bonus by this
REVIEW_THRESHOLD = 0.70       # below this strength counts as reviewable for the random-seasoning pool
REVIEW_WEAKEST_FRACTION = 0.8  # of review slots, this share goes to weakest-first; rest random-below-threshold
SESSION_SIZE = 10

# strength -> review fraction. Flat-ish in normal use, ramps to 1.0 only at
# the catastrophic-decay extreme (user hasn't touched the app in a long time).
_RATIO_ANCHORS = [
    (0.00, 1.00),
    (0.15, 0.90),
    (0.30, 0.65),
    (0.50, 0.40),
    (0.80, 0.15),
    (1.00, 0.15),
]


def review_fraction_from_strength(avg_strength: float) -> float:
    """Piecewise-linear interpolation over the anchor points above."""
    s = max(0.0, min(1.0, avg_strength))
    for i in range(len(_RATIO_ANCHORS) - 1):
        s0, f0 = _RATIO_ANCHORS[i]
        s1, f1 = _RATIO_ANCHORS[i + 1]
        if s0 <= s <= s1:
            if s1 == s0:
                return f0
            t = (s - s0) / (s1 - s0)
            return f0 + t * (f1 - f0)
    return _RATIO_ANCHORS[-1][1]

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

# ----------------------------- VARIETY SELECTOR -----------------------------

def _select_with_variety(candidates, n, unit_filter, used_ids, tag_counts):
    """
    Pick up to n questions, blending two goals:
      - review weak words first (lower effective_count is better)
      - keep question types varied (a fresh/underused type gets a bonus that
        can override small count differences)

    candidates: dicts with keys tag, question_type, effective_count, priority_weight
    unit_filter: set of units a question may come from (None = any unit)
    used_ids / tag_counts: shared mutable state so multiple calls (learning +
      review pools) don't collide on the same question or overuse a tag.
    """
    picked = []
    type_penalty = {}

    while len(picked) < n:
        best = None
        best_score = None
        best_avail = None

        for item in candidates:
            tag = item["tag"]
            qt = item["question_type"]
            if tag_counts.get(tag, 0) >= MAX_SAME_TAG_PER_SESSION:
                continue

            # variety bonus: full when the type is unused, decays each time it's picked
            variety_bonus = VARIETY_WEIGHT * (TYPE_DECAY ** type_penalty.get(qt, 0))
            # lower score wins: count drives it, variety pulls fresh types up,
            # tiny priority term breaks ties toward sentences.
            score = item["effective_count"] - variety_bonus - 0.001 * item["priority_weight"]

            if best_score is None or score < best_score:
                questions = inverted_index.get(tag, [])
                avail = [
                    q for q in questions
                    if q["question_type"] == qt
                    and q["id"] not in used_ids
                    and (unit_filter is None or q.get("unit") in unit_filter)
                ]
                if avail:
                    best, best_score, best_avail = item, score, avail

        if best is None:
            break

        chosen = random.choice(best_avail)
        picked.append(chosen)
        used_ids.add(chosen["id"])
        tag_counts[best["tag"]] = tag_counts.get(best["tag"], 0) + 1
        type_penalty[best["question_type"]] = type_penalty.get(best["question_type"], 0) + 1

    return picked

# ----------------------------- SESSION GENERATORS -----------------------------

def _build_learning_candidates(unit, record_map):
    unit_tags = unit_to_tags_dict.get(unit, set())
    candidates = []
    for tag in unit_tags:
        record = record_map.get(tag)
        correct_count = record.correct_count if record else 0
        for qt in get_allowed_types(correct_count):
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
    return candidates


def generate_mixed_session(db: Session, user_id: int, unit: int, graduated_units: set):
    """
    Learning-mode session that blends current-unit material with review of
    graduated units. The review share scales with how well previous units are
    retained: strong retention -> mostly new material; long absence -> mostly
    (or all) review.
    """
    records = crud.get_progress_by_user(db, user_id)
    record_map = {r.tag: r for r in records}

    # decide review fraction from average retention of graduated units
    review_scores = get_srs_strength_scores(db, user_id, MIN_UNIT, 99, graduated_units)
    if review_scores:
        avg_strength = sum(s["strength"] for s in review_scores) / len(review_scores)
        review_frac = review_fraction_from_strength(avg_strength)
    else:
        review_frac = 0.0  # nothing graduated yet -> all new material

    n_review = round(SESSION_SIZE * review_frac)
    n_review = min(n_review, len(review_scores))  # can't review more than exist
    n_new = SESSION_SIZE - n_review

    used_ids = set()
    tag_counts = {}
    question_set = []

    # --- current-unit learning pool ---
    learn_candidates = _build_learning_candidates(unit, record_map)
    question_set += _select_with_variety(learn_candidates, n_new, {unit}, used_ids, tag_counts)

    # --- review pool: weakest-first, seasoned with random below-threshold picks ---
    if n_review > 0:
        review_scores.sort(key=lambda x: x["strength"])
        weakest = [s["tag"] for s in review_scores]
        below = [s["tag"] for s in review_scores if s["strength"] < REVIEW_THRESHOLD]

        n_weak = round(n_review * REVIEW_WEAKEST_FRACTION)
        review_tag_order = weakest[:n_weak]
        random.shuffle(below)
        review_tag_order += [t for t in below if t not in review_tag_order][: n_review - len(review_tag_order)]

        review_candidates = []
        for tag in review_tag_order:
            for qt in QUESTION_TYPE_PRIORITY:
                review_candidates.append({
                    "tag": tag,
                    "question_type": qt,
                    "effective_count": 0,
                    "priority_weight": PRIORITY_WEIGHTS.get(qt, 1),
                })
        question_set += _select_with_variety(review_candidates, n_review, graduated_units, used_ids, tag_counts)

    # backfill from the learning pool if review came up short, so sessions stay full
    if len(question_set) < SESSION_SIZE:
        question_set += _select_with_variety(
            learn_candidates, SESSION_SIZE - len(question_set), {unit}, used_ids, tag_counts
        )

    random.shuffle(question_set)
    return SessionResponse(user_id=user_id, session_type="practice_session", question_set=question_set)


def generate_learning_session(db: Session, user_id: int, unit: int):
    """
    Original single-pool learning session. No longer used by the router (the
    mixed session replaces it) but kept so you can revert by swapping the one
    line in generate_session().
    """
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
        if len(question_set) >= SESSION_SIZE:
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
    """
    Original pure-review session. Superseded by generate_mixed_session, kept for
    reference / manual use.
    """
    scores = get_srs_strength_scores(db, user_id, MIN_UNIT, 99, graduated_units)
    scores.sort(key=lambda x: x["strength"])

    question_set = []
    used_ids = set()
    tag_counts = {}

    review_candidates = []
    for item in scores:
        for qt in QUESTION_TYPE_PRIORITY:
            review_candidates.append({
                "tag": item["tag"],
                "question_type": qt,
                "effective_count": 0,
                "priority_weight": PRIORITY_WEIGHTS.get(qt, 1),
            })
    question_set = _select_with_variety(review_candidates, SESSION_SIZE, graduated_units, used_ids, tag_counts)

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

    unit_tags = unit_to_tags_dict.get(user_unit, set())
    all_records = crud.get_progress_by_user(db, user_id)
    unit_records = [r for r in all_records if r.tag in unit_tags]

    # unit test stays exactly as-is: pure current unit, no review mixed in
    if is_unit_graduated(unit_records, unit_tags):
        return generate_unit_test(user_id, user_unit)

    # otherwise a learning session that folds in review proportional to retention
    return generate_mixed_session(db, user_id, user_unit, graduated_units)


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
    graduated_units = crud.get_graduated_units(db, user_id)
    session = generate_mixed_session(db, user_id, user.current_unit, graduated_units)
    return {
        "current_unit": user.current_unit,
        "graduated_units": user.graduated_units,
        "unit_tags_count": len(unit_tags),
        "unit_ready_to_graduate": graduated,
        "questions_found": len(session.question_set),
        "sample_question_types": list(set(q["question_type"] for q in session.question_set)),
        "sample_units": sorted(set(q.get("unit") for q in session.question_set)),
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