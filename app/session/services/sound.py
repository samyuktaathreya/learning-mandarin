from textbook.services import get_pinyin
from pinyin_utils import split_pinyin_sounds, GATED_INITIALS, GATED_FINALS
from sqlalchemy.orm import Session

def _tag_sounds(db: Session, tag: str) -> set:
    p = get_pinyin(db, tag)
    if not p:
        return set()
    sounds = set()
    for initial, final, _tone in split_pinyin_sounds(p):
        if initial in GATED_INITIALS:
            sounds.add(initial)
        if final in GATED_FINALS:
            sounds.add(final)
    return sounds