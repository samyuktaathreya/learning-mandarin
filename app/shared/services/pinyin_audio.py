"""
shared/services/pinyin_audio.py

Real recordings for single pinyin syllables, used by the pinyin section
instead of TTS (TTS tones aren't clear enough on one syllable).

Recordings live in PINYIN_AUDIO_DIR/audio-male and /audio-female as
e.g. ma3.mp3, lv4.mp3. They're served as static files (mounted in main.py),
so this module only works out whether a file exists and builds its URL.
Slowed-down copies are made on demand with ffmpeg and cached in SLOW_DIR.
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

RECORDINGS_DIR = DATA_DIR / "pinyin-audio"

# URL prefixes the two folders are mounted at (see main.py)
RECORDINGS_URL = "/pinyin-audio"
SLOW_URL = "/pinyin-audio-slow"

# Checked in order: male first, female as fallback
VOICE_FOLDERS = ["audio-male", "audio-female"]

# atempo slows audio without changing pitch, so the tone contour is kept.
# 0.5 matches the -50% rate TTS uses for single syllables.
SLOW_TEMPO = "0.5"

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


def find_recording(pinyin: str) -> Path | None:
    key = to_filename_key(pinyin)
    if not key:
        return None
    
    folders = list(VOICE_FOLDERS)
    random.shuffle(folders)
    
    for folder in folders:
        path = RECORDINGS_DIR / folder / f"{key}.mp3"
        if path.exists():
            return path
    return None


async def _make_slowed(src: Path) -> str | None:
    """Returns the filename (inside SLOW_DIR) of a slowed copy of `src`,
    creating it the first time. None if ffmpeg fails."""
    name = f"{src.parent.name}_{src.name}"   # e.g. audio-male_ma3.mp3
    out = SLOW_DIR / name
    if out.exists() and out.stat().st_size > 0:
        return name

    SLOW_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SLOW_DIR / f"{src.stem}.{uuid.uuid4().hex}.tmp.mp3"
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", str(src), "-filter:a", f"atempo={SLOW_TEMPO}", str(tmp),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        if tmp.exists():
            os.remove(tmp)
        print(f"[pinyin audio] ffmpeg failed to slow {src.name} (exit {proc.returncode}), serving normal speed")
        print(stderr.decode(errors="replace")[-1500:])
        return None

    os.replace(tmp, out)
    return name


async def get_recording_url(pinyin: str, slow: bool = False) -> str | None:
    """URL of the recording for `pinyin`, or None if there isn't one.
    If slowing fails, returns the normal-speed recording rather than None,
    so a slow request never falls back to TTS just because of ffmpeg."""
    path = find_recording(pinyin)
    if path is None:
        return None

    normal_url = f"{RECORDINGS_URL}/{path.parent.name}/{path.name}"
    if not slow:
        return normal_url

    slow_name = await _make_slowed(path)
    return f"{SLOW_URL}/{slow_name}" if slow_name else normal_url