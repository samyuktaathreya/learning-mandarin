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
import asyncio
import time

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

PINYIN_OVERRIDES = {
    "谁": "shei2",
}

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


# ----------------------------- STT HELPERS -----------------------------

def to_numbered_pinyin(text: str) -> str:
    if text in PINYIN_OVERRIDES:
        return PINYIN_OVERRIDES[text]

    result = pinyin(text, style=Style.TONE3, heteronym=False)
    parts = []
    for i, syllables in enumerate(result):
        char = text[i] if i < len(text) else ''
        if char in PINYIN_OVERRIDES:
            parts.append(PINYIN_OVERRIDES[char])
        else:
            parts.append(syllables[0])
    return ''.join(parts).lower()


VALID_INITIALS = ['zh', 'ch', 'sh', 'b', 'p', 'm', 'f', 'd', 't', 'n', 'l',
                   'g', 'k', 'h', 'j', 'q', 'x', 'r', 'z', 'c', 's', 'y', 'w']

VALID_FINALS = ['iang', 'iong', 'uang', 'ueng', 'uan', 'uen', 'uai', 'ing',
                'ang', 'eng', 'ong', 'ian', 'iao', 'ie', 'in', 'an', 'en',
                'ao', 'ou', 'ai', 'ei', 'ia', 'ua', 'uo', 'ui', 'un', 'iu',
                've', 'vn', 'a', 'o', 'e', 'i', 'u', 'v', 'er', 'ng']


def split_pinyin_syllables(p: str) -> list:
    result = []
    i = 0
    p = p.lower()

    while i < len(p):
        if not (p[i].isalpha() or p[i] == 'v'):
            i += 1
            continue

        matched = False
        for init_len in [2, 1, 0]:
            if matched:
                break
            initial = p[i:i + init_len] if init_len > 0 else ''
            if init_len > 0 and (i + init_len > len(p) or initial not in VALID_INITIALS):
                continue
            rest_start = i + init_len
            for final in sorted(VALID_FINALS, key=len, reverse=True):
                end = rest_start + len(final)
                if p[rest_start:end] == final:
                    syllable = initial + final
                    if end < len(p) and p[end] in '12345':
                        result.append((syllable, p[end]))
                        i = end + 1
                    else:
                        result.append((syllable, '5'))
                        i = end
                    matched = True
                    break
        if not matched:
            i += 1

    return result


def apply_tone_sandhi(syllables: list) -> list:
    result = list(syllables)
    for i in range(len(result) - 1):
        base, tone = result[i]
        _, next_tone = result[i + 1]
        if base == 'bu' and tone == '4' and next_tone == '4':
            result[i] = (base, '2')
        elif base == 'yi' and tone == '1':
            if next_tone == '4':
                result[i] = (base, '2')
            elif next_tone in ('1', '2', '3'):
                result[i] = (base, '4')
        elif tone == '3' and next_tone == '3':
            result[i] = (base, '2')
    return result


def tones_match(t_pinyin: str, e_pinyin: str) -> bool:
    t_sylls = split_pinyin_syllables(t_pinyin)
    e_sylls = split_pinyin_syllables(e_pinyin)
    if len(t_sylls) != len(e_sylls):
        return False
    t_sandhi = apply_tone_sandhi(t_sylls)
    e_sandhi = apply_tone_sandhi(e_sylls)
    for (t_base, t_tone), (e_base, e_tone) in zip(t_sandhi, e_sandhi):
        if t_base != e_base:
            return False
        if e_tone == '5':
            continue
        if t_tone != e_tone:
            return False
    return True


# ----------------------------- STT (Azure) -----------------------------

def transcribe_with_azure(audio_path: str, expected: str = "") -> str:
    speech_config = speechsdk.SpeechConfig(
        subscription=AZURE_SPEECH_KEY,
        region=AZURE_SPEECH_REGION
    )
    speech_config.speech_recognition_language = "zh-CN"
    speech_config.set_property(
        speechsdk.PropertyId.SpeechServiceConnection_InitialSilenceTimeoutMs, "5000"
    )
    speech_config.set_property(
        speechsdk.PropertyId.SpeechServiceConnection_EndSilenceTimeoutMs, "5000"
    )

    audio_config = speechsdk.audio.AudioConfig(filename=audio_path)
    recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config,
        audio_config=audio_config
    )

    # use continuous recognition for multi-clause sentences, recognize_once otherwise
    has_comma = '，' in expected or ',' in expected

    if has_comma:
        import threading
        results = []
        done = threading.Event()

        def handle_result(evt):
            if evt.result.text:
                results.append(evt.result.text.strip())

        def handle_stop(evt):
            done.set()

        recognizer.recognized.connect(handle_result)
        recognizer.session_stopped.connect(handle_stop)
        recognizer.canceled.connect(handle_stop)

        recognizer.start_continuous_recognition()
        done.wait(timeout=15)
        recognizer.stop_continuous_recognition()

        result_text = ''.join(results)
    else:
        result = recognizer.recognize_once()
        print(f"Azure STT reason: {result.reason}, text: '{result.text}'")
        if result.reason == speechsdk.ResultReason.RecognizedSpeech:
            result_text = result.text.strip()
        elif result.reason == speechsdk.ResultReason.NoMatch:
            print(f"NoMatch details: {result.no_match_details}")
            result_text = ""
        elif result.reason == speechsdk.ResultReason.Canceled:
            details = speechsdk.CancellationDetails.from_result(result)
            print(f"Canceled: {details.reason}, error: {details.error_details}")
            result_text = ""
        else:
            result_text = ""

    print(f"Azure STT final: '{result_text}'")
    return result_text


