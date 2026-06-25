from fastapi import APIRouter
from fastapi.responses import JSONResponse
import edge_tts
import hashlib
import os
import random
import base64
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

router = APIRouter()

CACHE_DIR = "audio_cache"
os.makedirs(CACHE_DIR, exist_ok=True)

MANDARIN_VOICES = [
    "zh-CN-XiaoxiaoNeural",
    "zh-CN-YunxiNeural",
    "zh-CN-XiaoyiNeural",
    "zh-CN-YunyangNeural",
]

audio_cache = {}      # text -> filepath
session_files = set() # filepaths generated this session

openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))


# ----------------------------- TTS -----------------------------

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
    for filepath in list(session_files):
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
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
    count = len(session_files)
    clear_session_audio()
    return {"deleted": count}


# ----------------------------- STT (Whisper) -----------------------------

@router.post("/api/transcribe")
async def transcribe(payload: dict):
    """
    Receives a base64-encoded audio blob from the frontend,
    sends it to Whisper, and returns the transcription.
    Expected payload: { "audio": "<base64 string>", "expected": "<hanzi or pinyin>" }
    """
    audio_b64 = payload.get("audio")
    expected = payload.get("expected", "")

    if not audio_b64:
        return JSONResponse({"error": "No audio provided"}, status_code=400)

    # decode base64 -> bytes -> temp file
    audio_bytes = base64.b64decode(audio_b64)
    temp_path = os.path.join(CACHE_DIR, "temp_recording.webm")
    with open(temp_path, "wb") as f:
        f.write(audio_bytes)

    try:
        with open(temp_path, "rb") as f:
            result = openai_client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language="zh",  # force Mandarin
                response_format="text"
            )
        transcription = result.strip()
    except Exception as e:
        traceback.print_exc()  # prints full traceback to uvicorn terminal
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    # simple match: does the transcription contain the expected answer?
    is_correct = expected.strip() in transcription or transcription in expected.strip()

    return JSONResponse({
        "transcription": transcription,
        "expected": expected,
        "is_correct": is_correct
    })


# ----------------------------- AI GRADING -----------------------------

import anthropic as anthropic_sdk

anthropic_client = anthropic_sdk.Anthropic(
    api_key=os.environ.get("CLAUDE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
)

@router.post("/api/grade")
async def grade_answer(payload: dict):
    """
    Uses Claude to check if a student's English translation is correct.
    Expected payload: { "user_answer": "...", "expected_answer": "..." }
    Returns: { "is_correct": true/false }
    """
    user_answer = payload.get("user_answer", "").strip()
    expected = payload.get("expected_answer", "").strip()

    if not user_answer or not expected:
        return JSONResponse({"is_correct": False})

    try:
        response = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",  # cheapest, fast enough for grading
            max_tokens=5,
            messages=[{
                "role": "user",
                "content": f'Does this student answer mean the same thing as the expected answer? Reply only YES or NO.\n\nExpected: {expected}\nStudent: {user_answer}'
            }]
        )
        result = response.content[0].text.strip().upper()
        if result == "YES":
            return JSONResponse({"is_correct": True})
        elif result == "NO":
            return JSONResponse({"is_correct": False})
        else:
            return JSONResponse({"is_correct": False})  # unexpected response -> mark wrong
    except Exception as e:
        print(f"Grading error: {e}")
        return JSONResponse({"is_correct": False})