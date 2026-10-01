# app/pinyin/services/phoneme.py
"""
Phoneme checking for spoken pinyin: did the learner produce the right
INITIAL and FINAL? Tones are deliberately ignored -- the tone checker grades
those separately. If the target is zhe2 and the learner says zhe4, that's
correct here.

How it works: a local sherpa-onnx Mandarin ASR model transcribes the audio
to hanzi, each hanzi is converted to toneless pinyin, and that's compared
against the expected syllable(s). Comparing pinyin rather than characters
means homophones don't matter (是 vs 事 are both "shi"), and every reading of
a polyphonic character is accepted (着 can be zhe/zhao/zhuo).

The model is loaded once per process (see get_recognizer) and runs in-process;
there is no separate model server.
"""
import os
import re
import subprocess
from functools import lru_cache

import numpy as np
import sherpa_onnx
from pypinyin import pinyin, Style

from pinyin_utils import split_pinyin_sounds, strip_punct

# The shared parser only knows the "ve" spelling of the üe final, so
# xue/yue/jue/que (and lue/nue) would mis-split into two syllables.
_UE_AFTER = re.compile(r"([jqxyln])ue")


def _parse(p: str) -> list[tuple[str, str]]:
    """Toneless (initial, final) per syllable, ü normalized to 'v'."""
    p = _UE_AFTER.sub(r"\1ve", p.lower().replace("ü", "v").replace(" ", ""))
    return [(i, f) for i, f, _ in split_pinyin_sounds(p)]

MODEL_DIR = os.getenv(
    "PHONEME_MODEL_DIR",
    "models/sherpa-onnx-streaming-zipformer-small-ctc-zh-int8-2025-04-01",
)
NUM_THREADS = int(os.getenv("PHONEME_NUM_THREADS", "2"))  # match VPS vCPU count
SAMPLE_RATE = 16000
# Streaming models hold back the last few frames until more audio arrives;
# trailing silence flushes the final syllable out.
TAIL_PADDING_SECONDS = 0.66
FFMPEG_TIMEOUT_SECONDS = 10


class AudioDecodeError(ValueError):
    """Uploaded audio couldn't be decoded by ffmpeg."""


# ----------------------------- MODEL -----------------------------

@lru_cache(maxsize=1)
def get_recognizer() -> sherpa_onnx.OnlineRecognizer:
    """Loaded on first call, then reused. Call once at app startup to avoid
    a slow first request."""
    return sherpa_onnx.OnlineRecognizer.from_zipformer2_ctc(
        model=f"{MODEL_DIR}/model.int8.onnx",
        tokens=f"{MODEL_DIR}/tokens.txt",
        num_threads=NUM_THREADS,
        sample_rate=SAMPLE_RATE,
        feature_dim=80,
        decoding_method="greedy_search",
    )


# ----------------------------- AUDIO -----------------------------

def decode_audio(data: bytes) -> np.ndarray:
    """Any browser recording (webm/opus from Chrome/Firefox, mp4/aac from
    Safari) -> 16 kHz mono float32 samples."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-i", "pipe:0",
             "-f", "f32le", "-ac", "1", "-ar", str(SAMPLE_RATE), "pipe:1"],
            input=data, capture_output=True, check=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        raise AudioDecodeError("Could not decode audio") from e
    return np.frombuffer(proc.stdout, dtype=np.float32)


def transcribe(samples: np.ndarray) -> str:
    recognizer = get_recognizer()
    stream = recognizer.create_stream()
    stream.accept_waveform(SAMPLE_RATE, samples)
    stream.accept_waveform(
        SAMPLE_RATE, np.zeros(int(TAIL_PADDING_SECONDS * SAMPLE_RATE), dtype=np.float32)
    )
    stream.input_finished()
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)
    return strip_punct(recognizer.get_result(stream))


# ----------------------------- GRADING -----------------------------

Sound = tuple[str, str]  # (initial, final); initial is '' for bare finals


def _expected_sounds(expected_pinyin: str) -> list[Sound]:
    """'zhe2' -> [('zh','e')]; 'ni3hao3' or 'ni3 hao3' -> two syllables.
    Tone digits are parsed and then thrown away."""
    return _parse(expected_pinyin)


def _char_readings(ch: str) -> list[Sound]:
    """Every toneless reading of one hanzi, parsed with the same
    ü-normalizing parser as the expected side so the two compare cleanly."""
    readings = []
    for r in pinyin(ch, style=Style.NORMAL, heteronym=True)[0]:
        parsed = _parse(r)
        if len(parsed) == 1 and parsed[0] not in readings:
            readings.append(parsed[0])
    return readings


def _best_reading(readings: list[Sound], target: Sound) -> Sound | None:
    """The reading closest to the target: full match, else one that gets the
    initial or final right, else the first."""
    if not readings:
        return None
    return max(readings, key=lambda r: (r[0] == target[0]) + (r[1] == target[1]))


def _syllable_result(target: Sound, heard: Sound | None) -> dict:
    # Initials/finals use the internal "v" spelling for ü (qv, xve, lv);
    # the frontend converts to display spelling.
    return {
        "expected": "".join(target),
        "heard": "".join(heard) if heard else None,
        "expected_initial": target[0],
        "expected_final": target[1],
        "heard_initial": heard[0] if heard else None,
        "heard_final": heard[1] if heard else None,
        "initial_ok": heard is not None and heard[0] == target[0],
        "final_ok": heard is not None and heard[1] == target[1],
    }


def grade_phonemes(transcript: str, expected_pinyin: str) -> dict:
    expected = _expected_sounds(expected_pinyin)
    heard_readings = [_char_readings(ch) for ch in transcript]

    if not expected:
        raise ValueError(f"Could not parse expected pinyin: {expected_pinyin!r}")

    if len(heard_readings) == len(expected):
        # One heard character per expected syllable: compare position by position.
        syllables = [
            _syllable_result(t, _best_reading(r, t))
            for t, r in zip(expected, heard_readings)
        ]
    elif len(expected) == 1 and heard_readings:
        # Single-syllable drill, but the model heard extra characters (a
        # filler, a stray syllable): use whichever one comes closest.
        target = expected[0]
        candidates = [b for r in heard_readings if (b := _best_reading(r, target))]
        best = _best_reading(candidates, target)
        syllables = [_syllable_result(target, best)]
    else:
        # Nothing heard, or a multi-syllable target with a length mismatch.
        syllables = [_syllable_result(t, None) for t in expected]

    return {
        "correct": all(s["initial_ok"] and s["final_ok"] for s in syllables),
        "transcript": transcript,
        "syllables": syllables,
    }


def check_phonemes(audio_bytes: bytes, expected_pinyin: str) -> dict:
    return grade_phonemes(transcribe(decode_audio(audio_bytes)), expected_pinyin)