@router.post("/api/transcribe")
async def transcribe(payload: dict):
    audio_b64 = payload.get("audio")
    expected = payload.get("expected", "").strip()

    if not audio_b64:
        return JSONResponse({"error": "No audio provided"}, status_code=400)

    audio_bytes = base64.b64decode(audio_b64)
    webm_path = os.path.join(CACHE_DIR, "temp_recording.webm")
    wav_path = os.path.join(CACHE_DIR, "temp_recording.wav")

    with open(webm_path, "wb") as f:
        f.write(audio_bytes)

    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", webm_path, "-ar", "16000", "-ac", "1", wav_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await proc.wait()

        wav_size = os.path.getsize(wav_path) if os.path.exists(wav_path) else 0
        webm_size = os.path.getsize(webm_path) if os.path.exists(webm_path) else 0
        print(f"webm size: {webm_size} bytes, wav size: {wav_size} bytes")

        if not os.path.exists(wav_path):
            return JSONResponse({"error": "Audio conversion failed"}, status_code=500)

        transcription_hanzi = await asyncio.to_thread(transcribe_with_azure, wav_path, expected)

        expected_pinyin = (
            to_numbered_pinyin(expected)
            if any('\u4e00' <= c <= '\u9fff' for c in expected)
            else expected.lower().replace(' ', '').replace(',', '')
        )

        if not transcription_hanzi:
            return JSONResponse({
                "transcription": "",
                "transcription_pinyin": "",
                "expected_pinyin": expected_pinyin,
                "is_correct": False,
                "hallucination": True
            })

        expected_char_count = len(expected.replace(' ', ''))
        transcription_char_count = len(transcription_hanzi.replace(' ', ''))
        if expected_char_count > 0 and transcription_char_count > expected_char_count * 3:
            return JSONResponse({
                "transcription": transcription_hanzi,
                "transcription_pinyin": "",
                "expected_pinyin": expected_pinyin,
                "is_correct": False,
                "hallucination": True
            })

        transcription_pinyin = to_numbered_pinyin(transcription_hanzi)
        is_correct = tones_match(transcription_pinyin, expected_pinyin)

        return JSONResponse({
            "transcription": transcription_hanzi,
            "transcription_pinyin": transcription_pinyin,
            "expected_pinyin": expected_pinyin,
            "is_correct": is_correct
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
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


# ----------------------------- CHINESE ANSWER GRADING -----------------------------

@router.post("/api/grade_chinese")
async def grade_chinese(payload: dict):
    """
    Grades a Chinese character answer by comparing pinyin instead of characters.
    Handles homophones like 他/她/它 (all ta1) that are written differently
    but pronounced identically.
    Expected payload: { "user_answer": "他是学生", "expected_answer": "她是学生" }
    """
    user_answer = payload.get("user_answer", "").strip()
    expected = payload.get("expected_answer", "").strip()

    if not user_answer or not expected:
        return JSONResponse({"is_correct": False})

    # strip punctuation from both sides
    import re
    def strip_punct(s):
        return re.sub(r'[。？！，、；：""''…]', '', s)

    user_pinyin = to_numbered_pinyin(strip_punct(user_answer))
    expected_pinyin = to_numbered_pinyin(strip_punct(expected))

    is_correct = tones_match(user_pinyin, expected_pinyin)

    return JSONResponse({
        "is_correct": is_correct,
        "user_pinyin": user_pinyin,
        "expected_pinyin": expected_pinyin,
    })

# ----------------------------- GET PINYIN -----------------------------
@router.post("/api/pinyin")
async def get_pinyin(payload: dict):
    text = payload.get("text", "")
    return JSONResponse({"pinyin": to_numbered_pinyin(text)})