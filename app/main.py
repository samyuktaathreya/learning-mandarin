# run: uvicorn main:app --host 0.0.0.0 --port 8000 --reload
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from database import engine, Base, init_db
import models.user  # registers models with Base.metadata before create_all()

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
    print("Warning: ../frontend/public not found. Static files bypassed.")

@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    init_db()
    # clear any leftover audio files from previous sessions
    import shutil
    cache_dir = "./audio_cache"
    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)
        os.makedirs(cache_dir)
    print("Audio cache cleared on startup.")

# TODO: include routers as you build them
# from api.v1.endpoints.auth import router as auth_router
# app.include_router(auth_router)

from api.v1.endpoints.practice import router as practice_router
app.include_router(practice_router)

from api.v1.endpoints.audio import router as audio_router
app.include_router(audio_router)

@app.get("/")
def root():
    return {"message": "Server is running!"}