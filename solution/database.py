"""Database connection and session management using SQLAlchemy 2.x."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from solution.config import DATABASE_URL
from solution.models import Base

_ENGINE: Optional[Engine] = None
_SESSION_FACTORY: Optional[sessionmaker[Session]] = None


def get_engine(db_url: Optional[str] = None) -> Engine:
    """Get or create the SQLAlchemy engine."""
    global _ENGINE
    url = db_url or DATABASE_URL
    if _ENGINE is None or str(_ENGINE.url) != url:
        _ENGINE = create_engine(url, echo=False, future=True)
    return _ENGINE


def get_session_factory(db_url: Optional[str] = None) -> sessionmaker[Session]:
    """Get or create session factory."""
    global _SESSION_FACTORY
    engine = get_engine(db_url)
    if _SESSION_FACTORY is None or _SESSION_FACTORY.kw.get("bind") != engine:
        _SESSION_FACTORY = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return _SESSION_FACTORY


def init_db(db_url: Optional[str] = None) -> None:
    """Initialize database schema, creating all tables if they do not exist."""
    engine = get_engine(db_url)
    Base.metadata.create_all(bind=engine)


@contextmanager
def get_session(db_url: Optional[str] = None) -> Generator[Session, None, None]:
    """Context manager for transactional database sessions."""
    factory = get_session_factory(db_url)
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
