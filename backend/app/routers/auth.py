from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from ..security import create_token, get_current_user, hash_password, verify_password
from ..services import ai_engine

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = ""
    mode: str = "general"  # general | personal (personal = upload once, keep searching)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


def _user_out(u: User) -> dict:
    return {"id": u.id, "email": u.email, "full_name": u.full_name, "mode": u.mode,
            "consent_accepted": u.consent_accepted_at is not None,
            "has_cv": u.profile is not None and u.profile.verified or any(c.is_current for c in u.cv_files),
            "profile_verified": bool(u.profile and u.profile.verified)}


@router.post("/register")
def register(body: RegisterIn, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == body.email.lower()).first():
        raise HTTPException(409, "An account with this email already exists.")
    user = User(email=body.email.lower(), password_hash=hash_password(body.password),
                full_name=body.full_name, mode=body.mode if body.mode in ("general", "personal") else "general")
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"token": create_token(user), "user": _user_out(user)}


@router.post("/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email.lower()).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password.")
    if not user.is_active:
        raise HTTPException(403, "Account deactivated.")
    return {"token": create_token(user), "user": _user_out(user)}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"user": _user_out(user),
            "ai_configured": ai_engine.ai_available(),
            "server_time_utc": __import__("datetime").datetime.utcnow().isoformat() + "Z"}


class ConsentIn(BaseModel):
    accept: bool


@router.post("/consent")
def consent(body: ConsentIn, user: User = Depends(get_current_user),
            db: Session = Depends(get_db)):
    from datetime import datetime, timezone
    user.consent_accepted_at = datetime.now(timezone.utc) if body.accept else None
    db.commit()
    return _user_out(user)
