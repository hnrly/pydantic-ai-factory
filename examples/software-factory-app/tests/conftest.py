"""Fixtures for the example's tests. Every test runs offline, on scripted models."""

from __future__ import annotations

import importlib
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic_ai_factory import SQLiteStore
from pydantic_ai_factory.serve import SQLiteDispatcher

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")


@dataclass
class Served:
    """``board.py`` as loaded on a test's own file, and a client for its web process."""

    client: TestClient
    store: SQLiteStore
    dispatcher: SQLiteDispatcher


@pytest.fixture
def served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Served]:
    """Load ``board.py`` afresh, pointed at a file of the test's own through ``FACTORY_DB``."""
    monkeypatch.setenv("FACTORY_DB", str(tmp_path / "factory.db"))
    import board

    importlib.reload(board)
    with TestClient(board.app) as client:
        yield Served(client, board.store, board.dispatcher)
