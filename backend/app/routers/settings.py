"""Settings: account, privacy/consent, integrations & data deletion."""
import json
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import config
from ..database import get_db
from ..models import Application, CvFile, Integration, Job, Preferences, Profile, SearchLog, User
from ..security import get_current_user, verify_password
from ..services.job_sources import SOURCES

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/integrations")
def list_integrations(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Honest per-source configuration screen. Keys live only on the server; the
    client sees booleans, never secret values."""
    auths = {i.key: i for i in (user.integrations or [])}
    out = []
    for key, src in SOURCES.items():
        cfg = src.configured()
        row = auths.get(key)
        out.append({
            "key": key, "label": src.label, "description": src.description,
            "docs_url": src.docs_url, "needs_credentials": src.needs_credentials,
            "server_configured": cfg,
            "state": ("active" if cfg else
                      ("restricted" if key in ("linkedin", "indeed") else "not_connected")),
            "user_authorized": bool(row and row.authorized),
            "authorized_at": row.authorized_at.isoformat() if row and row.authorized_at else None,
        })
    return {"sources": out,
            "ai_configured": bool(config.AI_API_KEY),
            "note": ("API keys are stored only in backend environment variables (.env). "
                     "They are never sent to or visible in the browser.")}


class AuthorizeIn(BaseModel):
    source_key: str
    authorize: bool


@router.post("/integrations/authorize")
def authorize(body: AuthorizeIn, user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    if body.source_key not in SOURCES:
        raise HTTPException(422, "Unknown integration")
    from datetime import datetime, timezone
    row = next((i for i in (user.integrations or []) if i.key == body.source_key), None)
    if row is None:
        row = Integration(user_id=user.id, key=body.source_key)
        db.add(row)
    row.authorized = body.authorize
    row.authorized_at = datetime.now(timezone.utc) if body.authorize else None
    db.commit()
    return {"ok": True, "source_key": body.source_key, "authorized": row.authorized}


class PasswordIn(BaseModel):
    current_password: str
    new_password: str


@router.post("/change-password")
def change_password(body: PasswordIn, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(401, "Current password is incorrect.")
    if len(body.new_password) < 8:
        raise HTTPException(422, "New password must be at least 8 characters.")
    from ..security import hash_password
    user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"ok": True}


class DeleteIn(BaseModel):
    password: str
    confirm_text: str   # must equal "DELETE MY DATA"


@router.delete("/account")
def delete_account(body: DeleteIn, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """Full data deletion: DB rows + stored CV files."""
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Password is incorrect.")
    if body.confirm_text.strip() != "DELETE MY DATA":
        raise HTTPException(422, "Type DELETE MY DATA exactly to confirm.")
    for cv in db.query(CvFile).filter(CvFile.user_id == user.id):
        p = Path(cv.stored_path)
        p.unlink(missing_ok=True)
    # cascade deletes handle profiles/prefs/jobs/apps/integrations/logs
    db.delete(user)
    db.commit()
    return {"ok": True, "message": "Account, CV files and all related data deleted."}


@router.get("/privacy")
def privacy_summary(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {
        "consent_accepted": user.consent_accepted_at is not None,
        "data_stored": {
            "cv_files": db.query(CvFile).filter(CvFile.user_id == user.id).count(),
            "jobs_seen": db.query(Job).filter(Job.user_id == user.id).count(),
            "applications": db.query(Application).filter(Application.user_id == user.id).count(),
            "search_logs": db.query(SearchLog).filter(SearchLog.user_id == user.id).count(),
        },
        "storage": f"SQLite DB on this server ({Path(config.DB_PATH).name}) + CV files in {config.UPLOAD_DIR.name}/",
        "controls": ["Export profile JSON below", "Delete account wipes DB rows and CV files",
                     "Revoke consent via POST /api/auth/consent {accept:false}"],
    }


@router.get("/export")
def export_data(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Simple data export (profile + prefs + jobs + applications)."""
    def jl(v):
        try:
            return json.loads(v or "[]")
        except json.JSONDecodeError:
            return []
    prof = None
    if user.profile:
        p = user.profile
        prof = {k: getattr(p, k) for k in ("full_name", "email", "phone", "location", "headline")}
        prof.update({k: jl(getattr(p, k)) for k in
                     ("summary", "education", "experience", "skills", "certifications", "languages")})
    apps = [{"id": a.id, "job_id": a.job_id, "status": a.status,
             "confirmation_ref": a.confirmation_ref, "created_at": a.created_at.isoformat()}
            for a in db.query(Application).filter(Application.user_id == user.id)]
    jobs = [{"id": j.id, "title": j.title, "company": j.company, "url": j.url,
             "match_score": j.match_score, "status": j.status}
            for j in db.query(Job).filter(Job.user_id == user.id)]
    return {"email": user.email, "full_name": user.full_name, "mode": user.mode,
            "profile": prof, "preferences": _prefs(user.preferences),
            "jobs": jobs, "applications": apps}


def _prefs(pr):
    if pr is None:
        return None
    from ..worker import prefs_dict
    return prefs_dict(pr)
