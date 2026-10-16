from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from app.core.logger import logger

from .constants import MAX_UPLOAD_BYTES
from .schemas import ScoreResponse, WordScoreResponse
from .services.audio import AudioError
from .services.scorer import TargetError, score_recording, score_word

router = APIRouter(prefix="/pronunciation", tags=["pronunciation"])


async def _run_scorer(scorer, audio: UploadFile, target: str):
    """Shared by both routes: checks the upload, runs `scorer(data, target)` off the event loop
    (scoring is CPU work, so other requests aren't blocked), and turns failures into HTTP errors."""
    data = await audio.read()
    if not data:
        raise HTTPException(400, "empty audio")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "audio too large")
    try:
        return await run_in_threadpool(scorer, data, target)
    except (TargetError, AudioError) as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        # e.g. ffmpeg missing, a model failing to load or run: log the traceback, tell the client why
        logger.exception("pronunciation %s failed (target=%r, %d bytes)", scorer.__name__, target, len(data))
        raise HTTPException(500, f"scoring failed: {e}")


@router.post("/score", response_model=ScoreResponse)
async def score(audio: UploadFile = File(...), target: str = Form(...)):
    """Score one recorded syllable against the syllable the learner was asked to say.

    Form fields: `audio` (the recording, any common format) and `target` (pinyin with tone number,
    e.g. "zhuang4", "lv4", "yi1")."""
    return await _run_scorer(score_recording, audio, target)


@router.post("/score_word", response_model=WordScoreResponse)
async def score_word_route(audio: UploadFile = File(...), target: str = Form(...)):
    """Score several syllables said in one take, e.g. a two-syllable word.

    Form fields: `audio` (the recording) and `target` (space-separated pinyin with tone numbers,
    e.g. "cao3 mei2"). The take is split into syllables and each is scored like /score."""
    return await _run_scorer(score_word, audio, target)
