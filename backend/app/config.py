"""Application configuration.

All secrets (JWT secret, AI keys, job-source API keys) live on the server only,
loaded from environment variables or a local .env file. They are NEVER sent to
the frontend; the frontend only sees whether an integration is configured.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent          # backend/
DATA_DIR = Path(os.getenv("JOBFINDER_DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "jobfinder.db"

for candidate in (BASE_DIR / ".env", Path.cwd() / ".env"):
    if candidate.exists():  # minimal .env loader (no extra dependency)
        for line in candidate.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

SECRET_KEY = os.getenv("SECRET_KEY", "")
if not SECRET_KEY:
    # Dev fallback so the app boots locally; production deployments MUST set it.
    SECRET_KEY = "dev-insecure-secret-change-me"
    IS_DEV_SECRET = True
else:
    IS_DEV_SECRET = False

JWT_ALGORITHM = "HS256"
TOKEN_TTL_HOURS = int(os.getenv("TOKEN_TTL_HOURS", "72"))

# --- AI provider (optional). Works with OpenAI-compatible chat APIs. ---------
AI_API_BASE = os.getenv("AI_API_BASE", "https://api.openai.com/v1")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "gpt-4o-mini")

# --- Job source credentials (server-side only) -------------------------------
ADZUNA_APP_ID = os.getenv("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.getenv("ADZUNA_APP_KEY", "")
INDEED_PUBLISHER_KEY = os.getenv("INDEED_PUBLISHER_KEY", "")
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "")

SEARCH_INTERVAL_MINUTES = int(os.getenv("SEARCH_INTERVAL_MINUTES", "360"))
MAX_JOBS_PER_SOURCE_PER_RUN = int(os.getenv("MAX_JOBS_PER_SOURCE_PER_RUN", "60"))

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
