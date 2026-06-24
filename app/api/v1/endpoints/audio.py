from fastapi import APIRouter
from fastapi.responses import FileResponse
import edge_tts
import hashlib
import os
import random
import base64
from fastapi.responses import JSONResponse

router = APIRouter()

CACHE_DIR = "audio_cache"
os.makedirs(CACHE_DIR, exist_ok=True)

MANDARIN_VOICES = [
    "zh-CN-XiaoxiaoNeural",   # female
    "zh-CN-YunxiNeural",      # male
    "zh-CN-XiaoyiNeural",     # female
    "zh-CN-YunyangNeural",    # male
]

# maps text -> (filepath, voice) so we don't re-generate with a different voice
audio_cache = {}  # text -> filepath
session_files = set()  # filepaths generated this session


async def generate_and_cache_audio(text: str):
    if text in audio_cache:
        return audio_cache[text]

    voice = random.choice(MANDARIN_VOICES)
    filename = hashlib.md5(text.encode("utf-8")).hexdigest() + ".mp3"
    filepath = os.path.join(CACHE_DIR, filename)

    if not os.path.exists(filepath):
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(filepath)

    audio_cache[text] = filepath
    session_files.add(filepath)
    return filepath


def clear_session_audio():
    """Delete all audio files generated this session."""
    for filepath in list(session_files):
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
            # remove from in-memory cache too
            for text, fp in list(audio_cache.items()):
                if fp == filepath:
                    del audio_cache[text]
        except Exception as e:
            print(f"Failed to delete {filepath}: {e}")
    session_files.clear()


@router.post("/api/audio")
async def audio(payload: dict):
    filepath = await generate_and_cache_audio(payload["text"])
    with open(filepath, "rb") as f:
        audio_data = base64.b64encode(f.read()).decode("utf-8")
    return JSONResponse({"audio": audio_data})

@router.post("/api/audio/clear")
async def clear_audio():
    """Call this when the session ends to clean up audio files."""
    count = len(session_files)
    clear_session_audio()
    return {"deleted": count}