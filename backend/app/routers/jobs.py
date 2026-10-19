"""Job preferences + job discovery endpoints."""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Application, Job, Preferences, SearchLog, User
from ..security import get_current_user
from ..services.job_sources import SOURCES
from ..worker import run_user_search

router = APIRouter(prefix="/api", tags=["jobs"])


def _prefs_out(pr: Preferences) -> dict:
    def jl(v):
        try:
            return json.loads(v or "[]")
        except json.JSONDecodeError:
            return []
    return {"job_titles": jl(pr.job_titles), "fields": jl(pr.fields),
            "countries": jl(pr.countries), "cities": jl(pr.cities), "worldwide": pr.worldwide,
            "work_modes": jl(pr.work_modes), "employment_types": jl(pr.employment_types),
            "min_salary": pr.min_salary, "currency": pr.currency,
            "qualifications": jl(pr.qualifications),
            "min_experience_years": pr.min_experience_years,
            "visa_sponsorship_required": pr.visa_sponsorship_required,
            "open_to_relocation": pr.open_to_relocation, "english_ok": pr.english_ok,
            "sources": jl(pr.sources), "search_frequency_hours": pr.search_frequency_hours,
            "auto_apply_mode": pr.auto_apply_mode,
            "auto_apply_authorized": pr.auto_apply_authorized,
            "max_auto_applications_per_day": pr.max_auto_applications_per_day}


class PrefsIn(BaseModel):
    job_titles: list[str] = []
    fields: list[str] = []
    countries: list[str] = ["Worldwide"]
    cities: list[str] = []
    worldwide: bool = True
    work_modes: list[str] = ["Remote", "Hybrid", "Onsite"]
    employment_types: list[str] = ["Full-time"]
    min_salary: float = 0
    currency: str = "USD"
    qualifications: list[str] = []
    min_experience_years: float = 0
    visa_sponsorship_required: bool = False
    open_to_relocation: bool = False
    english_ok: bool = True
    sources: list[str] = ["remotive"]
    search_frequency_hours: int = 12
    auto_apply_mode: str = "find_only"
    auto_apply_authorized: bool = False
    max_auto_applications_per_day: int = 5


