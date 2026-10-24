"""Loads the models once per process and scores recordings.

Each server worker process holds its own copy of the models (~630 MB), so run the app with ONE worker
on a 2 GB machine.
"""
import threading
import time

from app.core.logger import logger

from ..constants import (MAX_CONCURRENT, MAX_SECONDS, MAX_SECONDS_WORD, MIN_SECONDS, MODEL_DIR,
                         MODEL_NAMES, PITCH_MODEL_FILE, SAMPLE_RATE, THREADS)
from .audio import AudioError, decode
from .onnx_scorer import OnnxScorer
from .pinyin_units import split_syllable
from .scorer_core import apply_pitch, combine, judge, prepare, trim_silence
from .split import FRAME_SAMPLES, split

_scorer = None
_load_lock = threading.Lock()
_slots = threading.BoundedSemaphore(MAX_CONCURRENT)

# score_word: True = run the models ONCE on the whole take and score each syllable from its slice of
# that output (2 model runs for any word). False = the old way, which runs the models again on every
# cut-out syllable (2 + 2 x syllables runs). Flip to False to compare results on the same recordings.
REUSE_MODEL_OUTPUT = True


def _ms(a, b):
    return round(1000 * (b - a))


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
    t_start = time.perf_counter()
    target = target.strip().lower()
    if split_syllable(target) is None:
        raise TargetError(f"not a pinyin syllable with tone: {target!r}")
    wav = decode(data)
    t_decoded = time.perf_counter()
    seconds = len(wav) / SAMPLE_RATE
    if seconds < MIN_SECONDS:
        raise AudioError("audio too short")
    if seconds > MAX_SECONDS:
        raise AudioError(f"audio longer than {MAX_SECONDS:.0f} s; record one syllable")
    wav, _ = trim_silence(wav)
    scorer = get_scorer()
    with _slots:
        t0 = time.perf_counter()
        x = prepare(wav)
        probs = combine(scorer.model_probs(x))
        t_models = time.perf_counter()
        vote = scorer.pitch_vote(x)
        t_pitch = time.perf_counter()
        result = judge(apply_pitch(probs, vote, scorer.pitch_weight), target)
    t_end = time.perf_counter()
    result["ms"] = _ms(t0, t_end)
    logger.info("score %s: audio %.2fs -> %.2fs after trim | decode %dms | wait %dms | models %dms | "
                "pitch %dms | total %dms", target, seconds, len(x) / SAMPLE_RATE,
                _ms(t_start, t_decoded), _ms(t_decoded, t0), _ms(t0, t_models), _ms(t_models, t_pitch),
                _ms(t_start, t_end))
    return result


def score_word(data: bytes, target: str) -> dict:
    """Raw upload bytes + several syllables said in one take (e.g. "cao3 mei2") -> one result per
    syllable. The take is split into syllables (split.py) and each is scored like score_recording.
    Raises TargetError / AudioError for bad input."""
    t_start = time.perf_counter()
    targets = target.strip().lower().split()
    if not targets or any(split_syllable(t) is None for t in targets):
        raise TargetError(f"not pinyin syllables with tones: {target!r}")
    wav = decode(data)
    t_decoded = time.perf_counter()
    seconds = len(wav) / SAMPLE_RATE
    if seconds < MIN_SECONDS:
        raise AudioError("audio too short")
    if seconds > MAX_SECONDS_WORD:
        raise AudioError(f"audio longer than {MAX_SECONDS_WORD:.0f} s")
    wav, offset = trim_silence(wav)        # cuts below are shifted back by `offset` to match the upload
    scorer = get_scorer()
    with _slots:
        t0 = time.perf_counter()
        x = prepare(wav)
        logits = scorer.logits(x)                       # the only model run when REUSE_MODEL_OUTPUT
        t_models = time.perf_counter()
        clips, cuts, found = split(x, scorer.frame_probs_from(logits), len(targets))
        if REUSE_MODEL_OUTPUT:
            # Syllable k is frames [cut k-1, cut k). A cut always lies after syllable k's final spike
            # and no later than the frame of syllable k+1's initial spike, so each spike lands in its
            # own syllable's slice.
            bounds = [0, *(c // FRAME_SAMPLES for c in cuts), None]
            syllables = [scorer.score_slice(logits, a, b, clip, t)
                         for a, b, clip, t in zip(bounds, bounds[1:], clips, targets)]
        else:
            syllables = [scorer.score(clip, t) for clip, t in zip(clips, targets)]
    t_end = time.perf_counter()
    ms = _ms(t0, t_end)
    logger.info("score_word %s (%s): audio %.2fs -> %.2fs after trim | decode %dms | wait %dms | "
                "models on whole take %dms | split + per-syllable %dms | total %dms",
                " ".join(targets), "reuse" if REUSE_MODEL_OUTPUT else "rerun", seconds,
                len(x) / SAMPLE_RATE, _ms(t_start, t_decoded), _ms(t_decoded, t0), _ms(t0, t_models),
                _ms(t_models, t_end), _ms(t_start, t_end))
    pad = (len(x) - len(wav)) // 2         # prepare() pads clips under 0.5 s on both sides
    no_speech = all(r["no_speech"] for r in syllables)
    return {
        "target": " ".join(targets),
        "syllables": syllables,
        "no_speech": no_speech,
        "understood": not no_speech and all(r["understood"] for r in syllables),
        "cuts": [round((max(0, c - pad) + offset) / SAMPLE_RATE, 3) for c in cuts],
        "split_found": found,
        "ms": ms,
    }