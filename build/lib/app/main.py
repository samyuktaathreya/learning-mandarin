# run: python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
import os
import shutil
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from core.database import engine, Base
from scripts.seed import init_db
from session_log import reset_log

from textbook.models import Base as TextbookBase
from textbook.db_utils import engine as textbook_engine
from scripts.seed import init_db

from app.core.logger import logger

app = FastAPI(docs_url="/api/docs", openapi_url="/api/openapi.json")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # replace with frontend URL in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if os.path.exists("../frontend/public"):
    app.mount("/api/static", StaticFiles(directory="../frontend/public"), name="static")
else:
    logger.debug("Warning: ../frontend/public not found. Static files bypassed.")

@app.on_event("startup")
def on_startup():
    # Core/session database
    Base.metadata.create_all(bind=engine)
    
    # Textbook database (vocab, sentences, questions, etc.)
    TextbookBase.metadata.create_all(bind=textbook_engine)
    
    init_db()
    reset_log()
    cache_dir = "./audio_cache"
    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)
    os.makedirs(cache_dir, exist_ok=True)
    logger.debug("Audio cache cleared on startup.")

# --- Feature-Based Routers ---

from session.router import router as session_router
app.include_router(session_router)

from characters.router import router as characters_router
app.include_router(characters_router)

from shared.routers.audio import router as audio_router
app.include_router(audio_router)

from shared.routers.grading import router as grading_router
app.include_router(grading_router)

# --- Legacy/Unmigrated Routers ---
# (These remain in api/v1/endpoints as they don't have new feature folders yet)

from api.v1.endpoints.tools import router as tools_router
app.include_router(tools_router)

from api.v1.endpoints.voice_agent import router as voice_agent_router
app.include_router(voice_agent_router)

@app.get("/")
def root():
    return {"message": "Server is running!"}