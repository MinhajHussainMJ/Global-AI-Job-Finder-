"""Application agent endpoints: prepare documents, track statuses, dashboard."""
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Application, CvFile, Integration, Job, SearchLog, User
from ..security import get_current_user
from ..services import ai_engine
from ..worker import prefs_dict, profile_dict

router = APIRouter(prefix="/api/applications", tags=["applications"])

VALID_STATUSES = {"prepared", "awaiting_user", "submitted", "interview", "rejected", "expired", "failed"}


def _app_out(a: Application, job: Job | None = None) -> dict:
    return {"id": a.id, "job_id": a.job_id, "status": a.status,
            "cover_letter": a.cover_letter, "tailored_cv_summary": a.tailored_cv_summary,
            "answers": json.loads(a.answers or "[]"), "pause_reason": a.pause_reason,
            "confirmation_ref": a.confirmation_ref,
            "submitted_docs": json.loads(a.submitted_docs or "[]"),
            "error": a.error, "attempts": a.attempts,
            "follow_up_at": a.follow_up_at.isoformat() if a.follow_up_at else None,
            "created_at": a.created_at.isoformat(), "updated_at": a.updated_at.isoformat(),
            "job": ({"title": job.title, "company": job.company, "location": job.location,
                     "url": job.url, "match_score": job.match_score, "source": job.source,
                     "deadline": job.deadline.isoformat() if job.deadline else None}
                    if job else None)}


