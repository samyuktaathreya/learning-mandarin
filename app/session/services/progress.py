from sqlalchemy.orm import Session
from datetime import datetime
from session import crud
from textbook.services import get_unit_vocab_tags, get_all_unit_numbers
from session.crud import get_tiers_for_tags, get_progress_by_user
from session.constants import GRADUATION_THRESHOLD, REVIEW_THRESHOLD
from session.services.review_engine import is_facet_review_eligible


class _CollapsedRecord:
    __slots__ = ("tag", "correct_count", "stability", "last_practice")

    def __init__(self, tag, correct_count, stability, last_practice):
        self.tag = tag
        self.correct_count = correct_count
        self.stability = stability
        self.last_practice = last_practice


def collapse_facets(records):
    """[(tag,facet)-rows] -> [one _CollapsedRecord per tag], min across facets."""
    by_tag = {}
    for r in records:
        by_tag.setdefault(r.tag, []).append(r)
    collapsed = []
    for tag, rows in by_tag.items():
        collapsed.append(_CollapsedRecord(
            tag=tag,
            correct_count=min(r.correct_count for r in rows),
            stability=min(r.stability for r in rows),
            last_practice=min(r.last_practice for r in rows),
        ))
    return collapsed


def get_collapsed_progress(db: Session, user_id: int):
    return collapse_facets(crud.get_progress_by_user(db, user_id))


def is_unit_graduated(db: Session, user_id: int, tag_records: list, unit_tags: set) -> bool:
    from session.constants import GRADUATION_THRESHOLD, MAX_TIER

    record_map = {r.tag: r for r in tag_records}
    tiers = crud.get_tiers_for_tags(db, user_id, unit_tags)
    for tag in unit_tags:
        record = record_map.get(tag)
        if not record or record.correct_count < GRADUATION_THRESHOLD:
            return False
        if tiers.get(tag, 1) < MAX_TIER:
            return False
    return True

def build_unit_progress_summary(db: Session, textbook_db: Session, user_id: int) -> dict:
    """Replaces the per-unit loop that used to live directly in
    router.py's /api/progress, walking `unit_questions.keys()` and
    `unit_to_vocab_tags_dict`. Same output shape as before: {unit_str: {...}}.
    """
    from session.services.progress import get_collapsed_progress  # avoid circular import at module load
 
    all_records = get_collapsed_progress(db, user_id)
    record_map = {r.tag: r for r in all_records}
 
    unit_progress = {}
    for unit in get_all_unit_numbers(textbook_db):
        unit_tags = get_unit_vocab_tags(textbook_db, unit)
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
 
        unit_progress[str(unit)] = {
            "unit": unit,
            "total_tags": total,
            "graduated_tags": graduated_tags,
            "progress_pct": round(graduated_tags / total * 100) if total > 0 else 0,
            "avg_correct_count": round(avg_correct, 1),
        }
 
    return unit_progress
 
 
def build_unit_words_detail(db: Session, textbook_db: Session, user_id: int, unit: int,
                             is_current: bool) -> list:
    """Replaces the facet_detail closure + word-list assembly that used to
    live directly in router.py's /api/unit_detail. Returns the sorted list
    of per-word {tag, tier, character, pinyin} dicts; the router still owns
    locked/is_graduated framing since that needs `user.current_unit` and
    `graduated_units`, which are request-shaped, not curriculum-shaped.
 
    is_current is passed in (rather than recomputed here) because a
    current-unit word is never review-eligible regardless of tier/count --
    same rule the original inline closure used -- and the router already
    knows whether this unit == user.current_unit, so there's no need to
    duplicate that check inside the service function.
    """
    unit_tags = get_unit_vocab_tags(textbook_db, unit)
    tiers = get_tiers_for_tags(db, user_id, unit_tags)
 
    rows = {}
    for r in get_progress_by_user(db, user_id):
        if r.tag in unit_tags and r.facet in ("character", "pinyin"):
            rows[(r.tag, r.facet)] = r
 
    now = datetime.utcnow()
 
    def facet_detail(tag: str, facet: str) -> dict:
        r = rows.get((tag, facet))
        if not r:
            return {"correct_count": 0, "stability": None, "strength": None,
                    "is_review_eligible": False, "is_due": False}
        strength = 0.5 ** ((now - r.last_practice).total_seconds() / 86400 / r.stability)
        eligible = (not is_current) and is_facet_review_eligible(
            tiers.get(tag, 1), r.correct_count)
        return {
            "correct_count": r.correct_count,
            "stability": round(r.stability, 2),
            "strength": round(strength, 3),
            "is_review_eligible": eligible,
            "is_due": eligible and strength < REVIEW_THRESHOLD,
        }
 
    return sorted([
        {
            "tag": tag,
            "tier": tiers.get(tag, 1),
            "character": facet_detail(tag, "character"),
            "pinyin": facet_detail(tag, "pinyin"),
        }
        for tag in unit_tags
    ], key=lambda w: w["tag"])