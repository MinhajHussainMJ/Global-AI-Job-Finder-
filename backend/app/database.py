"""Database engine/session (SQLite by default; set DATABASE_URL for Postgres)."""
import os
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from . import config
from .models import Base

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{config.DB_PATH}")

if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _fk_pragma(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()
else:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
