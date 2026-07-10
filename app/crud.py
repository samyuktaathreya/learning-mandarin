from sqlalchemy.orm import Session
from models.user import StrengthTable, User, SoundProgress
from datetime import datetime

SOUND_UNLOCK_SUCCESSES = 1
SOUND_UNLOCK_ATTEMPTS_CAP = 5


def get_progress_by_user(db: Session, user_id: int):
    return db.query(StrengthTable).filter(StrengthTable.user_id == user_id).all()


def get_strength_row(db: Session, user_id: int, tag: str):
    return db.query(StrengthTable).filter(
        StrengthTable.user_id == user_id,
        StrengthTable.tag == tag,
    ).first()


def update_after_answer(db: Session, user_id: int, tag: str, is_correct: bool):
    row = get_strength_row(db, user_id, tag)
    if not row:
        return {"tag": tag, "error": "not found"}

    if is_correct:
        row.correct_count += 1
        row.stability = min(row.stability * 2, 365)
    else:
        row.stability = max(row.stability * 0.5, 1)

    row.last_practice = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return {"tag": tag, "correct_count": row.correct_count, "stability": row.stability}


def get_user(db: Session, user_id: int):
    return db.query(User).filter(User.id == user_id).first()


def update_user_unit(db: Session, user_id: int, new_unit: int):
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        user.current_unit = new_unit
        db.commit()
        db.refresh(user)
    return user


def graduate_unit(db: Session, user_id: int, unit: int):
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        graduated = set(user.graduated_units.split(",")) if user.graduated_units else set()
        graduated.discard("")
        graduated.add(str(unit))
        user.graduated_units = ",".join(graduated)
        user.current_unit = unit + 1
        db.commit()
        db.refresh(user)
    return user


def get_graduated_units(db: Session, user_id: int) -> set:
    user = get_user(db, user_id)
    if not user or not user.graduated_units:
        return set()
    return {int(u) for u in user.graduated_units.split(",") if u}


def get_sound_progress(db: Session, user_id: int):
    return db.query(SoundProgress).filter(SoundProgress.user_id == user_id).all()


def get_unlocked_sounds(db: Session, user_id: int) -> set:
    """A sound with no row yet has never been attempted, so it stays locked."""
    rows = get_sound_progress(db, user_id)
    return {
        r.sound for r in rows
        if r.successes >= SOUND_UNLOCK_SUCCESSES or r.attempts >= SOUND_UNLOCK_ATTEMPTS_CAP
    }


def record_sound_attempt(db: Session, user_id: int, sound: str, is_correct: bool):
    row = db.query(SoundProgress).filter(
        SoundProgress.user_id == user_id,
        SoundProgress.sound == sound,
    ).first()
    if not row:
        row = SoundProgress(user_id=user_id, sound=sound, attempts=0, successes=0)
        db.add(row)

    row.attempts += 1
    if is_correct:
        row.successes += 1

    db.commit()
    db.refresh(row)
    return row