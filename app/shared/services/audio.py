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
from core.logger import logger
import re
from shared.services.pinyin_audio import get_recording_url

import time

ASSESS_TIMEOUT = 5         # seconds, single syllable/word
TRANSCRIBE_TIMEOUT = 12    # seconds, sentences

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
PINYIN_ACCURACY_THRESHOLD = 80
ASSESSMENT_QUESTION_TYPES = {"speaking vocab", "speaking_pinyin"}

class ForcedPronunciationError(Exception):
    """Azure rejected or cancelled a forced-pronunciation request. Carries
    everything that was sent so the router can return it to the client."""
 
    def __init__(self, text: str, numbered_pinyin: str, voice: str, slow: bool,
                 ssml: str, reason, cancel_reason, error_details):
        self.text = text
        self.numbered_pinyin = numbered_pinyin
        self.voice = voice
        self.slow = slow
        self.ssml = ssml
        self.reason = str(reason)
        self.cancel_reason = str(cancel_reason)
        self.error_details = error_details
        super().__init__(f"Forced-pronunciation synthesis failed: {cancel_reason} - {error_details}")
 
    def to_dict(self) -> dict:
        return {
            "error": "forced_pronunciation_failed",
            "sent_to_azure": {
                "text": self.text,
                "ph": self.numbered_pinyin,
                "voice": self.voice,
                "slow": self.slow,
                "ssml": self.ssml,
            },
            "azure_response": {
                "reason": self.reason,
                "cancel_reason": self.cancel_reason,
                "error_details": self.error_details,
            },
        }
        
def normalize_sapi_pinyin(pinyin: str) -> str:
    """Azure's SAPI phoneme tag wants a space between each syllable and its
    tone digit. Callers send different formats ('ma3' from speaking
    questions, 'ma 3' from the progress popup), so normalize them all here:
      'ma3' -> 'ma 3',  'ni3hao3' -> 'ni 3 hao 3',  'ma 3' -> 'ma 3'
    """
    spaced = re.sub(r"([a-zü]+)\s*([0-5])", r"\1 \2 ", pinyin.strip().lower())
    return " ".join(spaced.split())

# ----------------------------- TTS -----------------------------

def get_rate(text: str, slow: bool) -> str:
    if not slow:
        return "+0%"
    # Single characters/syllables are over in a fraction of a second, so they
    # need the slowest setting to sound noticeably slower.
    return "-50%" if len(strip_punct(text)) <= 1 else "-30%"

TONE_CONTOURS = {
    "1": "(0%,+15%) (100%,+15%)",
    "2": "(0%,-10%) (100%,+30%)",
    "3": "(0%,-5%) (35%,-35%) (75%,-35%) (100%,+10%)",
    "4": "(0%,+35%) (55%,-25%) (100%,-40%)",   # fast/normal: steeper fall, reaches the bottom early
}

# Slower audio smooths pitch changes, so exaggerate the shapes a bit
SLOW_TONE_CONTOURS = {
    **TONE_CONTOURS,
    "4": "(0%,+45%) (40%,-30%) (100%,-45%)",
}

BAD_VOICES_BY_TONE = {
    "3": {"zh-CN-XiaoyiNeural"},   # <- replace with the voice from your logs
}

def pick_voice(seed_text: str, numbered_pinyin: str | None) -> str:
    seed = int(hashlib.md5(seed_text.encode("utf-8")).hexdigest(), 16)
    pool = MANDARIN_VOICES
    if numbered_pinyin:
        tone = numbered_pinyin.split()[-1]
        bad = BAD_VOICES_BY_TONE.get(tone, set())
        pool = [v for v in MANDARIN_VOICES if v not in bad] or MANDARIN_VOICES
    return pool[seed % len(pool)]

def get_contour_attr(numbered_pinyin: str, slow: bool = False) -> str:
    parts = numbered_pinyin.split()
    if len(parts) != 2:
        return ""
    table = SLOW_TONE_CONTOURS if slow else TONE_CONTOURS
    contour = table.get(parts[1])
    return f' contour="{contour}"' if contour else ""

async def generate_and_cache_audio(text: str, slow: bool = False, numbered_pinyin: str | None = None) -> str:
    """`numbered_pinyin` (e.g. 'ma 3') forces the exact tone via SSML phoneme forcing,
    for heteronyms like 阿 where the TTS engine would otherwise guess the reading."""
    # Normalize first, so the cache key, voice choice and SSML all see the same format
    if numbered_pinyin:
        numbered_pinyin = normalize_sapi_pinyin(numbered_pinyin)

    cache_key = f"{numbered_pinyin or text}_slow" if slow else (numbered_pinyin or text)
    if cache_key in audio_cache:
        return audio_cache[cache_key]

    # Same voice for normal + slow of the same item, so speed is the only difference.
    voice = pick_voice(numbered_pinyin or text, numbered_pinyin)

    filename = hashlib.md5(cache_key.encode("utf-8")).hexdigest() + ".mp3"
    filepath = os.path.join(CACHE_DIR, filename)

    # A failed synthesis leaves an empty file behind (Azure creates it before
    # it knows whether synthesis worked). Treat empty files as missing, and
    # delete them on failure, so a failed sound is retried next time instead
    # of being served as silence.
    if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
        try:
            if numbered_pinyin:
                await asyncio.to_thread(_synthesize_forced_pronunciation, text, numbered_pinyin, filepath, voice, slow)
            else:
                rate = get_rate(text, slow)
                communicate = edge_tts.Communicate(text, voice, rate=rate)
                await communicate.save(filepath)
        except Exception:
            if os.path.exists(filepath):
                os.remove(filepath)
            raise

    audio_cache[cache_key] = filepath
    session_files.add(filepath)
    return filepath

