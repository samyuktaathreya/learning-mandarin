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
    """
    Resolves the Clerk user (if any) for this request.
    - No/invalid/expired token -> guest (clerk_user_id = None), NOT a 401.
      Guests are allowed in this app, so a missing or bad token should
      just mean "no identity attached," not a blocked request.
    - Valid token -> request.state.clerk_user_id set from claims.

    jwt_key enables networkless verification: the JWT signature is checked
    locally against Clerk's public key instead of round-tripping to Clerk's
    API on every request. Matters here since this runs on every request.
    """
    request.state.clerk_user_id = None

    try:
        request_state = clerk.authenticate_request(
            request,
            AuthenticateRequestOptions(
                authorized_parties=["http://localhost:5173", "https://wenku.app", "https://www.wenku.app"],
                jwt_key=CLERK_JWT_KEY,
            ),
        )
        if request_state.is_signed_in:
            request.state.clerk_user_id = request_state.payload["sub"]
    except Exception:
        pass  # any failure -> stays None -> guest

    return await call_next(request)