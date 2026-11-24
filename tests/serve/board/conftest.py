"""Fixtures for the board tests: one SQLite file holding the store and the queue."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pydantic_ai_factory import (
    Factory,
    SQLiteStore,
)
from pydantic_ai_factory.serve import SQLiteDispatcher, board_app

from .support import Clock, built, reports


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "factory.db"


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(db: Path) -> SQLiteStore:
    return SQLiteStore(db)


@pytest.fixture
def jobs(db: Path, clock: Clock) -> SQLiteDispatcher:
    return SQLiteDispatcher(db, clock=clock)


@pytest.fixture
def factory() -> Factory:
    return reports()


@pytest.fixture
def client(factory: Factory, store: SQLiteStore, jobs: SQLiteDispatcher) -> Iterator[TestClient]:
    with TestClient(board_app(built(factory), store, jobs)) as client:
        yield client
