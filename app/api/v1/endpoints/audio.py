from fastapi import APIRouter
from fastapi.responses import JSONResponse
import edge_tts
import hashlib
import os
import random
import base64
from openai import OpenAI
from dotenv import load_dotenv
from pypinyin import pinyin, Style
import re
import anthropic as anthropic_sdk
import azure.cognitiveservices.speech as speechsdk
import tempfile
import asyncio

load_dotenv(os.path.join(os.path.dirname(__file__), '../../../language-app-data/.env'))

router = APIRouter()

CACHE_DIR = "audio_cache"
os.makedirs(CACHE_DIR, exist_ok=True)

MANDARIN_VOICES = [
    "zh-CN-XiaoxiaoNeural",
    "zh-CN-YunxiNeural",
    "zh-CN-XiaoyiNeural",
    "zh-CN-YunyangNeural",
]

audio_cache = {}
session_files = set()

openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
anthropic_client = anthropic_sdk.Anthropic(
    api_key=os.environ.get("CLAUDE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
)

AZURE_SPEECH_KEY = os.environ.get("AZURE_SPEECH_KEY")
AZURE_SPEECH_REGION = os.environ.get("AZURE_SPEECH_REGION", "eastus")

# ----------------------------- TTS -----------------------------

async def generate_and_cache_audio(text: str, slow: bool = False):
    cache_key = f"{text}_slow" if slow else text
    if cache_key in audio_cache:
        return audio_cache[cache_key]

    voice = random.choice(MANDARIN_VOICES)
    filename = hashlib.md5(cache_key.encode("utf-8")).hexdigest() + ".mp3"
    filepath = os.path.join(CACHE_DIR, filename)

    if not os.path.exists(filepath):
        rate = "-30%" if slow else "+0%"
        communicate = edge_tts.Communicate(text, voice, rate=rate)
        await communicate.save(filepath)

    audio_cache[cache_key] = filepath
    session_files.add(filepath)
    return filepath


def clear_session_audio():
    for filepath in list(session_files):
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
            for key, fp in list(audio_cache.items()):
                if fp == filepath:
                    del audio_cache[key]
        except Exception as e:
            print(f"Failed to delete {filepath}: {e}")
    session_files.clear()


@router.post("/api/audio")
async def audio(payload: dict):
    text = payload["text"]
    slow = payload.get("slow", False)
    filepath = await generate_and_cache_audio(text, slow=slow)
    with open(filepath, "rb") as f:
        audio_data = base64.b64encode(f.read()).decode("utf-8")
    return JSONResponse({"audio": audio_data})


@router.post("/api/audio/clear")
async def clear_audio():
    count = len(session_files)
    clear_session_audio()
    return {"deleted": count}


# ----------------------------- STT (Azure) -----------------------------

def to_numbered_pinyin(text: str) -> str:
    result = pinyin(text, style=Style.TONE3, heteronym=False)
    return ''.join([syllable[0] for syllable in result]).lower()


def tones_match(t_pinyin: str, e_pinyin: str) -> bool:
    t_sylls = re.findall(r'[a-zü]+[1-5]?', t_pinyin)
    e_sylls = re.findall(r'[a-zü]+[1-5]?', e_pinyin)

    t_sylls = [s for s in t_sylls if s]
    e_sylls = [s for s in e_sylls if s]

    if len(t_sylls) != len(e_sylls):
        return False

    for t, e in zip(t_sylls, e_sylls):
        e_tone = e[-1] if e[-1].isdigit() else '5'
        t_tone = t[-1] if t[-1].isdigit() else '5'
        e_base = e[:-1] if e[-1].isdigit() else e
        t_base = t[:-1] if t[-1].isdigit() else t

        if e_base != t_base:
            return False
        if e_tone != '5' and t_tone != e_tone:
            return False

    return True


def transcribe_with_azure(audio_path: str) -> str:
    """
    Transcribe audio file using Azure Speech SDK.
    Returns recognized text in Chinese characters.
    """
    speech_config = speechsdk.SpeechConfig(
        subscription=AZURE_SPEECH_KEY,
        region=AZURE_SPEECH_REGION
    )
    speech_config.speech_recognition_language = "zh-CN"

    audio_config = speechsdk.audio.AudioConfig(filename=audio_path)
    recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config,
        audio_config=audio_config
    )

    result = recognizer.recognize_once()

    if result.reason == speechsdk.ResultReason.RecognizedSpeech:
        return result.text.strip()
    elif result.reason == speechsdk.ResultReason.NoMatch:
        return ""
    else:
        raise RuntimeError(f"Azure STT failed: {result.reason}")


