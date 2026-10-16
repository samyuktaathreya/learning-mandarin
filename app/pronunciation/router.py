from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from .constants import MAX_UPLOAD_BYTES
from .schemas import ScoreResponse
from .services.audio import AudioError
from .services.scorer import TargetError, score_recording

router = APIRouter(prefix="/pronunciation", tags=["pronunciation"])


@router.post("/score", response_model=ScoreResponse)
async def score(audio: UploadFile = File(...), target: str = Form(...)):
    """Score one recorded syllable against the syllable the learner was asked to say.

    Form fields: `audio` (the recording, any common format) and `target` (pinyin with tone number,
    e.g. "zhuang4", "lv4", "yi1")."""
    data = await audio.read()
    if not data:
        raise HTTPException(400, "empty audio")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "audio too large")
    try:
        # scoring is CPU work: run it off the event loop so other requests aren't blocked
        return await run_in_threadpool(score_recording, data, target)
    except (TargetError, AudioError) as e:
        raise HTTPException(400, str(e))
