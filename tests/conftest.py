"""Pytest configuration and shared fixtures for unit and integration testing."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from solution.config import DEFAULT_DISPOSITIONS_PATH, DEFAULT_SCHEMAS_DIR
from solution.models import Base
from solution.schemas import DispositionCatalogue, WorkflowSchema, load_workflow_schemas


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Provide a temporary directory cleaned up after test."""
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """Provide an isolated in-memory SQLite database session."""
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@pytest.fixture
def schemas() -> dict[str, WorkflowSchema]:
    """Load workflow schemas from the assignment directory."""
    return load_workflow_schemas(DEFAULT_SCHEMAS_DIR)


@pytest.fixture
def motor_schema(schemas: dict[str, WorkflowSchema]) -> WorkflowSchema:
    """Motor renewal workflow schema."""
    return schemas["motor_renewal"]


@pytest.fixture
def feedback_schema(schemas: dict[str, WorkflowSchema]) -> WorkflowSchema:
    """Policy feedback workflow schema."""
    return schemas["policy_feedback"]


@pytest.fixture
def catalogue() -> DispositionCatalogue:
    """Load dispositions catalogue."""
    return DispositionCatalogue.from_csv(DEFAULT_DISPOSITIONS_PATH)
