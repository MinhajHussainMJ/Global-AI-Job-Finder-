"""Background worker: periodic job searches, dedup, AI matching, application
preparation per auto-apply mode, expiry cleanup, error logging.

Runs in-process on a daemon thread (zero extra infrastructure / cost). For
multi-server deployments replace with Celery/RQ using the same task functions.
"""
import hashlib
import json
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

from . import config
from .database import SessionLocal
from .models import Application, Integration, Job, Preferences, Profile, SearchLog, User
from .services import ai_engine, job_sources

TICK_SECONDS = 10 * 60            # scheduler wake-up interval
MATCH_THRESHOLD = 55              # score >= threshold => "matched"
PREPARE_THRESHOLD = 70            # score >= threshold => agent prepares application

_loop_started = False
_lock = threading.Lock()


def log(msg: str):
    print(f"[worker {datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}")


def start_background_worker():
    global _loop_started
    with _lock:
        if _loop_started:
            return
        _loop_started = True
    t = threading.Thread(target=_loop, daemon=True, name="jobfinder-worker")
    t.start()
    log("background worker started")


def _loop():
    while True:
        try:
            run_due_searches()
            expire_old_jobs()
        except Exception:
            log("scheduler error:\n" + traceback.format_exc())
        time.sleep(TICK_SECONDS)


def profile_dict(p: Profile) -> dict:
    def jl(v):
        try:
            return json.loads(v or "[]")
        except json.JSONDecodeError:
            return []
    return {"full_name": p.full_name, "email": p.email, "phone": p.phone,
            "location": p.location, "headline": p.headline, "summary": jl(p.summary),
            "education": jl(p.education), "experience": jl(p.experience),
            "skills": jl(p.skills), "certifications": jl(p.certifications),
            "languages": jl(p.languages), "years_experience": p.years_experience}


def prefs_dict(pr: Preferences) -> dict:
    def jl(v):
        try:
            return json.loads(v or "[]")
        except json.JSONDecodeError:
            return []
    return {"job_titles": jl(pr.job_titles), "fields": jl(pr.fields),
            "countries": jl(pr.countries), "cities": jl(pr.cities),
            "worldwide": pr.worldwide, "work_modes": jl(pr.work_modes),
            "employment_types": jl(pr.employment_types), "min_salary": pr.min_salary,
            "currency": pr.currency, "qualifications": jl(pr.qualifications),
            "min_experience_years": pr.min_experience_years,
            "visa_sponsorship_required": pr.visa_sponsorship_required,
            "open_to_relocation": pr.open_to_relocation, "english_ok": pr.english_ok,
            "sources": jl(pr.sources), "search_frequency_hours": pr.search_frequency_hours,
            "auto_apply_mode": pr.auto_apply_mode,
            "auto_apply_authorized": pr.auto_apply_authorized,
            "max_auto_applications_per_day": pr.max_auto_applications_per_day}


def dedup_key(source: str, url: str, title: str) -> str:
    norm_url = (url or "").lower().split("?")[0].rstrip("/")
    return hashlib.sha256(f"{source}|{norm_url}|{(title or '').lower().strip()}".encode()).hexdigest()[:64]


def queries_for(pr: Preferences, prof: Profile | None) -> list[str]:
    titles = [t for t in json.loads(pr.job_titles or "[]") if t]
    if not titles and prof is not None:
        skills = json.loads(prof.skills or "[]")[:3]
        titles = [f"{s} developer" for s in skills if s in ("python", "sql")] or ["software developer"]
    return titles[:6] or ["software developer"]


