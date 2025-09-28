from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import uvicorn
import os
from app.api import patients, login, recording
from app.core.database import create_tables

app = FastAPI(title="AI Scribe Backend", version="1.0.0")

static_dir = "static"
audio_dir = os.path.join(static_dir, "audio")
os.makedirs(audio_dir, exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")



@app.on_event("startup")
async def startup_event():
    create_tables()


app.include_router(patients.router, prefix="/v1", tags=["patients"])
app.include_router(login.router, prefix="/auth", tags=["authentication"])
app.include_router(recording.router, prefix="/v1", tags=["recording"])

@app.get("/")
async def root():
    return {"message": "AI Scribe Backend API", "version": "1.0.0"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)
    