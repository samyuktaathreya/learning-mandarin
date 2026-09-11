from fastapi import Request, Depends, Header
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from auth.crud import get_or_create_user, get_or_create_guest, merge_guest_into_user
from auth.models import User


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    x_guest_id: str | None = Header(None, alias="X-Guest-Id"),
) -> User:
    clerk_id = getattr(request.state, "clerk_user_id", None)

    if clerk_id:
        if x_guest_id:
            return merge_guest_into_user(db, guest_id=x_guest_id, clerk_id=clerk_id)
        return get_or_create_user(db, clerk_id=clerk_id)

    if x_guest_id:
        return get_or_create_guest(db, guest_id=x_guest_id)

    return get_or_create_guest(db, guest_id="anonymous-fallback")