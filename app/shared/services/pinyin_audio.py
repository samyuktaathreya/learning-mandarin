"""
shared/services/pinyin_audio.py

Real recordings for single pinyin syllables, used by the pinyin section
instead of TTS (TTS tones aren't clear enough on one syllable).

Recordings are the Tone Perfect set, in RECORDINGS_DIR/tone_perfect/ as
one folder per syllable holding one file per speaker, e.g. ma3/FV1.mp3,
lv4/MV2.mp3 (FV1-3 female, MV1-3 male; a few syllables are missing a
speaker). Each request picks a speaker at random.
They're served as static files (mounted in main.py),
so this module only works out whether a file exists and builds its URL.
Two-syllable pinyin ("ma 1 ma 3") is played by joining the two recordings
with ffmpeg; the joined files are cached in PAIRS_DIR. Slowed-down copies
are made on demand with ffmpeg and cached in SLOW_DIR.
"""
import asyncio
import os
import re
import uuid
from pathlib import Path
import random

from app.core.config.shared import settings
from app.core.config.data import DATA_DIR

# Absolute, so ffmpeg, the static mount and this module always agree
SLOW_DIR = (Path("audio_cache") / "pinyin_slow").resolve()
SLOW_DIR.mkdir(parents=True, exist_ok=True)
PAIRS_DIR = (Path("audio_cache") / "pinyin_pairs").resolve()
PAIRS_DIR.mkdir(parents=True, exist_ok=True)

RECORDINGS_DIR = DATA_DIR / "pinyin-audio"

# URL prefixes the two folders are mounted at (see main.py)
RECORDINGS_URL = "/pinyin-audio"
SLOW_URL = "/pinyin-audio-slow"
PAIRS_URL = "/pinyin-audio-pairs"

TONE_PERFECT_DIR = RECORDINGS_DIR / "tone_perfect"
SPEAKERS = ["FV1", "FV2", "FV3", "MV1", "MV2", "MV3"]

# atempo slows audio without changing pitch, so the tone contour is kept.
# 0.5 matches the -50% rate TTS uses for single syllables.
SLOW_TEMPO = "0.5"

# Silence between the two syllables of a joined pair, so they're heard as
# two separate syllables (like the comma in the TTS text) rather than run
# together.
PAIR_GAP_SECONDS = 0.15

_TONE_MARKS = {}
for _base, _marked in {"a": "āáǎà", "e": "ēéěè", "i": "īíǐì",
                       "o": "ōóǒò", "u": "ūúǔù", "ü": "ǖǘǚǜ"}.items():
    for _tone, _ch in enumerate(_marked, start=1):
        _TONE_MARKS[_ch] = (_base, _tone)


def to_filename_key(pinyin: str) -> str | None:
    """Normalize any pinyin format to the recordings' file naming.
      'mǎ' / 'ma3' / 'ma 3' / 'MA3'  -> 'ma3'
      'lǜ' / 'lü4' / 'lu:4' / 'lv4'  -> 'lv4'
    Returns None if it isn't a single syllable with tone 1-4
    (e.g. neutral tone, which has no recordings)."""
    s = pinyin.strip().lower().replace(" ", "")
    tone = None
    chars = []
    for ch in s:
        if ch in _TONE_MARKS:
            base, tone = _TONE_MARKS[ch]
            chars.append(base)
        else:
            chars.append(ch)
    s = "".join(chars).replace("u:", "v").replace("ü", "v")
    if tone:
        s += str(tone)
    return s if re.fullmatch(r"[a-z]+[1-4]", s) else None


def to_filename_keys(pinyin: str) -> list[str] | None:
    """Like to_filename_key, but for one or more space-separated syllables.
      'ma 3'       -> ['ma3']
      'ma 1 ma 3'  -> ['ma1', 'ma3']
      'mā mǎ'      -> ['ma1', 'ma3']
    None if any syllable has no recording key (e.g. a neutral tone)."""
    # Glue each tone digit to its syllable: "ma 1 ma 3" -> "ma1 ma3"
    parts = re.sub(r"\s+(?=\d)", "", pinyin.strip()).split()
    keys = [to_filename_key(p) for p in parts]
    return keys if keys and all(keys) else None


