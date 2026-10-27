"""
shared/services/speech_token.py

Trades the Azure Speech key for a short-lived token the browser can use.
The key never leaves the server; the browser only ever sees the token.

Azure tokens last 10 minutes. One token is cached and shared by every
request for 9 of those minutes, so Azure's token service is called at
most about once every 9 minutes no matter how many users or tabs ask.
"""
import asyncio
import time

import aiohttp

from app.core.config.shared import settings

TOKEN_URL = (
    f"https://{settings.AZURE_SPEECH_REGION}.api.cognitive.microsoft.com"
    "/sts/v1.0/issueToken"
)
TOKEN_LIFETIME = 9 * 60  # seconds; Azure's are 10 min, refresh a minute early

_cached_token: str | None = None
_expires_at = 0.0  # time.monotonic() value
_lock = asyncio.Lock()  # so simultaneous requests don't all fetch at once


class SpeechTokenError(Exception):
    """Azure's token service refused the request or couldn't be reached."""


async def get_speech_token() -> tuple[str, int]:
    """Returns (token, seconds until the browser should fetch a new one)."""
    global _cached_token, _expires_at

    async with _lock:
        now = time.monotonic()
        if _cached_token and now < _expires_at:
            return _cached_token, int(_expires_at - now)

        headers = {"Ocp-Apim-Subscription-Key": settings.AZURE_SPEECH_KEY}
        timeout = aiohttp.ClientTimeout(total=5)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # Empty body on purpose: Azure wants a Content-Length even with no data.
                async with session.post(TOKEN_URL, headers=headers, data=b"") as res:
                    body = await res.text()
                    if res.status != 200:
                        raise SpeechTokenError(f"Azure returned {res.status}: {body[:200]}")
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise SpeechTokenError(f"Couldn't reach Azure: {type(e).__name__}: {e}") from e

        _cached_token = body.strip()
        _expires_at = time.monotonic() + TOKEN_LIFETIME
        return _cached_token, TOKEN_LIFETIME