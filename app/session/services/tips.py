from datetime import datetime

from sqlalchemy.orm import Session

from models.user import QuestionTip
from session.schemas import SessionResponse


def attach_tips(db: Session, session_response: SessionResponse) -> SessionResponse:
    texts = set()
    for q in session_response.question_set:
        if q.get("question"):
            texts.add(q["question"])
        if q.get("answer"):
            texts.add(q["answer"])
    if not texts:
        return session_response

    rows = db.query(QuestionTip).filter(QuestionTip.key_value.in_(texts)).all()
    tip_map = {(r.key_type, r.key_value): r.tip for r in rows}

    for q in session_response.question_set:
        tip = tip_map.get(("question", q.get("question")))
        if tip is None:
            tip = tip_map.get(("answer", q.get("answer")))
        if tip is not None:
            q["tip"] = tip

    return session_response


def save_tip(db: Session, key_type: str, key_value: str, tip_text: str) -> dict:
    """Upsert a tip keyed on (key_type, key_value). Raises ValueError on bad input."""
    key_value = (key_value or "").strip()
    tip_text = (tip_text or "").strip()

    if key_type not in ("question", "answer"):
        raise ValueError("key_type must be 'question' or 'answer'")
    if not key_value or not tip_text:
        raise ValueError("key_value and tip are required")

    row = db.query(QuestionTip).filter(
        QuestionTip.key_type == key_type,
        QuestionTip.key_value == key_value,
    ).first()
    if row:
        row.tip = tip_text
        row.updated_at = datetime.utcnow()
    else:
        row = QuestionTip(key_type=key_type, key_value=key_value, tip=tip_text)
        db.add(row)
    db.commit()

    return {"key_type": key_type, "key_value": key_value, "tip": tip_text}