def find_recordings(keys: list[str]) -> list[Path] | None:
    """One recording per key from a randomly chosen speaker, all from the
    same speaker when one has them all (so a pair doesn't switch speaker
    mid-word), otherwise from whichever speaker has each one. None if any
    key has no recording."""
    speakers = list(SPEAKERS)
    random.shuffle(speakers)

    for speaker in speakers:
        paths = [TONE_PERFECT_DIR / key / f"{speaker}.mp3" for key in keys]
        if all(p.exists() for p in paths):
            return paths

    paths = []
    for key in keys:
        path = next((TONE_PERFECT_DIR / key / f"{s}.mp3" for s in speakers
                     if (TONE_PERFECT_DIR / key / f"{s}.mp3").exists()), None)
        if path is None:
            return None
        paths.append(path)
    return paths


def find_recording(pinyin: str) -> Path | None:
    key = to_filename_key(pinyin)
    if not key:
        return None
    paths = find_recordings([key])
    return paths[0] if paths else None


async def _run_ffmpeg(args: list[str], out: Path, label: str) -> bool:
    """Runs ffmpeg writing to a temp file next to `out`, then moves it into
    place, so a half-written file is never served. False if ffmpeg fails."""
    tmp = out.parent / f"{out.stem}.{uuid.uuid4().hex}.tmp.mp3"
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", *args, str(tmp),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        if tmp.exists():
            os.remove(tmp)
        print(f"[pinyin audio] ffmpeg failed to {label} (exit {proc.returncode})")
        print(stderr.decode(errors="replace")[-1500:])
        return False

    os.replace(tmp, out)
    return True


async def _make_joined(paths: list[Path]) -> Path | None:
    """Path of the recordings in `paths` joined in order with a short gap,
    creating it the first time. None if ffmpeg fails."""
    # e.g. ma1_FV1+ma3_FV1.mp3
    name = "+".join(f"{p.parent.name}_{p.stem}" for p in paths) + ".mp3"
    out = PAIRS_DIR / name
    if out.exists() and out.stat().st_size > 0:
        return out

    PAIRS_DIR.mkdir(parents=True, exist_ok=True)
    # The voices are recorded in different formats (stereo 320k vs mono), so
    # bring every input to one format before joining.
    fmt = "aformat=sample_rates=44100:channel_layouts=mono"
    inputs, filters, labels = [], [], []
    for i, p in enumerate(paths):
        inputs += ["-i", str(p)]
        pad = f",apad=pad_dur={PAIR_GAP_SECONDS}" if i < len(paths) - 1 else ""
        filters.append(f"[{i}:a]{fmt}{pad}[a{i}]")
        labels.append(f"[a{i}]")
    graph = ";".join(filters) + f";{''.join(labels)}concat=n={len(paths)}:v=0:a=1[out]"

    ok = await _run_ffmpeg([*inputs, "-filter_complex", graph, "-map", "[out]"],
                           out, f"join {name}")
    return out if ok else None


async def _make_slowed(src: Path) -> str | None:
    """Returns the filename (inside SLOW_DIR) of a slowed copy of `src`,
    creating it the first time. None if ffmpeg fails."""
    # e.g. ma3_FV1.mp3; a joined pair is already named by its speakers
    name = src.name if src.parent == PAIRS_DIR else f"{src.parent.name}_{src.name}"
    out = SLOW_DIR / name
    if out.exists() and out.stat().st_size > 0:
        return name

    SLOW_DIR.mkdir(parents=True, exist_ok=True)
    ok = await _run_ffmpeg(["-i", str(src), "-filter:a", f"atempo={SLOW_TEMPO}"],
                           out, f"slow {name}, serving normal speed")
    return name if ok else None


async def get_recording_url(pinyin: str, slow: bool = False) -> str | None:
    """URL of the recording for `pinyin`, or None if there isn't one.
    Several syllables ("ma 1 ma 3") are joined into one file; None if any
    of them has no recording or joining fails.
    If slowing fails, returns the normal-speed recording rather than None,
    so a slow request never falls back to TTS just because of ffmpeg."""
    keys = to_filename_keys(pinyin)
    paths = find_recordings(keys) if keys else None
    if not paths:
        return None

    if len(paths) == 1:
        path = paths[0]
        normal_url = f"{RECORDINGS_URL}/{path.relative_to(RECORDINGS_DIR).as_posix()}"
    else:
        path = await _make_joined(paths)
        if path is None:
            return None
        normal_url = f"{PAIRS_URL}/{path.name}"
    if not slow:
        return normal_url

    slow_name = await _make_slowed(path)
    return f"{SLOW_URL}/{slow_name}" if slow_name else normal_url