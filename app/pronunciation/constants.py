import os
from app.core.config.data import MODEL_DIR

# Folder with the exported models (see pinyin_scorer's README, "Deploying"):
#   stage6b/model_int8.onnx + onnx.json
#   stage7_hubert/model_int8.onnx + onnx.json
#   tone23_pitch.joblib
# Defaults to <repo>/data/pronunciation; override with the PRONUNCIATION_MODEL_DIR environment variable.
MODEL_NAMES = ["stage6b", "stage7_hubert"]          # combined by averaging
PITCH_MODEL_FILE = "tone23_pitch.joblib"

# CPU threads for the models (0 = let ONNX Runtime decide). Set to the server's core count.
THREADS = int(os.environ.get("PRONUNCIATION_THREADS", "0")) or None
# How many recordings may be scored at the same time; extra requests wait. Keeps CPU and memory bounded.
MAX_CONCURRENT = int(os.environ.get("PRONUNCIATION_MAX_CONCURRENT", "1"))

MAX_UPLOAD_BYTES = 2_000_000                        # ~2 MB
MIN_SECONDS = 0.1
MAX_SECONDS = 4.0                                   # one syllable; longer audio is rejected
MAX_SECONDS_WORD = 6.0                              # several syllables in one take (/score_word)
SAMPLE_RATE = 16000
