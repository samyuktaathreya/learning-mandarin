"""
SPIKE: Azure Pronunciation Assessment test endpoint.

Purpose: answer two empirical questions we can't answer from docs.
  1. Does zh-CN accuracy_score penalize TONE errors? (say ji1 when ji3 expected)
  2. Does it penalize INITIAL errors? (say zhi3 when ji3 expected)
     -> this is the ji/zhi, sui/shui misrecognition problem

Method: record the same character three ways, assess each against the same
reference_text, and compare the scores. Also dumps the raw JSON so we can see
every field Azure returns for Mandarin (looking for anything tone-related).

This is throwaway code. Once we know the answer, the real implementation goes
into audio.py and this file gets deleted.

Wire up in main.py:
    from api.v1.endpoints.test_pronounciation import router as test_router
    app.include_router(test_router)
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
import azure.cognitiveservices.speech as speechsdk
from dotenv import load_dotenv
import os
import base64
import json
import asyncio
import traceback

load_dotenv(os.path.join(os.path.dirname(__file__), '../../../.env'))

router = APIRouter()

CACHE_DIR = "audio_cache"
os.makedirs(CACHE_DIR, exist_ok=True)

AZURE_SPEECH_KEY = os.environ.get("AZURE_SPEECH_KEY")
AZURE_SPEECH_REGION = os.environ.get("AZURE_REGION", "eastus")

# Microsoft's own convention in their language-learning sample:
# words scoring below 60 accuracy are treated as mispronunciations.
MISPRONUNCIATION_THRESHOLD = 60


def assess_pronunciation(audio_path: str, reference_text: str) -> dict:
    """
    Run Azure Pronunciation Assessment on a wav file against reference_text.
    Returns all scores plus the raw JSON response.
    """
    speech_config = speechsdk.SpeechConfig(
        subscription=AZURE_SPEECH_KEY,
        region=AZURE_SPEECH_REGION,
    )
    speech_config.speech_recognition_language = "zh-CN"

    # The assessment service uses a longer end-silence timeout than plain STT
    # (education scenarios have longer pauses). Keep it generous.
    speech_config.set_property(
        speechsdk.PropertyId.SpeechServiceConnection_EndSilenceTimeoutMs, "3000"
    )

    audio_config = speechsdk.audio.AudioConfig(filename=audio_path)
    recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config,
        audio_config=audio_config,
    )

    # For zh-CN the reference text must have no spaces.
    reference_text = reference_text.replace(" ", "")

    pron_config = speechsdk.PronunciationAssessmentConfig(
        reference_text=reference_text,
        grading_system=speechsdk.PronunciationAssessmentGradingSystem.HundredMark,
        granularity=speechsdk.PronunciationAssessmentGranularity.Phoneme,
        enable_miscue=False,   # miscue needs word segmentation; off for single words
    )
    # Prosody is documented as en-US only, but enable it and see what comes back.
    try:
        pron_config.enable_prosody_assessment()
    except Exception as e:
        print(f"[spike] enable_prosody_assessment failed (expected on zh-CN?): {e}")

    pron_config.apply_to(recognizer)

    result = recognizer.recognize_once_async().get()

    out = {
        "reference_text": reference_text,
        "reason": str(result.reason),
        "recognized_text": result.text or "",
    }

    if result.reason == speechsdk.ResultReason.Canceled:
        details = result.cancellation_details
        out["error"] = f"Canceled: {details.reason} | {details.error_details}"
        return out

    if result.reason != speechsdk.ResultReason.RecognizedSpeech:
        out["error"] = f"No speech recognized: {result.reason}"
        return out

    # Raw JSON first — this is the point of the spike. We want to see EVERY
    # field Azure returns for Mandarin, including anything tone-related that
    # the typed wrapper might not expose.
    raw_json = result.properties.get(speechsdk.PropertyId.SpeechServiceResponse_JsonResult)
    try:
        out["raw"] = json.loads(raw_json) if raw_json else None
    except Exception:
        out["raw"] = {"unparsed": raw_json}

    # The typed wrapper can KeyError on unexpected response shapes
    # (see Azure-Samples issue #1763), so guard it.
    try:
        pa = speechsdk.PronunciationAssessmentResult(result)
        out["scores"] = {
            "accuracy": pa.accuracy_score,
            "fluency": pa.fluency_score,
            "completeness": pa.completeness_score,
            "pronunciation": pa.pronunciation_score,
            "prosody": getattr(pa, "prosody_score", None),
        }
        out["words"] = [
            {
                "word": w.word,
                "accuracy": w.accuracy_score,
                "error_type": w.error_type,
                "phonemes": [
                    {"phoneme": p.phoneme, "accuracy": p.accuracy_score}
                    for p in (w.phonemes or [])
                ],
            }
            for w in (pa.words or [])
        ]
        out["verdict"] = (
            "PASS" if (pa.accuracy_score or 0) >= MISPRONUNCIATION_THRESHOLD else "FAIL"
        )
    except Exception as e:
        out["typed_wrapper_error"] = f"{type(e).__name__}: {e}"
        out["note"] = "Typed wrapper failed; read the 'raw' field instead."

    return out


@router.post("/api/test/pronunciation")
async def test_pronounciation(payload: dict):
    """
    Payload: { "audio": "<base64 webm>", "reference": "几" }
    Returns full assessment scores + raw JSON.
    """
    audio_b64 = payload.get("audio")
    reference = (payload.get("reference") or "").strip()

    if not audio_b64:
        return JSONResponse({"error": "No audio provided"}, status_code=400)
    if not reference:
        return JSONResponse({"error": "No reference text provided"}, status_code=400)

    webm_path = os.path.join(CACHE_DIR, "spike_recording.webm")
    wav_path = os.path.join(CACHE_DIR, "spike_recording.wav")

    with open(webm_path, "wb") as f:
        f.write(base64.b64decode(audio_b64))

    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", webm_path, "-ar", "16000", "-ac", "1", wav_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

        if not os.path.exists(wav_path):
            return JSONResponse({"error": "Audio conversion failed"}, status_code=500)

        result = await asyncio.to_thread(assess_pronunciation, wav_path, reference)

        # print server-side too, so you can watch scores while testing
        print("\n" + "=" * 60)
        print(f"[spike] reference={reference!r} recognized={result.get('recognized_text')!r}")
        if "scores" in result:
            for k, v in result["scores"].items():
                print(f"[spike]   {k:>14}: {v}")
            print(f"[spike]   {'verdict':>14}: {result.get('verdict')}")
        for w in result.get("words", []):
            print(f"[spike]   word {w['word']!r} acc={w['accuracy']} err={w['error_type']}")
            for p in w["phonemes"]:
                print(f"[spike]       phoneme {p['phoneme']!r:>10} acc={p['accuracy']}")
        if "typed_wrapper_error" in result:
            print(f"[spike]   WRAPPER ERROR: {result['typed_wrapper_error']}")
        print("=" * 60 + "\n")

        return JSONResponse(result)

    except Exception as e:
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        for path in (webm_path, wav_path):
            if os.path.exists(path):
                os.remove(path)