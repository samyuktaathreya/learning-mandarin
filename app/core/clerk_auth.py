# app/core/clerk_auth.py
import os
from clerk_backend_api import Clerk
from clerk_backend_api.security import authenticate_request
from clerk_backend_api.security.types import AuthenticateRequestOptions
from starlette.requests import Request
from app.core.config.shared import settings

CLERK_SECRET_KEY = settings.CLERK_SECRET_KEY
CLERK_JWT_KEY = settings.CLERK_JWT_KEY  # enables networkless (local) verification

clerk = Clerk(bearer_auth=CLERK_SECRET_KEY)


async def clerk_auth_middleware(request: Request, call_next):
    request.state.clerk_user_id = None

    try:
        request_state = clerk.authenticate_request(
            request,
            AuthenticateRequestOptions(
                authorized_parties=["http://localhost:5173", "https://wenku.app", "https://www.wenku.app"],
                secret_key=CLERK_SECRET_KEY,
            ),
        )
        if request_state.is_signed_in:
            request.state.clerk_user_id = request_state.payload["sub"]
        elif request.headers.get("authorization"):
            print("Clerk auth failed:", request_state.reason, flush=True)
    except Exception as e:
        print("Clerk auth error:", repr(e), flush=True)

    return await call_next(request)