"""Global AI Job Finder & Auto-Apply Agent — FastAPI entry point."""
import os
import traceback
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .database import init_db
from .routers import applications, auth, cv, jobs, settings as settings_router
from .worker import start_background_worker

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_background_worker()
    yield
    # background thread is daemon; nothing to tear down


app = FastAPI(title="Global AI Job Finder", version="1.0.0", lifespan=lifespan)

# CORS: same-origin static hosting by default. Only open up explicitly if needed.
extra_origins = [o for o in os.getenv("CORS_ORIGINS", "").split(",") if o]
if extra_origins:
    app.add_middleware(CORSMiddleware, allow_origins=extra_origins,
                       allow_methods=["*"], allow_headers=["Authorization", "Content-Type"])


@app.exception_handler(Exception)
async def unhandled(request: Request, err: Exception):
    traceback.print_exc()
    return JSONResponse({"detail": f"Server error: {type(err).__name__}"}, status_code=500)


app.include_router(auth.router)
app.include_router(cv.router)
app.include_router(jobs.router)
app.include_router(applications.router)
app.include_router(settings_router.router)


@app.get("/api/health")
def health():
    return {"ok": True, "ai_configured": bool(config.AI_API_KEY),
            "dev_secret_in_use": config.IS_DEV_SECRET}


# Serve the SPA shell + assets
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        # serve real files (e.g. app.js, styles.css) if present, else SPA shell
        candidate = STATIC_DIR / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        if full_path.startswith(("api/",)):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return FileResponse(STATIC_DIR / "index.html")
