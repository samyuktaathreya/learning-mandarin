"""Splits a recording of several syllables ("cao3 mei2" said in one take) into one clip per syllable,
so each can be scored by the single-syllable scorer.

The models emit one CTC spike per syllable on every head, also on multi-syllable audio: the initial
head spikes once per syllable (as "_" for zero-initial syllables like the "an" in xi'an), and so does
the final head. Syllable k ends somewhere between final spike k and initial spike k+1. In that window
the cut goes where the first syllable's voicing fades out (loudness below 1 kHz drops): the start of a
stop closure, of a fricative, or of the low-energy stretch of a nasal or a vowel-to-vowel join.

The spikes are found without looking at the target syllables, so a mispronounced syllable can't move
the cut to wherever happens to suit the target.
"""
import numpy as np

SAMPLE_RATE = 16000
FRAME_SAMPLES = 320                     # one model output frame = 20 ms
MIN_SPIKE_GAP = 3                       # frames between two spikes of the same head
ENERGY_HOP = 160                        # 10 ms hop for the loudness curve
WINDOW = 400                            # 25 ms analysis window
LOW_BAND_HZ = 1000
DROP_FRACTION = 0.3                     # cut once loudness has fallen 70% of the way (in dB) to the minimum
# Each clip after the first starts this much before its cut. A clip that starts right at a fricative
# sounds like it starts with a burst: sh is heard as zh/ch, x as j. A little of the previous vowel
# in front avoids that. Much more and a zero-initial syllable picks up the vowel before it (the "an"
# of xi'an heard as "ian"). 40-60 ms was best on TTS words (Oct 2026).
LEAD_IN_SAMPLES = 800                   # 50 ms
FADE_SAMPLES = 160                      # 10 ms fade at each clip edge, so the cut itself isn't a click


def _spikes(frame_probs, n):
    """frame_probs: (T, C) with blank at 0 -> the n frames most likely to hold a unit, in time order.
    Takes the strongest frames greedily, keeping them MIN_SPIKE_GAP apart (one spike can span two
    frames). Returns None if the clip is too short to hold n spikes."""
    blank = np.clip(frame_probs[:, 0], 1e-9, 1.0)
    strength = np.log(np.clip(1.0 - blank, 1e-9, 1.0)) - np.log(blank)
    chosen = []
    for t in np.argsort(-strength):
        if all(abs(int(t) - c) >= MIN_SPIKE_GAP for c in chosen):
            chosen.append(int(t))
            if len(chosen) == n:
                return sorted(chosen)
    return None


def _low_band_db(seg):
    """Loudness below LOW_BAND_HZ in dB per 10 ms frame (25 ms windows). Vowels, nasals and other
    voiced sounds are loud here; fricatives like s/sh/x and stop closures are nearly silent."""
    n = (len(seg) - WINDOW) // ENERGY_HOP + 1
    if n < 3:
        return None
    frames = np.stack([seg[i * ENERGY_HOP:i * ENERGY_HOP + WINDOW] for i in range(n)]) * np.hanning(WINDOW)
    spec = np.abs(np.fft.rfft(frames, axis=1)) ** 2
    nbins = int(LOW_BAND_HZ * WINDOW / SAMPLE_RATE)
    db = 10 * np.log10(spec[:, 1:nbins].sum(axis=1) + 1e-10)
    return np.convolve(db, np.ones(3) / 3, mode="same")


def _syllable_end(wav, start, end):
    """Sample index in wav[start:end] where the first syllable's voicing fades out: the start of
    the drop into the quietest point, not the quietest point itself. Cutting at the minimum would
    land inside a fricative (quieter than a vowel, but part of the next syllable) and clip it."""
    db = _low_band_db(wav[start:end])
    if db is None:
        return (start + end) // 2
    db[0], db[-1] = db[1], db[-2]                   # edges only see half the smoothing window
    low = int(np.argmin(db))
    high = float(db[:low + 1].max())
    threshold = db[low] + DROP_FRACTION * (high - db[low])
    first = next(i for i in range(low + 1) if db[i] <= threshold)
    return start + first * ENERGY_HOP + WINDOW // 2


def find_cuts(wav, frame_probs, n):
    """Sample indices where to cut `wav` (the clip the models saw) into n syllables.
    frame_probs: {"initial": (T, C), "final": (T, C), ...}. Returns n - 1 cuts, or None if the
    syllables can't be located (then the caller falls back to an even split)."""
    initials = _spikes(frame_probs["initial"], n)
    finals = _spikes(frame_probs["final"], n)
    if initials is None or finals is None:
        return None
    cuts = []
    for k in range(n - 1):
        lo, hi = finals[k] + 1, initials[k + 1]
        if hi <= lo:                    # spikes out of order: search between the two finals instead
            lo, hi = finals[k] + 1, finals[k + 1]
        if hi <= lo:
            return None
        cuts.append(_syllable_end(wav, lo * FRAME_SAMPLES, hi * FRAME_SAMPLES + FRAME_SAMPLES))
    return cuts


def split(wav, frame_probs, n):
    """-> (list of n clips, cut positions in samples, whether the spikes were found)."""
    if n == 1:
        return [wav], [], True
    cuts = find_cuts(wav, frame_probs, n)
    found = cuts is not None
    if not found:
        cuts = [len(wav) * (k + 1) // n for k in range(n - 1)]
    starts = [0, *(max(c - LEAD_IN_SAMPLES, prev) for c, prev in zip(cuts, [0, *cuts]))]
    ends = [*cuts, len(wav)]
    return [_fade(wav[a:b]) for a, b in zip(starts, ends)], cuts, found


def _fade(clip):
    clip = clip.copy()
    k = min(FADE_SAMPLES, len(clip) // 2)
    ramp = np.linspace(0.0, 1.0, k, dtype=clip.dtype)
    clip[:k] *= ramp
    clip[len(clip) - k:] *= ramp[::-1]
    return clip
