from datetime import datetime

from fastapi import APIRouter, Depends, Body, HTTPException
from sqlalchemy.orm import Session

from session.database import get_db
from session.schemas import SessionResponse
from session.crud import get_user, get_tiers_for_tags, get_graduated_units, get_progress_by_user
from session.constants import GRADUATION_THRESHOLD, REVIEW_THRESHOLD
from session.services.progress import get_collapsed_progress, is_unit_graduated
from session.services.review_engine import (
    is_facet_review_eligible,
    review_due_word_count,
    review_due_tomorrow_word_count,
)
from session.services.tips import attach_tips, save_tip
from session.services.session_builder import (
    generate_practice_session,
    generate_full_session,
    process_submission,
)
from textbook.services import unit_to_vocab_tags_dict, unit_questions, hsk1_dictionary
from characters.database import get_characters_db

router = APIRouter()


@router.get("/api/generate_session/{user_id}", response_model=SessionResponse)
def generate_session(user_id: int, mode: str = "sentence", skip_review: bool = False,
                      db: Session = Depends(get_db),
                      characters_db: Session = Depends(get_characters_db)):
    return generate_full_session(db, characters_db, user_id, mode=mode, skip_review=skip_review)


@router.patch("/api/submit_session/{user_id}")
def submit_session(
    user_id: int,
    list_of_question_data: list[dict] = Body(...),
    is_correct: list[bool] = Body(...),
    is_unit_test: bool = Body(...),
    mode: str = Body("sentence"),
    db: Session = Depends(get_db)
):
    return process_submission(db, user_id, list_of_question_data, is_correct, is_unit_test, mode)


@router.get("/api/debug/{user_id}")
def debug(user_id: int, db: Session = Depends(get_db)):
    user = get_user(db, user_id)
    unit_tags = unit_to_vocab_tags_dict.get(user.current_unit, set())
    all_records = get_collapsed_progress(db, user_id)
    unit_records = [r for r in all_records if r.tag in unit_tags]
    graduated = is_unit_graduated(db, user_id, unit_records, unit_tags)
    session = generate_practice_session(db, user_id, user.current_unit)
    tiers = get_tiers_for_tags(db, user_id, unit_tags)

    from session.services.review_engine import _all_review_eligible_facets, _due_review_facets
    eligible = _all_review_eligible_facets(db, user_id)
    due = _due_review_facets(db, user_id)

    return {
        "current_unit": user.current_unit,
        "graduated_units": user.graduated_units,
        "unit_tags_count": len(unit_tags),
        "unit_ready_to_graduate": graduated,
        "review_eligible_facet_count": len(eligible),
        "review_due_facet_count": len(due),
        "questions_found": len(session.question_set),
        "sample_question_types": list(set(q["question_type"] for q in session.question_set)),
        "sample_units": sorted(set(q.get("unit") for q in session.question_set)),
        "sample_correct_counts": [
            {"tag": r.tag, "correct_count": r.correct_count, "tier": tiers.get(r.tag, 1)}
            for r in unit_records
        ],
    }


@router.get("/api/progress/{user_id}")
def get_progress(user_id: int, db: Session = Depends(get_db)):
    user = get_user(db, user_id)
    user_unit = user.current_unit
    graduated_units = get_graduated_units(db, user_id)
    all_records = get_collapsed_progress(db, user_id)
    record_map = {r.tag: r for r in all_records}

    unit_progress = {}
    for unit_str in unit_questions.keys():
        unit = int(unit_str)
        unit_tags = unit_to_vocab_tags_dict.get(unit, set())
        print("number of unit tags: ", len(unit_tags))
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

    current_unit_tags = unit_to_vocab_tags_dict.get(user_unit, set())
    current_unit_tiers = get_tiers_for_tags(db, user_id, current_unit_tags)
    current_unit_words = sorted([
        {
            "tag": tag,
            "correct_count": record_map[tag].correct_count if tag in record_map else 0,
            "tier": current_unit_tiers.get(tag, 1),
        }
        for tag in current_unit_tags
    ], key=lambda x: x["tag"])

    return {
        "user_id": user_id,
        "current_unit": user_unit,
        "graduated_units": list(graduated_units),
        "unit_progress": unit_progress,
        "current_unit_words": current_unit_words,
        "review_due_word_count": review_due_word_count(db, user_id),
        "review_due_tomorrow_word_count": review_due_tomorrow_word_count(db, user_id),
    }


@router.get("/api/unit_detail/{user_id}/{unit}")
def unit_detail(user_id: int, unit: int, db: Session = Depends(get_db)):
    user = get_user(db, user_id)
    graduated_units = get_graduated_units(db, user_id)
    unlocked = (unit == user.current_unit) or (unit in graduated_units)
    if not unlocked:
        return {"unit": unit, "locked": True, "words": []}

    unit_tags = unit_to_vocab_tags_dict.get(unit, set())
    tiers = get_tiers_for_tags(db, user_id, unit_tags)

    rows = {}
    for r in get_progress_by_user(db, user_id):
        if r.tag in unit_tags and r.facet in ("character", "pinyin"):
            rows[(r.tag, r.facet)] = r

    now = datetime.utcnow()
    is_current = unit == user.current_unit

    def facet_detail(tag, facet):
        r = rows.get((tag, facet))
        if not r:
            return {"correct_count": 0, "stability": None, "strength": None,
                    "is_review_eligible": False, "is_due": False}
        strength = 0.5 ** ((now - r.last_practice).total_seconds() / 86400 / r.stability)
        # a current-unit word is never review-eligible regardless of tier/count,
        # since its own unit isn't finished (mirrors _all_review_eligible_facets)
        eligible = (not is_current) and is_facet_review_eligible(
            tiers.get(tag, 1), r.correct_count)
        return {
            "correct_count": r.correct_count,
            "stability": round(r.stability, 2),
            "strength": round(strength, 3),
            "is_review_eligible": eligible,
            "is_due": eligible and strength < REVIEW_THRESHOLD,
        }

    words = sorted([
        {
            "tag": tag,
            "tier": tiers.get(tag, 1),
            "character": facet_detail(tag, "character"),
            "pinyin": facet_detail(tag, "pinyin"),
        }
        for tag in unit_tags
    ], key=lambda w: w["tag"])

    return {
        "unit": unit,
        "locked": False,
        "is_current": unit == user.current_unit,
        "is_graduated": unit in graduated_units,
        "words": words,
    }


@router.post("/api/tips")
def save_tip_endpoint(payload: dict = Body(...), db: Session = Depends(get_db)):
    key_type = payload.get("key_type")
    key_value = payload.get("key_value")
    tip_text = payload.get("tip")
    try:
        return save_tip(db, key_type, key_value, tip_text)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/lookup/{hanzi}")
def lookup(hanzi: str):
    if hanzi in hsk1_dictionary:
        entry = hsk1_dictionary[hanzi]
        return {"hanzi": hanzi, "pinyin": entry["pinyin"], "english": entry["english"]}

    from pypinyin import pinyin, Style
    try:
        result = pinyin(hanzi, style=Style.TONE3, heteronym=False)
        py = ''.join([s[0] for s in result]).lower()
    except Exception:
        py = None
    return {"hanzi": hanzi, "pinyin": py, "english": None}