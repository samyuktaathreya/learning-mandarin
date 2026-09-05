# core/session_auth.py
import os, time, jwt
from fastapi import Request, HTTPException, Header
from fastapi.responses import JSONResponse
from typing import Optional
from app.core.config.shared import settings

SECRET = settings.SESSION_SECRET
PUBLIC_PATHS = {"/", "/api/auth/verify", "/api/docs", "/api/openapi.json"}

def issue_session() -> str:
    return jwt.encode({"exp": int(time.time()) + 3600}, SECRET, algorithm="HS256")

async def session_middleware(request: Request, call_next):
    path = request.url.path
    if request.method == "OPTIONS" or path in PUBLIC_PATHS or path.startswith("/api/static"):
        return await call_next(request)
    token = request.cookies.get("session")
    if not token:
        return JSONResponse({"detail": "No session"}, status_code=401)
    try:
        jwt.decode(token, SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return JSONResponse({"detail": "Invalid session"}, status_code=401)
    return await call_next(request)