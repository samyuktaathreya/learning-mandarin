# app/core/deps.py
from fastapi import Request

def get_current_user_id(request: Request) -> str | None:
    return getattr(request.state, "clerk_user_id", None)

def get_current_user(request: Request, db: Session = Depends(get_db)):
    clerk_id = getattr(request.state, "clerk_user_id", None)
    if clerk_id:
        return get_or_create_user(db, clerk_id)
    else:
        return get_or_create_guest(db, request)