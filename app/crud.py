from sqlalchemy.orm import Session
from models.user import StrengthTable, User
from datetime import datetime


def get_progress_by_user(db: Session, user_id: int):
    return db.query(StrengthTable).filter(StrengthTable.user_id == user_id).all()


def get_strength_row(db: Session, user_id: int, tag: str):
    return db.query(StrengthTable).filter(
        StrengthTable.user_id == user_id,
        StrengthTable.tag == tag
    ).first()


def update_stability(db: Session, user_id: int, tag: str, is_correct: bool):
    row = get_strength_row(db, user_id, tag)

    if not row:
        return {"tag": tag, "error": "not found"}

    if is_correct:
        row.stability = min(row.stability * 2, 365)  # cap at 1 year
    else:
        row.stability = max(row.stability * 0.5, 1)  # floor at 1 day

    row.last_practice = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return {"tag": tag, "new_stability": row.stability}


def get_user(db: Session, user_id: int):
    return db.query(User).filter(User.id == user_id).first()


def update_user_unit(db: Session, user_id: int, new_unit: int):
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        user.current_unit = new_unit
        db.commit()
        db.refresh(user)
    return user