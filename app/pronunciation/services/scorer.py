"""Loads the models once per process and scores recordings.

Each server worker process holds its own copy of the models (~630 MB), so run the app with ONE worker
on a 2 GB machine.
"""
import threading
import time

from ..constants import (MAX_CONCURRENT, MAX_SECONDS, MIN_SECONDS, MODEL_DIR, MODEL_NAMES,
                         PITCH_MODEL_FILE, SAMPLE_RATE, THREADS)
from .audio import AudioError, decode
from .onnx_scorer import OnnxScorer
from .pinyin_units import split_syllable

_scorer = None
_load_lock = threading.Lock()
_slots = threading.BoundedSemaphore(MAX_CONCURRENT)


class TargetError(ValueError):
    pass


def get_scorer() -> OnnxScorer:
    global _scorer
    if _scorer is None:
        with _load_lock:
            if _scorer is None:
                _scorer = OnnxScorer([str(MODEL_DIR / m) for m in MODEL_NAMES],
                                     pitch_model=str(MODEL_DIR / PITCH_MODEL_FILE), threads=THREADS)
    return _scorer


def warm_up():
    """Load the models now (call at app startup), so the first learner doesn't wait ~2 s."""
    get_scorer()


def score_recording(data: bytes, target: str) -> dict:
    """Raw upload bytes + target syllable (e.g. "zhuang4") -> score.py's result dict, plus "ms".
    Raises TargetError / AudioError for bad input."""
    target = target.strip().lower()
    if split_syllable(target) is None:
        raise TargetError(f"not a pinyin syllable with tone: {target!r}")
    wav = decode(data)
    seconds = len(wav) / SAMPLE_RATE
    if seconds < MIN_SECONDS:
        raise AudioError("audio too short")
    if seconds > MAX_SECONDS:
        raise AudioError(f"audio longer than {MAX_SECONDS:.0f} s; record one syllable")
    scorer = get_scorer()
    with _slots:
        t0 = time.time()
        result = scorer.score(wav, target)
    result["ms"] = round(1000 * (time.time() - t0))
    return result
