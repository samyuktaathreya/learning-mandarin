# auth/crud.py
import uuid
from sqlalchemy.orm import Session
from auth.models import User


def get_or_create_user(db: Session, clerk_id: str, email: str | None = None) -> User:
    user = db.query(User).filter(User.clerk_user_id == clerk_id).first()
    if user:
        return user

    user = User(clerk_user_id=clerk_id, email=email, is_guest=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def delete_user_by_clerk_id(db: Session, clerk_id: str) -> None:
    user = db.query(User).filter(User.clerk_user_id == clerk_id).first()
    if user:
        db.delete(user)
        db.commit()


def get_or_create_guest(db: Session, guest_id: str) -> User:
    user = db.query(User).filter(User.guest_id == guest_id).first()
    if user:
        return user

    user = User(guest_id=guest_id, is_guest=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def merge_guest_into_user(db: Session, guest_id: str, clerk_id: str, email: str | None = None) -> User:
    """
    Called on first sign-in when a guest_id is present: transfers the guest's
    progress row onto a real Clerk-linked user instead of creating a fresh,
    empty one.
    """
    guest = db.query(User).filter(User.guest_id == guest_id, User.is_guest == True).first()
    existing = db.query(User).filter(User.clerk_user_id == clerk_id).first()

    if existing:
        return existing  # already linked, nothing to merge

    if guest:
        guest.clerk_user_id = clerk_id
        guest.email = email
        guest.is_guest = False
        db.commit()
        db.refresh(guest)
        return guest

    return get_or_create_user(db, clerk_id, email)