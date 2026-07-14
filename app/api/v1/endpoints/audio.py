from fastapi import APIRouter
from fastapi.responses import JSONResponse
import edge_tts
import hashlib
import os
import random
import base64
from dotenv import load_dotenv
import azure.cognitiveservices.speech as speechsdk
import asyncio

from pinyin_utils import strip_punct, to_numbered_pinyin, tones_match, grade_speaking_sentence

load_dotenv(os.path.join(os.path.dirname(__file__), '../../../.env'))

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

AZURE_SPEECH_KEY = os.environ.get("AZURE_SPEECH_KEY")
AZURE_SPEECH_REGION = os.environ.get("AZURE_REGION", "eastus")

# --- Pronunciation assessment tuning ---
# Empirically (see spike): correct tone scores highest, wrong tones drop 10-23
# points, but the bands overlap across characters (correct 是 = 87, correct 岁 = 96).
# So accuracy alone is a *soft* tone signal. We gate on BOTH a high accuracy
# score (catches mangled consonants/vowels) AND tones_match (exact tone check).
ACCURACY_THRESHOLD = 90

# Only speaking vocab goes through pronunciation assessment; longer answers
# stay on the transcription path.
ASSESSMENT_QUESTION_TYPES = {"speaking vocab"}

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


# --------------- PRONUNCIATION ASSESSMENT (single words) ---------------

def assess_pronunciation_with_azure(audio_path: str, reference_text: str) -> dict:
    """
    Score the audio AGAINST the known reference text, rather than asking Azure
    to guess what was said. Sidesteps homophone/near-homophone confusion
    (ji vs zhi, sui vs shui): Azure can't hand back the wrong character because
    it isn't choosing one.

    Returns: {recognized, accuracy, phonemes: [{phoneme, accuracy}], error?}
    """
    speech_config = speechsdk.SpeechConfig(
        subscription=AZURE_SPEECH_KEY,
        region=AZURE_SPEECH_REGION,
    )
    speech_config.speech_recognition_language = "zh-CN"
    speech_config.set_property(
        speechsdk.PropertyId.SpeechServiceConnection_EndSilenceTimeoutMs, "3000"
    )

    audio_config = speechsdk.audio.AudioConfig(filename=audio_path)
    recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config,
        audio_config=audio_config,
    )

    reference_text = strip_punct(reference_text)

    pron_config = speechsdk.PronunciationAssessmentConfig(
        reference_text=reference_text,
        grading_system=speechsdk.PronunciationAssessmentGradingSystem.HundredMark,
        granularity=speechsdk.PronunciationAssessmentGranularity.Phoneme,
        enable_miscue=False,
    )
    pron_config.apply_to(recognizer)

    result = recognizer.recognize_once_async().get()

    if result.reason == speechsdk.ResultReason.Canceled:
        details = result.cancellation_details
        print(f"Assessment canceled: {details.reason}, error: {details.error_details}")
        return {"error": "canceled"}

    if result.reason != speechsdk.ResultReason.RecognizedSpeech:
        print(f"Assessment: no speech recognized ({result.reason})")
        return {"error": "no_speech"}

    out = {"recognized": strip_punct(result.text or "")}

    # The typed wrapper can KeyError on unexpected response shapes
    # (Azure-Samples issue #1763), so guard it.
    try:
        pa = speechsdk.PronunciationAssessmentResult(result)
        out["accuracy"] = pa.accuracy_score
        out["phonemes"] = [
            {"phoneme": p.phoneme, "accuracy": p.accuracy_score}
            for w in (pa.words or [])
            for p in (w.phonemes or [])
        ]
    except Exception as e:
        print(f"Assessment wrapper error: {type(e).__name__}: {e}")
        return {"error": "wrapper_failed", "recognized": out["recognized"]}

    print(f"Assessment: ref={reference_text!r} heard={out['recognized']!r} "
          f"accuracy={out['accuracy']}")
    for p in out["phonemes"]:
        print(f"  phoneme {p['phoneme']!r} acc={p['accuracy']}")

    return out


# ----------------------------- STT (Azure) -----------------------------

