"""CV upload, parsing, profile review/edit, download, example profile."""
import json
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import config
from ..database import get_db
from ..models import CvFile, Profile, User
from ..security import get_current_user
from ..services.cv_parser import extract_text, parse_cv

router = APIRouter(prefix="/api/cv", tags=["cv"])

MAX_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_EXT = {".pdf", ".docx"}


def _profile_out(p: Profile) -> dict:
    def jl(v):
        try:
            return json.loads(v or "[]")
        except json.JSONDecodeError:
            return []
    return {"full_name": p.full_name, "email": p.email, "phone": p.phone,
            "location": p.location, "headline": p.headline, "summary": jl(p.summary),
            "education": jl(p.education), "experience": jl(p.experience),
            "skills": jl(p.skills), "certifications": jl(p.certifications),
            "languages": jl(p.languages), "years_experience": p.years_experience,
            "verified": p.verified}


@router.post("/upload")
async def upload_cv(file: UploadFile = File(...), user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    if not user.consent_accepted_at:
        raise HTTPException(403, "Please accept the privacy/consent terms before uploading your CV.")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(415, "Only PDF and DOCX files are accepted.")
    raw = await file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, "File too large (max 10 MB).")
    if len(raw) < 100:
        raise HTTPException(422, "File appears empty or corrupt.")

    safe_name = f"{user.id}_{uuid.uuid4().hex}{ext}"
    dest = config.UPLOAD_DIR / safe_name
    dest.write_bytes(raw)

    try:
        text = extract_text(dest)
    except ValueError as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(422, str(exc))
    if len(text.strip()) < 80:
        dest.unlink(missing_ok=True)
        raise HTTPException(422, "Could not read any text from this file. If it is a scanned "
                                 "image CV, please upload a text-based PDF/DOCX.")

    parsed = parse_cv(text)

    # mark previous CVs as not current, keep originals for history
    for old in db.query(CvFile).filter(CvFile.user_id == user.id, CvFile.is_current == True).all():  # noqa: E712
        old.is_current = False
    cv = CvFile(user_id=user.id, filename=file.filename or safe_name, stored_path=str(dest),
                file_type=ext.lstrip("."), size_bytes=len(raw))
    db.add(cv)

    p = user.profile or Profile(user_id=user.id)
    for k, v in parsed.items():
        setattr(p, k, json.dumps(v) if isinstance(v, list) else v)
    p.verified = False  # user must review & confirm
    if not p.email:
        p.email = user.email
    db.add(p)
    db.commit()
    return {"message": "CV parsed. Please review the extracted details and confirm.",
            "profile": _profile_out(p), "cv_files": [c.filename for c in user.cv_files if c.is_current]}


class ProfileIn(BaseModel):
    full_name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    headline: str = ""
    summary: list[str] = []
    education: list[str] = []
    experience: list[str] = []
    skills: list[str] = []
    certifications: list[str] = []
    languages: list[str] = []
    years_experience: float = 0
    verified: bool = True


@router.put("/profile")
def update_profile(body: ProfileIn, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    p = user.profile
    if p is None:
        raise HTTPException(404, "No profile yet — upload your CV first.")
    data = body.model_dump()
    lists = ("summary", "education", "experience", "skills", "certifications", "languages")
    for k, v in data.items():
        setattr(p, k, json.dumps(v) if k in lists else v)
    db.commit()
    return _profile_out(p)


@router.get("/profile")
def get_profile(user: User = Depends(get_current_user)):
    if user.profile is None:
        raise HTTPException(404, "No profile yet — upload your CV first.")
    return _profile_out(user.profile)


@router.get("/download")
def download_cv(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    cv = db.query(CvFile).filter(CvFile.user_id == user.id, CvFile.is_current == True).order_by(  # noqa: E712
        CvFile.uploaded_at.desc()).first()
    if cv is None or not Path(cv.stored_path).exists():
        raise HTTPException(404, "No CV on file.")
    return FileResponse(cv.stored_path, filename=cv.filename, background=None)


EXAMPLE_PROFILE = {
    "full_name": "Example Candidate",
    "email": "",
    "phone": "",
    "location": "Pakistan",
    "headline": "BS Software Engineer | Computer Science Teacher | AI & Automation Developer",
    "summary": ["Optional starting point based on a BS Software Engineering graduate with "
                "government CS teaching experience since 2018 and hands-on Python, LLM-app and "
                "n8n automation work. EDIT EVERY FIELD to reflect your real, verifiable facts."],
    "education": ["BS Software Engineering"],
    "experience": ["Computer Science Teacher — Government institution, Pakistan (2018–present)",
                   "Python programming & software development projects",
                   "AI applications, LLM-based apps and intelligent automation",
                   "n8n workflows and AI agent development"],
    "skills": ["python", "llm", "ai agents", "n8n", "automation", "workflow automation",
               "teaching", "curriculum development", "classroom management", "lesson planning",
               "student assessment", "mentoring", "sql", "rest api", "problem solving",
               "communication"],
    "certifications": [],
    "languages": ["English", "Urdu"],
    "years_experience": 8,
    "verified": False,
}


@router.post("/example-profile")
def load_example(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Loads an EDITABLE example profile (Section 8 of the spec). It is clearly
    marked unverified so no one mistakes it for real extracted data."""
    p = user.profile or Profile(user_id=user.id)
    for k, v in EXAMPLE_PROFILE.items():
        setattr(p, k, json.dumps(v) if isinstance(v, list) else v)
    p.verified = False
    db.add(p)
    db.commit()
    return {"message": "Example profile loaded — edit it to match your real details, then confirm.",
            "profile": _profile_out(p)}


@router.get("/history")
def cv_history(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(CvFile).filter(CvFile.user_id == user.id).order_by(CvFile.uploaded_at.desc()).all()
    return [{"filename": r.filename, "type": r.file_type, "size": r.size_bytes,
             "current": r.is_current, "uploaded_at": r.uploaded_at.isoformat()} for r in rows]
