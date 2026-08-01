from sqlalchemy.orm import Session

from session import crud


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