def transcribe_with_azure(audio_path: str, expected: str = "") -> str:
    """Transcription path -- still used for multi-word / sentence answers."""
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

    expected_hanzi = strip_punct(expected)
    is_long = ('，' in expected or ',' in expected or len(expected_hanzi) > 4)

    if is_long:
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
        done.wait(timeout=30)
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
            details = result.cancellation_details
            print(f"Canceled: {details.reason}, error: {details.error_details}")
            result_text = ""
        else:
            result_text = ""

    print(f"Azure STT final: '{result_text}'")
    return result_text


@router.post("/api/transcribe")
async def transcribe(payload: dict):
    audio_b64 = payload.get("audio")
    expected = payload.get("expected", "").strip()        # pinyin
    hanzi = payload.get("hanzi", "").strip()              # characters (assessment reference)
    question_type = payload.get("question_type", "").strip()

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

        expected_pinyin = (
            to_numbered_pinyin(expected)
            if any('\u4e00' <= c <= '\u9fff' for c in expected)
            else expected.lower().replace(' ', '').replace(',', '')
        )

        # ---------- SINGLE WORD: pronunciation assessment ----------
        if question_type in ASSESSMENT_QUESTION_TYPES and hanzi:
            assessment = await asyncio.to_thread(
                assess_pronunciation_with_azure, wav_path, hanzi
            )

            if assessment.get("error") in ("canceled", "no_speech"):
                return JSONResponse({
                    "transcription": "",
                    "transcription_pinyin": "",
                    "expected_pinyin": expected_pinyin,
                    "is_correct": False,
                    "hallucination": True,
                    "mode": "assessment",
                })

            if assessment.get("error") == "wrapper_failed":
                return JSONResponse({
                    "error": "Assessment failed",
                    "mode": "assessment",
                }, status_code=500)

            accuracy = assessment.get("accuracy") or 0
            recognized = assessment.get("recognized", "")
            transcription_pinyin = to_numbered_pinyin(recognized) if recognized else ""

            accuracy_ok = accuracy >= ACCURACY_THRESHOLD
            tone_ok = bool(transcription_pinyin) and tones_match(transcription_pinyin, expected_pinyin)
            is_correct = accuracy_ok and tone_ok

            if is_correct:
                feedback = "correct"
            elif not accuracy_ok and not tone_ok:
                feedback = "sound_and_tone"
            elif not accuracy_ok:
                feedback = "sound"
            else:
                feedback = "tone"

            phonemes = assessment.get("phonemes", [])
            weakest = min(phonemes, key=lambda p: p["accuracy"]) if phonemes else None

            return JSONResponse({
                "transcription": recognized,
                "transcription_pinyin": transcription_pinyin,
                "expected_pinyin": expected_pinyin,
                "is_correct": is_correct,
                "mode": "assessment",
                "accuracy": accuracy,
                "accuracy_threshold": ACCURACY_THRESHOLD,
                "accuracy_ok": accuracy_ok,
                "tone_ok": tone_ok,
                "feedback": feedback,
                "phonemes": phonemes,
                "weakest_phoneme": weakest,
            })

        # ---------- MULTI-WORD / SENTENCE: transcription path ----------
        transcription_hanzi = await asyncio.to_thread(transcribe_with_azure, wav_path, expected)

        if not transcription_hanzi:
            return JSONResponse({
                "transcription": "",
                "transcription_pinyin": "",
                "expected_pinyin": expected_pinyin,
                "is_correct": False,
                "hallucination": True,
                "mode": "transcription",
            })

        expected_char_count = len(expected.replace(' ', ''))
        transcription_char_count = len(transcription_hanzi.replace(' ', ''))
        if expected_char_count > 0 and transcription_char_count > expected_char_count * 3:
            return JSONResponse({
                "transcription": transcription_hanzi,
                "transcription_pinyin": "",
                "expected_pinyin": expected_pinyin,
                "is_correct": False,
                "hallucination": True,
                "mode": "transcription",
            })

        transcription_pinyin = to_numbered_pinyin(transcription_hanzi)  # for display

        if hanzi:
            is_correct = grade_speaking_sentence(transcription_hanzi, hanzi)
        else:
            is_correct = tones_match(transcription_pinyin, expected_pinyin)

        return JSONResponse({
            "transcription": transcription_hanzi,
            "transcription_pinyin": transcription_pinyin,
            "expected_pinyin": expected_pinyin,
            "is_correct": is_correct,
            "mode": "transcription",
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        for path in [webm_path, wav_path]:
            if os.path.exists(path):
                os.remove(path)