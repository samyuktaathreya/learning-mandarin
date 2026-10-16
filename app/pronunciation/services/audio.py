import subprocess

import numpy as np

from ..constants import SAMPLE_RATE


class AudioError(ValueError):
    pass


def decode(data: bytes) -> np.ndarray:
    """Any audio format ffmpeg understands (browser webm/opus, ogg, mp4/aac, wav) -> mono float32 at 16 kHz.
    Needs ffmpeg installed on the server."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
             "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "pipe:1"],
            input=data, capture_output=True, timeout=20)
    except FileNotFoundError:
        raise RuntimeError("ffmpeg is not installed on the server")
    if proc.returncode != 0:
        raise AudioError(f"could not read the audio: {proc.stderr.decode(errors='ignore')[:200]}")
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()
