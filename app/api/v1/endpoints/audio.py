from fastapi import APIRouter
from fastapi.responses import JSONResponse
import edge_tts
import hashlib
import os
import random
import base64
from dotenv import load_dotenv
from pypinyin import pinyin, Style
import re
import anthropic as anthropic_sdk
import azure.cognitiveservices.speech as speechsdk
import asyncio
import time
import json

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

PINYIN_OVERRIDES = {
    "谁": "shei2",
}

# Single-character pinyin from the curated index. Word-keyed multi-char entries
# are skipped — per-character grading only needs single characters, and pypinyin
# handles those correctly as a fallback.
CHAR_PINYIN = {}
try:
    _index_path = os.path.join(os.path.dirname(__file__), '../../../language-app-data/data/clean/index_output.json')
    with open(_index_path, 'r', encoding='utf-8') as f:
        _index = json.load(f)
    for _section in _index.values():          # vocab, grammar, proper_nouns
        for _entry in _section:
            _h = _entry.get("hanzi", "")
            if len(_h) == 1:                   # single characters only
                CHAR_PINYIN[_h] = _entry["pinyin"]
    print(f"Loaded {len(CHAR_PINYIN)} single-char pinyin entries")
except Exception as e:
    print(f"Could not load index_output.json ({e}); using pypinyin only")

def char_to_pinyin(ch: str) -> str:
    """Single character -> numeric pinyin. Curated dict first, pypinyin fallback."""
    if ch in CHAR_PINYIN:
        return CHAR_PINYIN[ch]
    return to_numbered_pinyin(ch)


def _base_tone(syllable: str):
    """'ta1' -> ('ta','1'); bare 'de' -> ('de','5')."""
    if syllable and syllable[-1] in '12345':
        return syllable[:-1], syllable[-1]
    return syllable, '5'

def grade_speaking_sentence(transcription: str, expected_hanzi: str) -> bool:
    """
    Grade a speaking-sentence attempt by comparing HANZI first, dropping to
    per-character pinyin only where characters differ. Avoids round-tripping
    the whole sentence through pinyin (which mis-segments run-together
    syllables). Homophones (他/她 both ta1) get credit since this is a spoken
    exercise; tones always count; neutral tone is forgiven as a last resort.
    """
    t = strip_punct(transcription)
    e = strip_punct(expected_hanzi)

    if t == e:
        return True                      # exact match, no pinyin needed
    if len(t) != len(e):
        return False                     # can't align; wrong

    for tc, ec in zip(t, e):
        if tc == ec:
            continue
        tb, tt = _base_tone(char_to_pinyin(tc))
        eb, et = _base_tone(char_to_pinyin(ec))
        if tb != eb:
            return False                 # different sound
        if tt != et:
            if tt == '5' or et == '5':
                continue                 # neutral-tone forgiveness
            return False                 # tones count
    return True

audio_cache = {}
session_files = set()

AZURE_SPEECH_KEY = os.environ.get("AZURE_SPEECH_KEY")
AZURE_SPEECH_REGION = os.environ.get("AZURE_REGION", "eastus")
anthropic_client = anthropic_sdk.Anthropic(api_key=os.environ.get("CLAUDE_API_KEY"))

# --- Pronunciation assessment tuning ---
# Empirically (see spike): correct tone scores highest, wrong tones drop 10-23
# points, but the bands overlap across characters (correct 是 = 87, correct 岁 = 96).
# So accuracy alone is a *soft* tone signal. We gate on BOTH a high accuracy
# score (catches mangled consonants/vowels) AND tones_match (exact tone check).
# Tune this after living with it: 90 is strict, 85 is forgiving.
ACCURACY_THRESHOLD = 90

# A "single word" answer goes through pronunciation assessment; longer answers
# stay on the transcription path (sentence assessment needs zh-CN word
# segmentation via get_reference_words, an extra service call we're skipping).
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


# ----------------------------- STT HELPERS -----------------------------

def strip_punct(text: str) -> str:
    return re.sub(r'[。？！，、；：""\'\'…\s]', '', text)


def to_numbered_pinyin(text: str) -> str:
    # strip punctuation before converting
    text = strip_punct(text)

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

# Sounds gated behind pronunciation practice because they have no English
# equivalent (see api/v1/endpoints/practice.py's speaking-sentence gate).
# Everything else is assumed learnable by ear/spelling alone.
GATED_INITIALS = {'zh', 'ch', 'sh', 'r', 'j', 'q', 'x', 'z', 'c'}
GATED_FINALS = {'v', 'er', 'e'}
GATED_SOUNDS = GATED_INITIALS | GATED_FINALS

# Initials after which numeric pinyin spells the u+00fc (ü) sound as a
# plain "u" instead of "v" (e.g. "qu4", not "qv4") -- only these four take the
# hidden-ü normalization. "nu"/"nv" (女 vs 努) are genuinely different
# finals and must NOT be collapsed, which is why this list is narrow and the
# swap below only fires for an exact bare "u" final.
HIDDEN_V_INITIALS = {'j', 'q', 'x', 'y'}