def _synthesize_forced_pronunciation(text: str, numbered_pinyin: str, filepath: str, voice: str, slow: bool = False):
    """Synthesizes `text` (hanzi) forced to the exact tone/reading in `numbered_pinyin`,
    via an SSML <phoneme> tag, so heteronyms (e.g. 阿) don't get read with the wrong tone."""
    speech_config = speechsdk.SpeechConfig(subscription=AZURE_SPEECH_KEY, region=AZURE_SPEECH_REGION)
    audio_config = speechsdk.audio.AudioOutputConfig(filename=filepath)
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)
    rate = get_rate(text, slow)
    contour = ' contour="(0%,-15%) (45%,-45%) (100%,+10%)"' if numbered_pinyin.endswith(" 3") else ""
 
    contour_attr = get_contour_attr(numbered_pinyin)

    ssml = f'''<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="zh-CN">
    <voice name="{voice}">
        <prosody rate="{rate}"{contour_attr}>
        <phoneme alphabet="sapi" ph="{numbered_pinyin}">{text}</phoneme>
        </prosody>
    </voice>
    </speak>'''

    print("ssml : ", ssml)

    # Logged on every forced request, not just failures, so a request that
    # "succeeds" but plays the wrong tone can still be checked against what
    # was actually sent.
    logger.info(f"Forced pronunciation request: text={text!r} ph={numbered_pinyin!r} voice={voice} slow={slow}")
 
    result = synthesizer.speak_ssml_async(ssml).get()
    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        details = getattr(result, "cancellation_details", None)
        err = ForcedPronunciationError(
            text=text,
            numbered_pinyin=numbered_pinyin,
            voice=voice,
            slow=slow,
            ssml=ssml,
            reason=result.reason,
            cancel_reason=getattr(details, "reason", None),
            error_details=getattr(details, "error_details", None),
        )
        logger.error(f"Forced-pronunciation TTS failed: {err.to_dict()}")
        raise err

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


# ----------------------------- AUDIO PREP -----------------------------

async def to_trimmed_pcm(audio_bytes: bytes) -> bytes:
    """webm (or anything ffmpeg reads) -> raw 16 kHz mono 16-bit PCM, with
    leading/trailing silence removed. All in memory, no temp files.
    Returns b"" if the recording was all silence."""
    trim = ("silenceremove=start_periods=1:start_silence=0.05:start_threshold=-45dB,"
            "areverse,"
            "silenceremove=start_periods=1:start_silence=0.05:start_threshold=-45dB,"
            "areverse")
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-loglevel", "error", "-i", "pipe:0",
        "-af", trim, "-ar", "16000", "-ac", "1", "-f", "s16le", "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    pcm, stderr = await proc.communicate(audio_bytes)
    if proc.returncode != 0:
        logger.error(f"ffmpeg conversion failed: {stderr.decode(errors='replace')[-500:]}")
        raise Exception("Audio conversion failed")
    return pcm


def _pcm_audio_config(pcm: bytes) -> speechsdk.audio.AudioConfig:
    """Hands the whole recording to Azure as a finished stream. Closing the
    stream tells Azure there's no more audio coming, so it doesn't wait."""
    fmt = speechsdk.audio.AudioStreamFormat(samples_per_second=16000, bits_per_sample=16, channels=1)
    stream = speechsdk.audio.PushAudioInputStream(stream_format=fmt)
    stream.write(pcm)
    stream.close()
    return speechsdk.audio.AudioConfig(stream=stream)


# --------------- PRONUNCIATION ASSESSMENT (single words) ---------------

def assess_pronunciation_pcm(pcm: bytes, reference_text: str) -> dict:
    speech_config = speechsdk.SpeechConfig(subscription=AZURE_SPEECH_KEY, region=AZURE_SPEECH_REGION)
    speech_config.speech_recognition_language = "zh-CN"
    speech_config.set_property(
        speechsdk.PropertyId.SpeechServiceConnection_EndSilenceTimeoutMs, "800"
    )

    recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config,
        audio_config=_pcm_audio_config(pcm),
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

