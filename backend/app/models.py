"""SQLAlchemy models.

Tables: users, profiles, preferences, jobs, applications, integrations,
cv_files, search_logs. Every job/application/profile row is owned by a user_id;
all queries are scoped to the authenticated user so one user can never see
another user's CV or application data.
"""
from datetime import datetime, timezone
from sqlalchemy import (String, Integer, Float, Boolean, Text, DateTime,
                        ForeignKey, UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(255), default="")
    mode: Mapped[str] = mapped_column(String(20), default="general")  # general|personal
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    consent_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    profile: Mapped["Profile"] = relationship(back_populates="user", uselist=False, cascade="all, delete-orphan")
    preferences: Mapped["Preferences"] = relationship(back_populates="user", uselist=False, cascade="all, delete-orphan")
    jobs: Mapped[list["Job"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    applications: Mapped[list["Application"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    cv_files: Mapped[list["CvFile"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    integrations: Mapped[list["Integration"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    search_logs: Mapped[list["SearchLog"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class CvFile(Base):
    __tablename__ = "cv_files"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(500))
    stored_path: Mapped[str] = mapped_column(String(1000))
    file_type: Mapped[str] = mapped_column(String(10))          # pdf|docx
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="cv_files")


class Profile(Base):
    """Structured candidate profile extracted from the CV; user-editable."""
    __tablename__ = "profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    phone: Mapped[str] = mapped_column(String(64), default="")
    location: Mapped[str] = mapped_column(String(255), default="")
    headline: Mapped[str] = mapped_column(String(500), default="")
    summary: Mapped[str] = mapped_column(Text, default="")            # JSON list of paragraphs
    education: Mapped[str] = mapped_column(Text, default="[]")        # JSON list
    experience: Mapped[str] = mapped_column(Text, default="[]")       # JSON list
    skills: Mapped[str] = mapped_column(Text, default="[]")           # JSON list
    certifications: Mapped[str] = mapped_column(Text, default="[]")   # JSON list
    languages: Mapped[str] = mapped_column(Text, default="[]")        # JSON list
    years_experience: Mapped[float] = mapped_column(Float, default=0)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)    # user reviewed/corrected
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="profile")


class Preferences(Base):
    __tablename__ = "preferences"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    job_titles: Mapped[str] = mapped_column(Text, default="[]")            # JSON list
    fields: Mapped[str] = mapped_column(Text, default="[]")                # JSON list
    countries: Mapped[str] = mapped_column(Text, default='["Worldwide"]')  # JSON list
    cities: Mapped[str] = mapped_column(Text, default="[]")
    worldwide: Mapped[bool] = mapped_column(Boolean, default=True)
    work_modes: Mapped[str] = mapped_column(Text, default='["Remote","Hybrid","Onsite"]')
    employment_types: Mapped[str] = mapped_column(Text, default='["Full-time"]')
    min_salary: Mapped[float] = mapped_column(Float, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    qualifications: Mapped[str] = mapped_column(Text, default="[]")
    min_experience_years: Mapped[float] = mapped_column(Float, default=0)
    visa_sponsorship_required: Mapped[bool] = mapped_column(Boolean, default=False)
    open_to_relocation: Mapped[bool] = mapped_column(Boolean, default=False)
    english_ok: Mapped[bool] = mapped_column(Boolean, default=True)
    sources: Mapped[str] = mapped_column(Text, default='["remotive"]')     # enabled source keys
    search_frequency_hours: Mapped[int] = mapped_column(Integer, default=12)
    auto_apply_mode: Mapped[str] = mapped_column(String(20), default="find_only")  # find_only|prepare|auto_apply
    auto_apply_authorized: Mapped[bool] = mapped_column(Boolean, default=False)
    max_auto_applications_per_day: Mapped[int] = mapped_column(Integer, default=5)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="preferences")


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    dedup_key: Mapped[str] = mapped_column(String(128), index=True)  # hash(source,url,title)
    source: Mapped[str] = mapped_column(String(50))
    external_id: Mapped[str] = mapped_column(String(255), default="")
    title: Mapped[str] = mapped_column(String(500))
    company: Mapped[str] = mapped_column(String(500), default="")
    location: Mapped[str] = mapped_column(String(500), default="")
    country: Mapped[str] = mapped_column(String(100), default="")
    url: Mapped[str] = mapped_column(String(1500))
    salary: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    job_type: Mapped[str] = mapped_column(String(64), default="")
    category: Mapped[str] = mapped_column(String(128), default="")
    remote: Mapped[bool] = mapped_column(Boolean, default=False)
    visa_note: Mapped[str] = mapped_column(String(500), default="unknown")
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    match_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    match_reasons: Mapped[str] = mapped_column(Text, default="[]")   # JSON list
    missing_skills: Mapped[str] = mapped_column(Text, default="[]")  # JSON list
    eligibility_notes: Mapped[str] = mapped_column(Text, default="[]")  # JSON list
    status: Mapped[str] = mapped_column(String(30), default="new")   # new|matched|expired|hidden
    bookmarked: Mapped[bool] = mapped_column(Boolean, default=False)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (UniqueConstraint("user_id", "dedup_key", name="uq_job_dedup"),)

    user: Mapped[User] = relationship(back_populates="jobs")
    applications: Mapped[list["Application"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class Application(Base):
    __tablename__ = "applications"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="prepared", index=True)
    # statuses: prepared|awaiting_user|submitted|interview|rejected|expired|failed
    cover_letter: Mapped[str] = mapped_column(Text, default="")
    tailored_cv_summary: Mapped[str] = mapped_column(Text, default="")
    answers: Mapped[str] = mapped_column(Text, default="[]")      # JSON list of {question, answer}
    pause_reason: Mapped[str] = mapped_column(Text, default="")
    confirmation_ref: Mapped[str] = mapped_column(String(500), default="")
    submitted_docs: Mapped[str] = mapped_column(Text, default="[]")  # JSON list of filenames
    error: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    follow_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="applications")
    job: Mapped[Job] = relationship(back_populates="applications")


class Integration(Base):
    """Per-user integration authorization + server-side key presence flags."""
    __tablename__ = "integrations"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(50))               # remotive|adzuna|linkedin|...
    authorized: Mapped[bool] = mapped_column(Boolean, default=False)
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")

    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_integration"),)

    user: Mapped[User] = relationship(back_populates="integrations")


class SearchLog(Base):
    __tablename__ = "search_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    found: Mapped[int] = mapped_column(Integer, default=0)
    added: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[str] = mapped_column(Text, default="[]")    # JSON list of strings
    triggered_by: Mapped[str] = mapped_column(String(20), default="scheduler")

    user: Mapped[User] = relationship(back_populates="search_logs")
