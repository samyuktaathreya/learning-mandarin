from fastapi import APIRouter
from fastapi.responses import JSONResponse

from pinyin_utils import to_numbered_pinyin

router = APIRouter()


@router.post("/api/pinyin")
async def get_pinyin(payload: dict):
    text = payload.get("text", "")
    return JSONResponse({"pinyin": to_numbered_pinyin(text)})