@router.post("/api/transcribe")
async def transcribe(payload: dict):
    audio_b64 = payload.get("audio")
    expected = payload.get("expected", "").strip()

    if not audio_b64:
        return JSONResponse({"error": "No audio provided"}, status_code=400)

    audio_bytes = base64.b64decode(audio_b64)

    # Azure SDK needs a wav file — save webm then convert via ffmpeg
    webm_path = os.path.join(CACHE_DIR, "temp_recording.webm")
    wav_path = os.path.join(CACHE_DIR, "temp_recording.wav")

    with open(webm_path, "wb") as f:
        f.write(audio_bytes)

    try:
        # convert webm -> wav using ffmpeg (available in codespaces)
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", webm_path, "-ar", "16000", "-ac", "1", wav_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await proc.wait()

        if not os.path.exists(wav_path):
            return JSONResponse({"error": "Audio conversion failed"}, status_code=500)

        # run Azure STT in a thread so we don't block the event loop
        transcription_hanzi = await asyncio.to_thread(transcribe_with_azure, wav_path)

        if not transcription_hanzi:
            return JSONResponse({
                "transcription": "",
                "transcription_pinyin": "",
                "expected_pinyin": to_numbered_pinyin(expected) if any('\u4e00' <= c <= '\u9fff' for c in expected) else expected.lower().replace(' ', ''),
                "is_correct": False,
                "hallucination": True
            })

        # sanity check
        expected_char_count = len(expected.replace(' ', ''))
        transcription_char_count = len(transcription_hanzi.replace(' ', ''))
        if expected_char_count > 0 and transcription_char_count > expected_char_count * 3:
            return JSONResponse({
                "transcription": transcription_hanzi,
                "transcription_pinyin": "",
                "expected_pinyin": to_numbered_pinyin(expected) if any('\u4e00' <= c <= '\u9fff' for c in expected) else expected.lower().replace(' ', ''),
                "is_correct": False,
                "hallucination": True
            })

        transcription_pinyin = to_numbered_pinyin(transcription_hanzi)
        expected_pinyin = (
            to_numbered_pinyin(expected)
            if any('\u4e00' <= c <= '\u9fff' for c in expected)
            else expected.lower().replace(' ', '')
        )
        is_correct = tones_match(transcription_pinyin, expected_pinyin)

        return JSONResponse({
            "transcription": transcription_hanzi,
            "transcription_pinyin": transcription_pinyin,
            "expected_pinyin": expected_pinyin,
            "is_correct": is_correct
        })

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        for path in [webm_path, wav_path]:
            if os.path.exists(path):
                os.remove(path)


# ----------------------------- AI GRADING -----------------------------

@router.post("/api/grade")
async def grade_answer(payload: dict):
    user_answer = payload.get("user_answer", "").strip()
    expected = payload.get("expected_answer", "").strip()

    if not user_answer or not expected:
        return JSONResponse({"is_correct": False})

    try:
        response = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=5,
            messages=[{
                "role": "user",
                "content": f'Does this student answer mean the same thing as the expected answer? Reply only YES or NO.\n\nExpected: {expected}\nStudent: {user_answer}'
            }]
        )
        result = response.content[0].text.strip().upper()
        return JSONResponse({"is_correct": result == "YES"})
    except Exception as e:
        print(f"Grading error: {e}")
        return JSONResponse({"is_correct": False})