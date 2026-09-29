"""
Trim leading and trailing silence from every .mp3 in audio-male/ and audio-female/.

Run from inside the pinyin-audio directory:
    python trim_silence.py

Originals are copied to _originals/ first (once), so you can re-run with
different settings or restore them. Requires ffmpeg on your PATH.
"""
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

FOLDERS = ["audio-female", "audio-male"]
BACKUP_DIR = Path("_originals")

THRESHOLD = "-45dB"  # anything quieter than this counts as silence
KEEP = "0.03"        # seconds of silence to leave at each end, so the onset isn't clipped

# Trim the start, reverse, trim the (new) start, reverse back.
FILTER = (
    f"silenceremove=start_periods=1:start_silence={KEEP}:start_threshold={THRESHOLD},"
    f"areverse,"
    f"silenceremove=start_periods=1:start_silence={KEEP}:start_threshold={THRESHOLD},"
    f"areverse"
)


def trim(path: Path) -> tuple[Path, str | None]:
    backup = BACKUP_DIR / path
    if not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)

    tmp = path.with_suffix(".tmp.mp3")
    result = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(backup),
         "-af", FILTER, "-c:a", "libmp3lame", "-q:a", "2", str(tmp)],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        return path, result.stderr.strip() or "empty output (file may be all silence)"

    tmp.replace(path)
    return path, None


def main():
    if shutil.which("ffmpeg") is None:
        sys.exit("ffmpeg not found on PATH.")

    files = []
    for folder in FOLDERS:
        d = Path(folder)
        if not d.is_dir():
            print(f"Skipping {folder}: not found (are you in the pinyin-audio directory?)")
            continue
        files += sorted(p for p in d.glob("*.mp3") if not p.name.endswith(".tmp.mp3"))

    if not files:
        sys.exit("No .mp3 files found.")

    print(f"Trimming {len(files)} files (originals backed up to {BACKUP_DIR}/)...")
    failed = []
    with ThreadPoolExecutor() as pool:
        for i, (path, err) in enumerate(pool.map(trim, files), 1):
            if err:
                failed.append((path, err))
            if i % 100 == 0 or i == len(files):
                print(f"  {i}/{len(files)}")

    print(f"Done. {len(files) - len(failed)} trimmed, {len(failed)} failed.")
    for path, err in failed:
        print(f"  FAILED {path}: {err}")


if __name__ == "__main__":
    main()