def run_user_search(db, user: User, triggered_by: str = "scheduler") -> SearchLog:
    """One search+match(+prepare) cycle for a single user."""
    pr = user.preferences
    prof = user.profile
    entry = SearchLog(user_id=user.id, triggered_by=triggered_by)
    db.add(entry)
    db.flush()
    errors: list[str] = []
    if pr is None:
        entry.errors = json.dumps(["No preferences saved yet."])
        entry.finished_at = datetime.now(timezone.utc)
        db.commit()
        return entry

    pd = prefs_dict(pr)
    sources = [s for s in pd["sources"]] or ["remotive"]
    queries = queries_for(pr, prof)
    outcomes, src_errors = job_sources.run_search(sources, queries, pd["countries"],
                                                  config.MAX_JOBS_PER_SOURCE_PER_RUN)
    errors.extend(src_errors)

    found = added = dupes = 0
    existing_keys = {k for (k,) in db.query(Job.dedup_key).filter(Job.user_id == user.id)}
    now = datetime.now(timezone.utc)
    for key, result in outcomes:
        if not result.ok:
            continue
        for nj in result.jobs:
            found += 1
            dk = dedup_key(key, nj.url, nj.title)
            if dk in existing_keys:
                dupes += 1
                continue
            existing_keys.add(dk)
            job = Job(
                user_id=user.id, dedup_key=dk, source=key, external_id=nj.external_id,
                title=nj.title, company=nj.company, location=nj.location, country=nj.country,
                url=nj.url, salary=nj.salary, description=nj.description, job_type=nj.job_type,
                category=nj.category, remote=nj.remote, visa_note=nj.visa_note,
                posted_at=nj.posted_at, expires_at=(nj.posted_at + timedelta(days=45)
                                                    if nj.posted_at else now + timedelta(days=45)),
            )
            db.add(job)
            db.flush()
            added += 1
            # Match scoring
            if prof is not None:
                try:
                    m = ai_engine.match_job(profile_dict(prof), pd, {
                        "title": nj.title, "company": nj.company, "location": nj.location,
                        "country": nj.country, "remote": nj.remote, "salary": nj.salary,
                        "visa_note": nj.visa_note, "description": nj.description})
                    job.match_score = m["score"]
                    job.match_reasons = json.dumps(m["reasons"])
                    job.missing_skills = json.dumps(m["missing"])
                    job.eligibility_notes = json.dumps(m["eligibility"])
                    job.status = "matched" if m["score"] >= MATCH_THRESHOLD else "new"
                except Exception as exc:
                    errors.append(f"match error on '{nj.title[:40]}': {exc}")
    entry.found, entry.added, entry.duplicates = found, added, dupes
    entry.errors = json.dumps(errors[:20])
    entry.finished_at = datetime.now(timezone.utc)
    db.commit()

    # Application agent pass
    try:
        prepare_applications(db, user)
    except Exception as exc:
        log(f"prepare pass failed for user {user.id}: {exc}\n{traceback.format_exc()}")
    return entry


def prepare_applications(db, user: User):
    """Create 'prepared' applications for high-match jobs according to mode.

    Auto-Apply mode still only PREPARES documents here; actual submission needs
    an authorized integration endpoint, which none of the current free sources
    provide — so nothing is ever falsely marked submitted.
    """
    pr = user.preferences
    prof = user.profile
    if pr is None or prof is None:
        return
    pd = prefs_dict(pr)
    mode = pd["auto_apply_mode"]
    if mode == "find_only":
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    day_count = db.query(Application).filter(
        Application.user_id == user.id,
        Application.created_at >= datetime.now(timezone.utc).replace(hour=0, minute=0, second=0)).count()
    budget = max(0, pd["max_auto_applications_per_day"] - day_count)
    candidates = (db.query(Job)
                    .filter(Job.user_id == user.id, Job.status == "matched",
                            Job.match_score >= PREPARE_THRESHOLD, Job.discovered_at >= cutoff)
                    .order_by(Job.match_score.desc()).limit(budget or 3).all())
    for job in candidates:
        exists = db.query(Application).filter(Application.user_id == user.id,
                                              Application.job_id == job.id).first()
        if exists:
            continue
        jd = {"title": job.title, "company": job.company, "location": job.location,
              "description": job.description, "url": job.url}
        letter, engine = ai_engine.generate_cover_letter(profile_dict(prof), jd, pd)
        cv_notes, _ = ai_engine.generate_tailored_cv_notes(profile_dict(prof), jd)
        answers = ai_engine.answer_application_questions(profile_dict(prof), jd)
        current_cv = next((c for c in user.cv_files if c.is_current), None)
        app = Application(
            user_id=user.id, job_id=job.id, status="awaiting_user",
            cover_letter=letter, tailored_cv_summary=cv_notes,
            answers=json.dumps(answers),
            pause_reason=("Documents prepared. Direct auto-submission is not available for "
                          f"source '{job.source}' (no authorized submit API). Open the original "
                          "link to apply, or review/edit the prepared documents first. "
                          f"(cover letter engine: {engine})"),
            submitted_docs=json.dumps([current_cv.filename] if current_cv else []),
        )
        db.add(app)
    db.commit()


def run_due_searches():
    db = SessionLocal()
    try:
        users = db.query(User).filter(User.is_active == True).all()  # noqa: E712
        for user in users:
            pr = user.preferences
            if pr is None:
                continue
            last = (db.query(SearchLog).filter(SearchLog.user_id == user.id)
                    .order_by(SearchLog.started_at.desc()).first())
            interval = timedelta(hours=max(1, pr.search_frequency_hours or 12))
            due = last is None or (last.started_at.replace(tzinfo=timezone.utc)
                                   if last.started_at.tzinfo is None
                                   else last.started_at) + interval <= datetime.now(timezone.utc)
            if due:
                entry = run_user_search(db, user, "scheduler")
                log(f"user {user.id}: found={entry.found} added={entry.added} "
                    f"dupes={entry.duplicates} errors={len(json.loads(entry.errors))}")
    finally:
        db.close()


def expire_old_jobs():
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        stale = db.query(Job).filter(Job.expires_at != None,  # noqa: E711
                                     Job.expires_at < now,
                                     Job.status.in_(["new", "matched"])).all()
        for j in stale:
            j.status = "expired"
        if stale:
            db.commit()
            log(f"marked {len(stale)} jobs expired")
    finally:
        db.close()
