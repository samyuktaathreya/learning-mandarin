#!/usr/bin/env python3
"""Test sherpa-onnx keyword spotting on single-syllable pinyin wavs.

Files must be named <pinyin><tone>.wav, e.g. ba1.wav, shang4.wav, lv4.wav.
For each wav, the target syllable (all four tones) and its likely confusions
(zh/z/j, n/l, -n/-ng, ...) compete as keywords; we report which one fires.

Usage:
  python3 kws_test.py data/pinyin-audio/audio-male --model <model-dir>
  python3 kws_test.py <dir> --model <model-dir> --threshold 0.15 --score 2
  python3 kws_test.py <dir> --model <model-dir> --target-only
"""
import argparse
import collections
import re
import sys
from pathlib import Path

import numpy as np
import sherpa_onnx
import soundfile as sf

INITIALS = ["zh", "ch", "sh", "b", "p", "m", "f", "d", "t", "n", "l", "g", "k",
            "h", "j", "q", "x", "r", "z", "c", "s", "y", "w"]
MARKS = {"a": "āáǎà", "e": "ēéěè", "i": "īíǐì", "o": "ōóǒò", "u": "ūúǔù", "ü": "ǖǘǚǜ"}
# Sounds learners (and the model) mix up. Each group competes with each other.
INITIAL_GROUPS = [["zh", "z", "j"], ["ch", "c", "q"], ["sh", "s", "x"], ["n", "l"],
                  ["l", "r"], ["b", "p"], ["d", "t"], ["g", "k"], ["h", "f"]]
FINAL_SWAPS = [("an", "ang"), ("en", "eng"), ("in", "ing"), ("ian", "iang"), ("uan", "uang")]


def split(syl):
    """'shang' -> ('sh', 'ang'); 'er' -> ('', 'er')"""
    syl = syl.replace("u:", "ü").replace("v", "ü")
    for ini in INITIALS:
        if syl.startswith(ini) and len(syl) > len(ini):
            return ini, syl[len(ini):]
    return "", syl


def mark(final, tone):
    """Put the tone mark on a final: ('ang', 4) -> 'àng'. Tone 5 = no mark."""
    if tone == 5:
        return final
    if "a" in final:
        v = "a"
    elif "e" in final:
        v = "e"
    elif "ou" in final:
        v = "o"
    else:
        v = [c for c in final if c in MARKS][-1]
    i = final.rindex(v) if v not in "aeo" else final.index(v)
    return final[:i] + MARKS[v][tone - 1] + final[i + 1:]


def keyword_line(syl, tone, vocab):
    """'shang', 4 -> 'sh àng @shang4', or None if the model lacks a token."""
    ini, fin = split(syl)
    if not any(c in MARKS for c in fin):
        return None
    toks = ([ini] if ini else []) + [mark(fin, tone)]
    if not all(t in vocab for t in toks):
        return None
    return " ".join(toks) + f" @{syl}{tone}"


def confusables(syl):
    ini, fin = split(syl)
    out = set()
    for g in INITIAL_GROUPS:
        if ini in g:
            out |= {o + fin for o in g if o != ini}
    for a, b in FINAL_SWAPS:
        if fin == a:
            out.add(ini + b)
        if fin == b:
            out.add(ini + a)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wav_dir")
    ap.add_argument("--model", required=True, help="unpacked sherpa-onnx-kws-zipformer-wenetspeech dir")
    ap.add_argument("--threshold", type=float, default=0.25, help="lower = fires more easily")
    ap.add_argument("--score", type=float, default=1.0, help="higher = fires more easily")
    ap.add_argument("--pad", type=float, default=0.5, help="seconds of silence added to each end")
    ap.add_argument("--target-only", action="store_true", help="no confusables, just the target")
    ap.add_argument("--quiet", action="store_true", help="only print the summary")
    a = ap.parse_args()

    m = Path(a.model)
    vocab = {l.split()[0] for l in (m / "tokens.txt").read_text(encoding="utf-8").splitlines() if l.strip()}
    pick = lambda name: str(sorted(m.glob(f"{name}-epoch-12-*.onnx"), key=lambda p: "int8" in p.name)[0])
    kws = sherpa_onnx.KeywordSpotter(
        tokens=str(m / "tokens.txt"), encoder=pick("encoder"), decoder=pick("decoder"),
        joiner=pick("joiner"), num_threads=1, keywords_file=str(m / "keywords.txt"),
        keywords_score=a.score, keywords_threshold=a.threshold, provider="cpu")

    counts = collections.Counter()
    wrong = collections.Counter()
    for wav in sorted(Path(a.wav_dir).glob("*.wav")):
        mt = re.fullmatch(r"([a-zü:]+?)([1-5])", wav.stem.lower())
        if not mt:
            counts["skipped (bad name)"] += 1
            continue
        syl, tone = mt.group(1).replace("u:", "ü").replace("v", "ü"), int(mt.group(2))
        cands = [syl] if a.target_only else [syl] + sorted(confusables(syl))
        lines = [k for c in cands for t in (1, 2, 3, 4) if (k := keyword_line(c, t, vocab))]
        if not any(l.endswith(f"@{syl}{t}") for l in lines for t in (1, 2, 3, 4)):
            counts["skipped (no token in model)"] += 1
            if not a.quiet:
                print(f"{wav.name:14s} SKIP  model has no tokens for '{syl}'")
            continue

        audio, sr = sf.read(str(wav), dtype="float32", always_2d=True)
        audio = audio.mean(axis=1)
        sil = np.zeros(int(a.pad * sr), dtype=np.float32)
        s = kws.create_stream("/".join(lines))
        s.accept_waveform(sr, np.concatenate([sil, audio, sil, np.zeros(sr, dtype=np.float32)]))
        s.input_finished()
        hits = []
        while kws.is_ready(s):
            kws.decode_stream(s)
            r = kws.get_result(s)
            if r:
                hits.append(r)
                kws.reset_stream(s)

        got = hits[0] if hits else None
        if got is None:
            verdict = "MISS "
            counts["nothing fired"] += 1
        elif got[:-1] == syl:
            verdict = "OK   "
            counts["right syllable"] += 1
            counts["  ...and right tone"] += got == f"{syl}{tone}"
        else:
            verdict = "WRONG"
            counts["wrong syllable fired"] += 1
            wrong[f"{syl} -> {got[:-1]}"] += 1
        if not a.quiet:
            print(f"{wav.name:14s} {verdict} heard: {got or '-'}")

    total = sum(v for k, v in counts.items() if not k.startswith(("  ", "skipped")))
    print(f"\n=== {total} files, threshold={a.threshold}, score={a.score} ===")
    for k in ["right syllable", "  ...and right tone", "nothing fired", "wrong syllable fired",
              "skipped (no token in model)", "skipped (bad name)"]:
        if counts[k]:
            pct = f" ({100 * counts[k] / total:.0f}%)" if total and not k.startswith("skipped") else ""
            print(f"{k:28s} {counts[k]}{pct}")
    if wrong:
        print("\nMost common confusions:")
        for k, v in wrong.most_common(10):
            print(f"  {k}  x{v}")


if __name__ == "__main__":
    sys.exit(main())