@router.get("")
def list_applications(status: str = Query(""), q: str = Query(""),
                      page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=100),
                      user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = db.query(Application).filter(Application.user_id == user.id)
    if status:
        query = query.filter(Application.status == status)
    rows = query.order_by(Application.updated_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
    jobs = {j.id: j for j in db.query(Job).filter(Job.user_id == user.id,
                                                  Job.id.in_([r.job_id for r in rows] or [-1]))}
    items = [_app_out(r, jobs.get(r.job_id)) for r in rows]
    if q:
        ql = q.lower()
        items = [i for i in items if i["job"] and (ql in i["job"]["title"].lower()
                                                   or ql in i["job"]["company"].lower())]
    total = query.count()
    return {"total": total, "page": page, "items": items}


class PrepareIn(BaseModel):
    job_id: int


@router.post("/prepare")
def prepare_for_job(body: PrepareIn, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """Generate cover letter + tailored-CV guidance + truthful answers for one job."""
    job = db.query(Job).filter(Job.id == body.job_id, Job.user_id == user.id).first()
    if job is None:
        raise HTTPException(404, "Job not found")
    if user.profile is None:
        raise HTTPException(422, "Upload & confirm your CV/profile first.")
    pd, prof = prefs_dict(user.preferences) if user.preferences else {}, profile_dict(user.profile)
    jd = {"title": job.title, "company": job.company, "location": job.location,
          "description": job.description, "url": job.url}
    letter, engine = ai_engine.generate_cover_letter(prof, jd, pd)
    cv_notes, _ = ai_engine.generate_tailored_cv_notes(prof, jd)
    answers = ai_engine.answer_application_questions(prof, jd)
    app = db.query(Application).filter(Application.user_id == user.id, Application.job_id == job.id).first()
    if app is None:
        app = Application(user_id=user.id, job_id=job.id, status="prepared")
        db.add(app)
    current_cv = next((c for c in user.cv_files if c.is_current), None)
    app.cover_letter = letter
    app.tailored_cv_summary = cv_notes
    app.answers = json.dumps(answers)
    app.submitted_docs = json.dumps([current_cv.filename] if current_cv else [])
    if app.status in ("prepared", "awaiting_user"):
        app.status = "prepared"
    app.pause_reason = (f"Documents generated ({engine}). Submit via the original application "
                        "link; auto-submission needs an authorized integration for this source.")
    db.commit()
    return _app_out(app, job)


class UpdateStatusIn(BaseModel):
    status: str
    confirmation_ref: str = ""


@router.patch("/{app_id}")
def update_status(app_id: int, body: UpdateStatusIn, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """User marks outcomes truthfully. 'submitted' requires a confirmation reference —
    the system never marks submitted without actual confirmation."""
    if body.status not in VALID_STATUSES:
        raise HTTPException(422, f"status must be one of {sorted(VALID_STATUSES)}")
    app = db.query(Application).filter(Application.id == app_id, Application.user_id == user.id).first()
    if app is None:
        raise HTTPException(404, "Application not found")
    if body.status == "submitted" and not body.confirmation_ref.strip():
        raise HTTPException(422, "Provide the confirmation reference/receipt you received when "
                                 "submitting. Applications are marked submitted only with real "
                                 "confirmation details.")
    app.status = body.status
    if body.confirmation_ref:
        app.confirmation_ref = body.confirmation_ref.strip()[:500]
    if body.status == "submitted":
        # schedule a follow-up reminder in 10 days
        app.follow_up_at = datetime.now(timezone.utc) + timedelta(days=10)
    db.commit()
    job = db.get(Job, app.job_id)
    return _app_out(app, job)


@router.post("/{app_id}/retry")
def retry_prepare(app_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    app = db.query(Application).filter(Application.id == app_id, Application.user_id == user.id).first()
    if app is None:
        raise HTTPException(404, "Application not found")
    app.attempts += 1
    app.error = ""
    if app.status == "failed":
        app.status = "prepared"
    db.commit()
    job = db.get(Job, app.job_id)
    return _app_out(app, job)


class AnswersIn(BaseModel):
    answers: list[dict]


@router.put("/{app_id}/answers")
def edit_answers(app_id: int, body: AnswersIn, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    app = db.query(Application).filter(Application.id == app_id, Application.user_id == user.id).first()
    if app is None:
        raise HTTPException(404, "Application not found")
    app.answers = json.dumps(body.answers[:30])
    db.commit()
    return _app_out(app, db.get(Job, app.job_id))


class LetterIn(BaseModel):
    cover_letter: str


@router.put("/{app_id}/letter")
def edit_letter(app_id: int, body: LetterIn, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    app = db.query(Application).filter(Application.id == app_id, Application.user_id == user.id).first()
    if app is None:
        raise HTTPException(404, "Application not found")
    app.cover_letter = body.cover_letter[:20000]
    db.commit()
    return _app_out(app, db.get(Job, app.job_id))


@router.get("/dashboard")
def dashboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    jobs_q = db.query(Job).filter(Job.user_id == user.id)
    discovered = jobs_q.count()
    matched = jobs_q.filter(Job.status == "matched").count()
    expired = jobs_q.filter(Job.status == "expired").count()
    apps = db.query(Application).filter(Application.user_id == user.id)
    prepared = apps.filter(Application.status.in_(["prepared", "awaiting_user"])).count()
    submitted = apps.filter(Application.status == "submitted").count()
    awaiting = apps.filter(Application.status == "awaiting_user").count()
    interviews = apps.filter(Application.status == "interview").count()
    rejections = apps.filter(Application.status == "rejected").count()
    failed = apps.filter(Application.status == "failed").count()
    now = datetime.now(timezone.utc)
    upcoming_deadlines = [{"job_id": j.id, "title": j.title, "company": j.company,
                           "expires_at": j.expires_at.isoformat()}
                          for j in jobs_q.filter(Job.expires_at != None,   # noqa: E711
                                                 Job.expires_at > now,
                                                 Job.expires_at < now + timedelta(days=7),
                                                 Job.status.in_(["new", "matched"]))
                          .order_by(Job.expires_at).limit(10)]
    followups = [{"app_id": a.id, "job_id": a.job_id, "follow_up_at": a.follow_up_at.isoformat()}
                 for a in apps.filter(Application.follow_up_at != None,      # noqa: E711
                                      Application.follow_up_at <= now + timedelta(days=3))
                 .order_by(Application.follow_up_at).limit(10)]
    recent_apps = apps.order_by(Application.updated_at.desc()).limit(8).all()
    jmap = {j.id: j for j in db.query(Job).filter(Job.user_id == user.id,
               Job.id.in_([a.job_id for a in recent_apps] or [-1]))}
    last_log = (db.query(SearchLog).filter_by(user_id=user.id)
                .order_by(SearchLog.started_at.desc()).first())
    return {
        "counts": {"discovered": discovered, "matched": matched, "prepared": prepared,
                   "submitted": submitted, "awaiting_user": awaiting, "interviews": interviews,
                   "rejections": rejections, "expired_jobs": expired, "failed": failed},
        "upcoming_deadlines": upcoming_deadlines,
        "follow_ups_due": followups,
        "recent_applications": [_app_out(a, jmap.get(a.job_id)) for a in recent_apps],
        "auto_apply_mode": user.preferences.auto_apply_mode if user.preferences else None,
        "profile_verified": bool(user.profile and user.profile.verified),
        "has_cv": any(c.is_current for c in user.cv_files),
        "last_search": {"started_at": last_log.started_at.isoformat(), "found": last_log.found,
                        "added": last_log.added} if last_log else None,
    }
