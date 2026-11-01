# app/VoicePractice/voice_log.py
"""
Dedicated log file for voice-practice conversations, for debugging.

One line per event, both from the backend (session setup, feedback) and from
the browser (realtime events, posted to /api/voice-session/log). Every line
carries the conversation's session id, so one conversation can be followed:

    grep a1b2c3d4e5f6 logs/voice_practice.log

Format:
    2026-10-31 16:05:12,345 [a1b2c3d4e5f6] server session.created {"user_id": 3, ...}
    2026-10-31 16:05:14,010 [a1b2c3d4e5f6] client transcript.user {"transcript": "我有姐姐"}

Chinese is written as-is (not \\u escapes) so the file is readable.
The file rotates at 5 MB, keeping 3 old copies.
"""
import json
import logging
import os
from logging.handlers import RotatingFileHandler

LOG_DIR = os.environ.get("VOICE_LOG_DIR", "logs")
LOG_PATH = os.path.join(LOG_DIR, "voice_practice.log")
MAX_DATA_CHARS = 4000  # per line; big events (e.g. session.updated with instructions) get truncated

_logger = logging.getLogger("voice_practice")

if not _logger.handlers:
    os.makedirs(LOG_DIR, exist_ok=True)
    _handler = RotatingFileHandler(LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    _handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    _logger.addHandler(_handler)
    _logger.setLevel(logging.INFO)
    _logger.propagate = False  # keep these out of the main app log


def log_event(session_id: str, source: str, event_type: str, data=None, client_time: str = None):
    """source is "server" or "client". client_time is the browser's timestamp,
    included because client events arrive in batches a few seconds late."""
    payload = ""
    if data is not None:
        payload = json.dumps(data, ensure_ascii=False, default=str)
        if len(payload) > MAX_DATA_CHARS:
            payload = payload[:MAX_DATA_CHARS] + f"... [truncated, {len(payload)} chars]"
    when = f" @{client_time}" if client_time else ""
    _logger.info(f"[{session_id or '-'}] {source} {event_type}{when} {payload}".rstrip())