def _match_pinyin_syllables(p: str):
    """Core longest-match segmentation shared by split_pinyin_syllables() and
    split_pinyin_sounds(). Yields (initial, final, tone) per syllable; initial
    is '' for syllables with no initial consonant."""
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
                    if end < len(p) and p[end] in '12345':
                        tone = p[end]
                        i = end + 1
                    else:
                        tone = '5'
                        i = end
                    yield initial, final, tone
                    matched = True
                    break
        if not matched:
            i += 1


def split_pinyin_syllables(p: str) -> list:
    return [(initial + final, tone) for initial, final, tone in _match_pinyin_syllables(p)]


def split_pinyin_sounds(p: str) -> list:
    """Like split_pinyin_syllables(), but keeps each syllable's initial and
    final separate instead of concatenating them, and normalizes the hidden-ü
    spelling: ju/qu/xu/yu are pronounced with ü even though numeric pinyin
    spells the final as a bare "u" (nu vs nv stays contrastive -- only a final
    that is *exactly* "u" after j/q/x/y gets swapped). Returns a list of
    (initial, final, tone) tuples."""
    result = []
    for initial, final, tone in _match_pinyin_syllables(p):
        if initial in HIDDEN_V_INITIALS and final == 'u':
            final = 'v'
        result.append((initial, final, tone))
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

# --------------- PRONUNCIATION ASSESSMENT (single words) ---------------

def assess_pronunciation_with_azure(audio_path: str, reference_text: str) -> dict:
    """
    Score the audio AGAINST the known reference text, rather than asking Azure
    to guess what was said. This sidesteps homophone/near-homophone confusion
    (ji vs zhi, sui vs shui) entirely: Azure can't hand back the wrong character
    because it isn't choosing one.

    Returns: {recognized, accuracy, phonemes: [{phoneme, accuracy}], error?}
    """
    speech_config = speechsdk.SpeechConfig(
        subscription=AZURE_SPEECH_KEY,
        region=AZURE_SPEECH_REGION,
    )
    speech_config.speech_recognition_language = "zh-CN"
    # Assessment uses a longer end-silence timeout than plain STT (education
    # scenarios have longer pauses).
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
        enable_miscue=False,  # miscue needs word segmentation; off for single words
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
    """Transcription path — still used for multi-word / sentence answers."""
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

    # Decide based on the EXPECTED answer, which we know up front. A mid-utterance
    # pause only cuts you off in single-shot mode, so use continuous recognition
    # whenever the expected answer is long enough to contain natural pauses.
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

            # Two gates. Accuracy catches mangled consonants/vowels; tones_match
            # is the exact tone check (accuracy alone is only a soft tone signal).
            accuracy_ok = accuracy >= ACCURACY_THRESHOLD
            tone_ok = bool(transcription_pinyin) and tones_match(transcription_pinyin, expected_pinyin)
            is_correct = accuracy_ok and tone_ok

            # Tell the user WHICH gate failed — "check your tones" is wrong advice
            # when the consonant was the problem.
            if is_correct:
                feedback = "correct"
            elif not accuracy_ok and not tone_ok:
                feedback = "sound_and_tone"
            elif not accuracy_ok:
                feedback = "sound"
            else:
                feedback = "tone"

            # weakest phoneme, for pointing at the specific problem sound
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

        # Grade by comparing HANZI first (falls to per-char pinyin only where
        # characters differ), which avoids the run-together syllable
        # mis-segmentation that breaks whole-sentence pinyin comparison.
        # Needs the expected hanzi from the frontend; falls back to the old
        # pinyin comparison if it wasn't sent.
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


# ----------------------------- AI GRADING -----------------------------

@router.post("/api/grade")
async def grade_answer(payload: dict):
    user_answer = payload.get("user_answer", "").strip()
    expected = payload.get("expected_answer", "").strip()
    question = payload.get("question", "").strip()
    print(f"[grade] q={question!r} user={user_answer!r} expected={expected!r}")

    if not user_answer or not expected:
        return JSONResponse({"is_correct": False})

    try:
        response = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=5,
            messages=[{
                "role": "user",
                "content": (
                    "You are grading a Chinese-to-English translation exercise for a beginner learner. "
                    "The learner sees a Chinese word or sentence and types an English translation. "
                    "Mark the answer CORRECT if it conveys the meaning of the Chinese, even if the wording, "
                    "articles, punctuation, or phrasing differ from the reference. Be lenient about minor "
                    "grammar, synonyms, and word order. Mark INCORRECT only if the meaning is wrong or missing.\n\n"
                    f"Chinese: {question}\n"
                    f"Reference translation: {expected}\n"
                    f"Learner's answer: {user_answer}\n\n"
                    "Reply with only YES (correct) or NO (incorrect)."
                )
            }]
        )
        result = response.content[0].text.strip().upper()
        return JSONResponse({"is_correct": result.startswith("YES")})
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