import os
import hashlib
import random
import asyncio
import base64
import uuid
import azure.cognitiveservices.speech as speechsdk
import edge_tts
from sqlalchemy.orm import Session
from pinyin_utils import strip_punct, to_numbered_pinyin, tones_match, grade_speaking_sentence
from core.config.shared import settings
from app.core.logger import logger

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

AZURE_SPEECH_KEY = settings.AZURE_SPEECH_KEY
AZURE_SPEECH_REGION = settings.AZURE_SPEECH_REGION

# --- Pronunciation assessment tuning ---
ACCURACY_THRESHOLD = 90
ASSESSMENT_QUESTION_TYPES = {"speaking vocab"}


# ----------------------------- TTS -----------------------------

async def generate_and_cache_audio(text: str, slow: bool = False) -> str:
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


def clear_session_audio() -> int:
    count = len(session_files)
    for filepath in list(session_files):
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
            for key, fp in list(audio_cache.items()):
                if fp == filepath:
                    del audio_cache[key]
        except Exception as e:
            logger.debug(f"Failed to delete {filepath}: {e}")
    session_files.clear()
    return count


# --------------- PRONUNCIATION ASSESSMENT (single words) ---------------

def assess_pronunciation_with_azure(audio_path: str, reference_text: str) -> dict:
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
        logger.debug(f"Assessment canceled: {details.reason}, error: {details.error_details}")
        return {"error": "canceled"}

    if result.reason != speechsdk.ResultReason.RecognizedSpeech:
        logger.debug(f"Assessment: no speech recognized ({result.reason})")
        return {"error": "no_speech"}

    out = {"recognized": strip_punct(result.text or "")}

    try:
        pa = speechsdk.PronunciationAssessmentResult(result)
        out["accuracy"] = pa.accuracy_score
        out["phonemes"] = [
            {"phoneme": p.phoneme, "accuracy": p.accuracy_score}
            for w in (pa.words or [])
            for p in (w.phonemes or [])
        ]
    except Exception as e:
        logger.debug(f"Assessment wrapper error: {type(e).__name__}: {e}")
        return {"error": "wrapper_failed", "recognized": out["recognized"]}

    logger.debug(f"Assessment: ref={reference_text!r} heard={out['recognized']!r} accuracy={out.get('accuracy')}")
    return out


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
        if result.reason == speechsdk.ResultReason.RecognizedSpeech:
            result_text = result.text.strip()
        else:
            result_text = ""

    return result_text


# ------------------------- FULL PIPELINE -------------------------

async def process_spoken_audio(audio_bytes: bytes, expected: str, hanzi: str, question_type: str, db: Session) -> dict:
    """Handles FFmpeg conversion, decides between assessment/transcription, and grades the result."""
    # Use unique filenames to prevent concurrency overwrites
    temp_id = uuid.uuid4().hex
    webm_path = os.path.join(CACHE_DIR, f"temp_{temp_id}.webm")
    wav_path = os.path.join(CACHE_DIR, f"temp_{temp_id}.wav")

    with open(webm_path, "wb") as f:
        f.write(audio_bytes)

    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", webm_path, "-ar", "16000", "-ac", "1", wav_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await proc.wait()

        if not os.path.exists(wav_path):
            raise Exception("Audio conversion failed")

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
                return {
                    "transcription": "",
                    "transcription_pinyin": "",
                    "expected_pinyin": expected_pinyin,
                    "is_correct": False,
                    "hallucination": True,
                    "mode": "assessment",
                }

            if assessment.get("error") == "wrapper_failed":
                raise Exception("Assessment failed")

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

            return {
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
            }

        # ---------- MULTI-WORD / SENTENCE: transcription path ----------
        transcription_hanzi = await asyncio.to_thread(transcribe_with_azure, wav_path, expected)

        if not transcription_hanzi:
            return {
                "transcription": "",
                "transcription_pinyin": "",
                "expected_pinyin": expected_pinyin,
                "is_correct": False,
                "hallucination": True,
                "mode": "transcription",
            }

        expected_char_count = len(expected.replace(' ', ''))
        transcription_char_count = len(transcription_hanzi.replace(' ', ''))
        if expected_char_count > 0 and transcription_char_count > expected_char_count * 3:
            return {
                "transcription": transcription_hanzi,
                "transcription_pinyin": "",
                "expected_pinyin": expected_pinyin,
                "is_correct": False,
                "hallucination": True,
                "mode": "transcription",
            }

        transcription_pinyin = to_numbered_pinyin(transcription_hanzi)

        if hanzi:
            is_correct = grade_speaking_sentence(transcription_hanzi, hanzi, db)
        else:
            is_correct = tones_match(transcription_pinyin, expected_pinyin)
            
        return {
            "transcription": transcription_hanzi,
            "transcription_pinyin": transcription_pinyin,
            "expected_pinyin": expected_pinyin,
            "is_correct": is_correct,
            "mode": "transcription",
        }

    finally:
        for path in [webm_path, wav_path]:
            if os.path.exists(path):
                os.remove(path)