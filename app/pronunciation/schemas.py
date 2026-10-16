from typing import List, Tuple

from pydantic import BaseModel


class PartResult(BaseModel):
    target: str              # what the learner was asked for, as shown to them (e.g. "zh", "uang", "4")
    heard: str               # the model's top guess
    target_unit: str
    heard_unit: str
    score: float             # probability credited to the target (plus forgiven partners)
    correct: bool            # score >= threshold
    forgiven: bool           # the top guess was an accepted partner (e.g. ang for eng): don't say "sounded like"
    top: List[Tuple[str, float]]


class PartsResult(BaseModel):
    initial: PartResult
    final: PartResult
    tone: PartResult


class ScoreResponse(BaseModel):
    target: str
    parts: PartsResult
    no_speech: bool          # nothing that sounds like speech was found
    understood: bool         # all three parts correct and speech found
    ms: int                  # time spent scoring on the server
