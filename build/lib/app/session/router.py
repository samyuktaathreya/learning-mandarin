from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Body, HTTPException
from sqlalchemy.orm import Session
from textbook.database import get_textbook_db
from session.database import get_db
from session.schemas import SessionResponse
from session.crud import get_user, get_tiers_for_tags, get_graduated_units, get_progress_by_user
from session.constants import GRADUATION_THRESHOLD, REVIEW_THRESHOLD
from session.services.progress import (
    get_collapsed_progress,
    is_unit_graduated,
    build_unit_progress_summary,
    build_unit_words_detail,
)
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
from textbook import services as textbook_services
from textbook.database import get_textbook_db
from characters.database import get_characters_db

router = APIRouter()


@router.get("/api/generate_session/{user_id}", response_model=SessionResponse)
def generate_session(user_id: int, mode: str = "sentence", skip_review: bool = False,
                      db: Session = Depends(get_db),
                      characters_db: Session = Depends(get_characters_db),
                      textbook_db: Session = Depends(get_textbook_db)):
    return generate_full_session(db, characters_db, textbook_db, user_id, mode=mode, skip_review=skip_review)


@router.patch("/api/submit_session/{user_id}")
def submit_session(
    user_id: int,
    list_of_question_data: list[dict] = Body(...),
    is_correct: list[bool] = Body(...),
    is_unit_test: bool = Body(...),
    mode: str = Body("sentence"),
    db: Session = Depends(get_db),
    textbook_db: Session = Depends(get_textbook_db),
):
    return process_submission(db, textbook_db, user_id, list_of_question_data, is_correct, is_unit_test, mode)


@router.get("/api/debug/{user_id}")
def debug(user_id: int, db: Session = Depends(get_db),
          textbook_db: Session = Depends(get_textbook_db)):
    user = get_user(db, user_id)
    hsk_level = getattr(user, "hsk_level", 1)
    unit_tags = textbook_services.get_unit_vocab_tags(textbook_db, user.current_unit, hsk_level)
    all_records = get_collapsed_progress(db, user_id)
    unit_records = [r for r in all_records if r.tag in unit_tags]
    graduated = is_unit_graduated(db, user_id, unit_records, unit_tags)
    session = generate_practice_session(db, textbook_db, user_id, user.current_unit)
    tiers = get_tiers_for_tags(db, user_id, unit_tags)

    from session.services.review_engine import _all_review_eligible_facets, _due_review_facets
    eligible = _all_review_eligible_facets(db, textbook_db, user_id, hsk_level)
    due = _due_review_facets(db, textbook_db, user_id, hsk_level)

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
def get_progress(user_id: int, db: Session = Depends(get_db),
                  textbook_db: Session = Depends(get_textbook_db)):
    user = get_user(db, user_id)
    user_unit = user.current_unit
    graduated_units = get_graduated_units(db, user_id)

    # was: an inline loop over unit_questions.keys() / unit_to_vocab_tags_dict --
    # moved to session/services/progress.py (see build_unit_progress_summary),
    # since it's per-unit progress aggregation, not routing.
    unit_progress = build_unit_progress_summary(db, textbook_db, user_id)
    for unit_str, entry in unit_progress.items():
        entry["is_graduated"] = entry["unit"] in graduated_units
        entry["is_current"] = entry["unit"] == user_unit

    current_unit_tags = textbook_services.get_unit_vocab_tags(textbook_db, user_unit, getattr(user, "hsk_level", 1))
    current_unit_tiers = get_tiers_for_tags(db, user_id, current_unit_tags)
    all_records = get_collapsed_progress(db, user_id)
    record_map = {r.tag: r for r in all_records}
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
        "review_due_word_count": review_due_word_count(db, textbook_db, user_id),
        "review_due_tomorrow_word_count": review_due_tomorrow_word_count(db, textbook_db, user_id),
    }


@router.get("/api/unit_detail/{user_id}/{unit}")
def unit_detail(user_id: int, unit: int, db: Session = Depends(get_db),
                 textbook_db: Session = Depends(get_textbook_db)):
    user = get_user(db, user_id)
    graduated_units = get_graduated_units(db, user_id)
    unlocked = (unit == user.current_unit) or (unit in graduated_units)
    if not unlocked:
        return {"unit": unit, "locked": True, "words": []}

    is_current = unit == user.current_unit
    # was: an inline facet_detail closure -- moved to
    # session/services/progress.py (see build_unit_words_detail).
    words = build_unit_words_detail(db, textbook_db, user_id, unit, is_current)

    return {
        "unit": unit,
        "locked": False,
        "is_current": is_current,
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
def lookup(
    hanzi: str,
    unit: Optional[int] = None,
    hsk_level: int = 1,
    textbook_db: Session = Depends(get_textbook_db),
):
    """
    Was: `inline `if hanzi in hsk1_dictionary: ... else: pypinyin
    fallback`` -- moved to textbook/services.py (see lookup_word), since
    dictionary lookup is a textbook-data concern, not a routing concern.

    NOW SENSE-AWARE: a word can have several taught meanings (see
    textbook.models.VocabSense), so this endpoint accepts optional `unit`
    (and `hsk_level`) query params -- pass the caller's current position in
    the curriculum (e.g. `current_user.current_unit`, or the unit the
    sentence being read belongs to) so a multi-sense word resolves to
    whichever meaning is actually relevant there, rather than always the
    word's overall primary sense. Omit them for a plain "what does this
    word generally mean" lookup.

    The response now also carries `other_definitions` -- every OTHER
    taught (or dictionary) meaning the word has, beyond the relevant one
    already in `pinyin`/`english`, so the frontend can show the relevant
    definition first and the rest underneath instead of picking one and
    hiding everything else, or dumping every definition on the learner at
    once. Empty for the common single-sense word.

    Example response for a multi-sense word:
        {
          "hanzi": "还", "pinyin": "hai2", "english": "still/also",
          "unit": 5, "hsk_level": 1,
          "other_definitions": [
            {"hanzi": "还", "pinyin": "huan2", "english": "to return (something)",
             "unit": 20, "hsk_level": 1, "word_type": "vocab"}
          ]
        }
    """
    return textbook_services.lookup_word(textbook_db, hanzi, unit_number=unit, hsk_level=hsk_level)

@router.get("/api/sentence_tags/{sentence_id}")
def get_sentence_tags(sentence_id: int, textbook_db: Session = Depends(get_textbook_db)):
    """Returns all vocab tags for a sentence with their definitions,
    preferring context_definition if available."""
    from textbook.models import Sentence, SentenceVocab
    
    sentence = textbook_db.query(Sentence).filter(Sentence.id == sentence_id).first()
    if not sentence:
        return {"sentence_id": sentence_id, "tags": {}}
    
    # Get all SentenceVocab links for this sentence
    links = (
        textbook_db.query(SentenceVocab)
        .filter(SentenceVocab.sentence_id == sentence_id)
        .order_by(SentenceVocab.position)
        .all()
    )
    
    tags = {}
    for link in links:
        vocab = link.vocab
        if not vocab:
            continue
        
        # Prefer context_definition if it's non-empty, otherwise use english
        english = vocab.english or "UNKNOWN_ENGLISH"
        if link.context_definition and link.context_definition.strip():
            english = link.context_definition
        
        tags[vocab.hanzi] = {
            "pinyin": vocab.pinyin or "UNKNOWN_PINYIN",
            "english": english,
            "context_definition": link.context_definition,  # expose it if the frontend wants to see both
        }
    
    return {"sentence_id": sentence_id, "tags": tags}