def transcribe_with_azure(pcm: bytes, expected: str = "") -> str:
    speech_config = speechsdk.SpeechConfig(subscription=AZURE_SPEECH_KEY, region=AZURE_SPEECH_REGION)
    speech_config.speech_recognition_language = "zh-CN"
    speech_config.set_property(
        speechsdk.PropertyId.SpeechServiceConnection_InitialSilenceTimeoutMs, "5000"
    )
    speech_config.set_property(
        speechsdk.PropertyId.SpeechServiceConnection_EndSilenceTimeoutMs, "5000"
    )

    recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config,
        audio_config=_pcm_audio_config(pcm),
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

        return ''.join(results)

    result = recognizer.recognize_once()
    if result.reason == speechsdk.ResultReason.RecognizedSpeech:
        return result.text.strip()
    return ""


# ------------------------- FULL PIPELINE -------------------------

async def process_spoken_audio(audio_bytes: bytes, expected: str, hanzi: str, question_type: str, db: Session) -> dict:
    """Converts + trims the recording in memory, decides between assessment/transcription, and grades the result."""
    t_start = time.perf_counter()

    expected_pinyin = (
        to_numbered_pinyin(expected)
        if any('\u4e00' <= c <= '\u9fff' for c in expected)
        else expected.lower().replace(' ', '').replace(',', '')
    )
    is_assessment = question_type in ASSESSMENT_QUESTION_TYPES and bool(hanzi)
    mode = "assessment" if is_assessment else "transcription"

    def no_speech_result(transcription: str = "") -> dict:
        return {
            "transcription": transcription,
            "transcription_pinyin": "",
            "expected_pinyin": expected_pinyin,
            "is_correct": False,
            "hallucination": True,
            "mode": mode,
        }

    def timeout_result() -> dict:
        return {
            "transcription": "",
            "transcription_pinyin": "",
            "expected_pinyin": expected_pinyin,
            "is_correct": False,
            "mode": mode,
            "feedback": "timeout",
        }

    pcm = await to_trimmed_pcm(audio_bytes)
    t_converted = time.perf_counter()

    if not pcm:
        # The whole recording was below the silence threshold
        return no_speech_result()

    def log_timing():
        t_done = time.perf_counter()
        logger.info(
            f"[grading] {mode} convert={t_converted - t_start:.2f}s "
            f"azure={t_done - t_converted:.2f}s audio={len(pcm) / 32000:.2f}s"
        )

    # ---------- SINGLE WORD: pronunciation assessment ----------
    if is_assessment:
        try:
            assessment = await asyncio.wait_for(
                asyncio.to_thread(assess_pronunciation_pcm, pcm, hanzi),
                timeout=ASSESS_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning(f"[grading] assessment timed out after {ASSESS_TIMEOUT}s for {hanzi!r}")
            return timeout_result()
        finally:
            log_timing()

        if assessment.get("error") in ("canceled", "no_speech"):
            return no_speech_result()

        if assessment.get("error") == "wrapper_failed":
            raise Exception("Assessment failed")

        accuracy = assessment.get("accuracy") or 0
        recognized = assessment.get("recognized", "")
        transcription_pinyin = to_numbered_pinyin(recognized) if recognized else ""

        threshold = PINYIN_ACCURACY_THRESHOLD if question_type == "speaking_pinyin" else ACCURACY_THRESHOLD
        accuracy_ok = accuracy >= threshold
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
            "accuracy_threshold": threshold,
            "accuracy_ok": accuracy_ok,
            "tone_ok": tone_ok,
            "feedback": feedback,
            "phonemes": phonemes,
            "weakest_phoneme": weakest,
        }

    # ---------- MULTI-WORD / SENTENCE: transcription path ----------
    try:
        transcription_hanzi = await asyncio.wait_for(
            asyncio.to_thread(transcribe_with_azure, pcm, expected),
            timeout=TRANSCRIBE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning(f"[grading] transcription timed out after {TRANSCRIBE_TIMEOUT}s for {expected!r}")
        return timeout_result()
    finally:
        log_timing()

    if not transcription_hanzi:
        return no_speech_result()

    expected_char_count = len(expected.replace(' ', ''))
    transcription_char_count = len(transcription_hanzi.replace(' ', ''))
    if expected_char_count > 0 and transcription_char_count > expected_char_count * 3:
        return no_speech_result(transcription_hanzi)

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

# ----------------------------- USING REAL AUDIO FILES (NOT TTS) -----------------------------
async def get_audio(text: str, slow: bool = False, numbered_pinyin: str | None = None,
                    prefer_recording: bool = False) -> dict:
    """Recording URL for pinyin syllables when one exists, otherwise base64 TTS."""
    if prefer_recording:
        url = await get_recording_url(numbered_pinyin or text, slow)
        if url:
            return {"url": url, "source": "recording"}
        print(f"[pinyin audio] No recording for {numbered_pinyin or text!r}, falling back to TTS")
        print()

    filepath = await generate_and_cache_audio(text, slow=slow, numbered_pinyin=numbered_pinyin)
    with open(filepath, "rb") as f:
        result = {"audio": base64.b64encode(f.read()).decode("utf-8")}
    if prefer_recording:
        result["source"] = "tts"
    return result