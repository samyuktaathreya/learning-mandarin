# app/core/clerk_auth.py
import os
from clerk_backend_api import Clerk
from starlette.requests import Request

clerk = Clerk(bearer_auth=os.environ["CLERK_SECRET_KEY"])

async def clerk_auth_middleware(request: Request, call_next):
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.removeprefix("Bearer ")
        try:
            claims = clerk.verify_token(token)  # check exact method name in current SDK docs
            request.state.clerk_user_id = claims["sub"]
        except Exception:
            request.state.clerk_user_id = None  # invalid token -> treat as guest, don't hard-fail
    else:
        request.state.clerk_user_id = None  # no token -> guest

    return await call_next(request)