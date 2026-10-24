"""The scorer for the server: ONNX models, no PyTorch. Same decisions as score.py (shared scorer_core).

    scorer = OnnxScorer(["models/stage6b", "models/stage7_hubert"], pitch_model="models/tone23_pitch.joblib")
    result = scorer.score(wav_or_path, "zhuang4")

Each model folder comes from export_onnx.py: model_int8.onnx + onnx.json.
"""
import json
import os

import numpy as np

from .scorer_core import (DEFAULT_THRESHOLD, PITCH_WEIGHT, apply_pitch, combine, head_probs, judge,
                         prepare)


class OnnxScorer:
    def __init__(self, model_dirs, pitch_model=None, pitch_weight=PITCH_WEIGHT, threads=None):
        import onnxruntime as ort
        if isinstance(model_dirs, str):
            model_dirs = [model_dirs]
        opts = ort.SessionOptions()
        if threads:
            opts.intra_op_num_threads = threads
            opts.inter_op_num_threads = 1
        self.models = []
        for d in model_dirs:
            cfg = json.load(open(os.path.join(d, "onnx.json")))
            sess = ort.InferenceSession(os.path.join(d, cfg["file"]), opts, providers=["CPUExecutionProvider"])
            self.models.append((sess, cfg))
        self.pitch = None
        if pitch_model:
            from .pitch_tone import PitchTone
            self.pitch = PitchTone(pitch_model)
        self.pitch_weight = pitch_weight

    prepare = staticmethod(prepare)

    def logits(self, wav):
        """Runs every model once on an already prepared wav -> one {head: (T, C) logits} per model.
        This is the expensive part; everything else here is cheap numpy on its output."""
        out = []
        for sess, cfg in self.models:
            x = wav.astype(np.float32)
            if cfg["do_normalize"]:
                x = (x - x.mean()) / np.sqrt(x.var() + 1e-7)
            res = sess.run(None, {"input_values": x[None, :]})
            out.append({h: res[i][0] for i, h in enumerate(cfg["heads"])})
        return out

    def model_probs(self, wav):
        return [{h: head_probs(z, h) for h, z in m.items()} for m in self.logits(wav)]

    @staticmethod
    def frame_probs_from(logits):
        """Per-frame unit probabilities from logits(), averaged over the models: {head: (T, C), blank
        at 0}. Used to find syllable boundaries (split.py)."""
        per_model = []
        for m in logits:
            out = {}
            for h, z in m.items():
                z = z.astype(np.float64)
                p = np.exp(z - z.max(axis=-1, keepdims=True))
                out[h] = p / p.sum(axis=-1, keepdims=True)
            per_model.append(out)
        T = min(p[h].shape[0] for p in per_model for h in p)
        return {h: sum(p[h][:T] for p in per_model) / len(per_model) for h in per_model[0]}

    def frame_probs(self, wav):
        """`wav` is already prepared. Runs the models; use frame_probs_from() if you have logits."""
        return self.frame_probs_from(self.logits(wav))

    def score_slice(self, logits, start_frame, end_frame, clip, target, threshold=DEFAULT_THRESHOLD,
                    top_k=3):
        """Score one syllable of a multi-syllable take without running the models again: frames
        [start_frame, end_frame) of logits computed on the whole take (end_frame None = to the end).
        The pitch vote still comes from the syllable's own audio clip (cheap, no model)."""
        per_model = []
        for m in logits:
            probs = {}
            for h, z in m.items():
                T = z.shape[0]
                a = min(start_frame, T - 1)
                b = T if end_frame is None else max(min(end_frame, T), a + 1)
                probs[h] = head_probs(z[a:b], h)
            per_model.append(probs)
        vote = self.pitch_vote(prepare(clip))
        return judge(apply_pitch(combine(per_model), vote, self.pitch_weight), target, threshold, top_k)

    def pitch_vote(self, wav):
        return self.pitch.vote(wav) if self.pitch is not None else None

    def probs(self, audio):
        wav = prepare(audio)
        return apply_pitch(combine(self.model_probs(wav)), self.pitch_vote(wav), self.pitch_weight)

    def score(self, audio, target, threshold=DEFAULT_THRESHOLD, top_k=3):
        return judge(self.probs(audio), target, threshold, top_k)