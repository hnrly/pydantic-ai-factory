"""Fixtures for the app's tests. Every test runs offline, on scripted models."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic_ai_factory import Factory, SQLiteStore
from pydantic_ai_factory_software import offline_model

from app.api import create_app
from app.factory import build_factory

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "factory.db"


@pytest.fixture
def store(db: Path) -> SQLiteStore:
    return SQLiteStore(db)


@pytest.fixture
def software() -> Factory:
    """The factory the worker runs, on the software package's scripted model."""
    return build_factory(offline_model())


@pytest.fixture
def client(db: Path) -> Iterator[TestClient]:
    with TestClient(create_app(db)) as client:
        yield client