@router.get("/preferences")
def get_preferences(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.preferences is None:
        return None
    return _prefs_out(user.preferences)


@router.put("/preferences")
def save_preferences(body: PrefsIn, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    bad = [s for s in body.sources if s not in SOURCES]
    if bad:
        raise HTTPException(422, f"Unknown job sources: {bad}")
    if body.auto_apply_mode not in ("find_only", "prepare", "auto_apply"):
        raise HTTPException(422, "auto_apply_mode must be find_only | prepare | auto_apply")
    if body.auto_apply_mode == "auto_apply" and not body.auto_apply_authorized:
        raise HTTPException(422,
                            "Auto-Apply requires explicit authorization of the submission rules "
                            "(checkbox) before it can be saved.")
    pr = user.preferences or Preferences(user_id=user.id)
    data = body.model_dump()
    lists = ("job_titles", "fields", "countries", "cities", "work_modes", "employment_types",
             "qualifications", "sources")
    for k, v in data.items():
        setattr(pr, k, json.dumps(v) if k in lists else v)
    db.add(pr)
    db.commit()
    return _prefs_out(pr)


EXAMPLE_PREFS = {
    "job_titles": ["AI Developer", "Python Developer", "AI Automation Specialist",
                   "Junior Machine Learning Engineer", "Software Developer",
                   "Computer Science Teacher", "IT Teacher"],
    "fields": ["Artificial Intelligence", "Software Development", "Education / Teaching"],
    "countries": ["Pakistan", "United Kingdom", "Germany", "Netherlands", "United Arab Emirates",
                  "Saudi Arabia", "Qatar", "Canada", "United States"],
    "worldwide": True,
    "cities": [],
    "work_modes": ["Remote", "Hybrid", "Onsite"],
    "employment_types": ["Full-time", "Contract", "Part-time"],
    "min_salary": 0, "currency": "USD",
    "qualifications": ["Bachelor's degree (recognized equivalently abroad)",
                       "Teaching qualification for school roles (B.Ed/PGCE/TEFL as required)"],
    "min_experience_years": 0,
    "visa_sponsorship_required": True,
    "open_to_relocation": True,
    "english_ok": True,
    "sources": ["remotive", "teachaway"],
    "search_frequency_hours": 12,
    "auto_apply_mode": "find_only",
    "auto_apply_authorized": False,
    "max_auto_applications_per_day": 5,
}


@router.post("/preferences/example")
def load_example_prefs(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Section 8 example preference set — fully editable afterwards."""
    pr = user.preferences or Preferences(user_id=user.id)
    for k, v in EXAMPLE_PREFS.items():
        setattr(pr, k, json.dumps(v) if isinstance(v, list) else v)
    db.add(pr)
    db.commit()
    return {"message": "Example preferences loaded — adjust to your real targets.",
            "preferences": _prefs_out(pr)}


@router.get("/sources")
def list_sources(user: User = Depends(get_current_user)):
    """Honest status of every integration: working / needs credentials / restricted."""
    out = []
    for key, src in SOURCES.items():
        cfg = src.configured()
        status = "ready" if (cfg and not src.needs_credentials) else \
                 ("connected" if cfg else ("needs_credentials" if src.needs_credentials else "ready"))
        if key == "linkedin":
            status = "restricted_partner_only"
        if key == "indeed" and not cfg:
            status = "restricted_partner_only"
        out.append({"key": key, "label": src.label, "configured": cfg, "status": status,
                    "description": src.description, "docs_url": src.docs_url})
    return out


def _job_out(j: Job, app_status: str | None = None) -> dict:
    def jl(v):
        try:
            return json.loads(v or "[]")
        except json.JSONDecodeError:
            return []
    return {"id": j.id, "source": j.source, "title": j.title, "company": j.company,
            "location": j.location, "country": j.country, "url": j.url, "salary": j.salary,
            "job_type": j.job_type, "category": j.category, "remote": j.remote,
            "visa_note": j.visa_note,
            "posted_at": j.posted_at.isoformat() if j.posted_at else None,
            "deadline": j.deadline.isoformat() if j.deadline else None,
            "expires_at": j.expires_at.isoformat() if j.expires_at else None,
            "match_score": j.match_score, "match_reasons": jl(j.match_reasons),
            "missing_skills": jl(j.missing_skills), "eligibility_notes": jl(j.eligibility_notes),
            "status": j.status, "bookmarked": j.bookmarked,
            "discovered_at": j.discovered_at.isoformat(),
            "description_preview": (j.description or "")[:400],
            "application_status": app_status}


@router.get("/jobs")
def list_jobs(status: str = Query(""), min_score: int = Query(0), bookmarked: bool = Query(False),
              q: str = Query(""), page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=100),
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = db.query(Job).filter(Job.user_id == user.id)          # strict ownership
    if status:
        query = query.filter(Job.status == status)
    else:
        query = query.filter(Job.status != "hidden")
    if min_score:
        query = query.filter(Job.match_score >= min_score)
    if bookmarked:
        query = query.filter(Job.bookmarked == True)  # noqa: E712
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(Job.title.ilike(like) | Job.company.ilike(like) | Job.location.ilike(like))
    total = query.count()
    apps = {a.job_id: a.status for a in db.query(Application).filter(Application.user_id == user.id)}
    rows = (query.order_by(Job.match_score.desc().nullslast(), Job.discovered_at.desc())
            .offset((page - 1) * per_page).limit(per_page).all())
    return {"total": total, "page": page, "per_page": per_page,
            "items": [_job_out(j, apps.get(j.id)) for j in rows]}


@router.get("/jobs/{job_id}")
def job_detail(job_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    j = db.query(Job).filter(Job.id == job_id, Job.user_id == user.id).first()
    if j is None:
        raise HTTPException(404, "Job not found")
    app = db.query(Application).filter(Application.user_id == user.id, Application.job_id == j.id).first()
    out = _job_out(j, app.status if app else None)
    out["description"] = j.description
    return out


@router.post("/jobs/{job_id}/bookmark")
def toggle_bookmark(job_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    j = db.query(Job).filter(Job.id == job_id, Job.user_id == user.id).first()
    if j is None:
        raise HTTPException(404, "Job not found")
    j.bookmarked = not j.bookmarked
    db.commit()
    return {"bookmarked": j.bookmarked}


@router.post("/jobs/{job_id}/hide")
def hide_job(job_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    j = db.query(Job).filter(Job.id == job_id, Job.user_id == user.id).first()
    if j is None:
        raise HTTPException(404, "Job not found")
    j.status = "hidden"
    db.commit()
    return {"ok": True}


@router.post("/search/run")
def trigger_search(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.preferences is None:
        raise HTTPException(422, "Save your job preferences first.")
    entry = run_user_search(db, user, triggered_by="manual")
    errs = json.loads(entry.errors or "[]")
    return {"found": entry.found, "added": entry.added, "duplicates_removed": entry.duplicates,
            "errors": errs,
            "note": ("New listings are scored against your profile automatically."
                     if entry.added else "No new unique listings this run (see errors/warnings if any).")}


@router.get("/search/logs")
def search_logs(limit: int = Query(15, le=50), user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    rows = (db.query(SearchLog).filter(SearchLog.user_id == user.id)
            .order_by(SearchLog.started_at.desc()).limit(limit).all())
    return [{"started_at": r.started_at.isoformat(),
             "finished_at": r.finished_at.isoformat() if r.finished_at else None,
             "found": r.found, "added": r.added, "duplicates": r.duplicates,
             "errors": json.loads(r.errors or "[]"), "triggered_by": r.triggered_by} for r in rows]
