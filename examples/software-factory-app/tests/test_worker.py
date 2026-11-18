"""The worker: how it fails a job, how it stops, and what it needs to start."""

from __future__ import annotations

import asyncio
import os
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai_factory import Run, SQLiteStore, StoreBusy
from pydantic_ai_factory_software import offline_model

from app import jobs, settings, worker
from app.api import create_app
from app.factory import build_factory
from app.worker import BUSY, serve
from tests.support import MISSION, work

APP = Path(__file__).resolve().parent.parent


class BusyStore(SQLiteStore):
    """A store whose every save of a run finds another writer holding the file too long."""

    async def put_run(self, run: Run, *, replaces: Run | None) -> None:
        raise StoreBusy("the store was busy for longer than 5.0 seconds")


def test_a_busy_store_fails_the_job_with_its_own_message(db: Path) -> None:
    jobs.add_start(db, MISSION, "Ada")

    [job] = work(db, build_factory(offline_model()), BusyStore(db))

    assert (job.state, job.error) == ("failed", BUSY)


def test_the_worker_finishes_the_job_in_hand_on_the_first_signal_then_stops(db: Path) -> None:
    scripted = offline_model().function
    assert scripted is not None
    sent: list[bool] = []

    async def stopped_mid_job(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not sent:
            sent.append(True)
            signal.raise_signal(signal.SIGTERM)
        response = scripted(messages, info)
        assert isinstance(response, ModelResponse)
        return response

    first = jobs.add_start(db, MISSION, "Ada")
    second = jobs.add_start(db, "A shopping list web page.", "Ada")
    store = SQLiteStore(db)

    asyncio.run(serve(db, build_factory(FunctionModel(stopped_mid_job)), store, poll=0.01))

    assert jobs.get(db, first.id).state == "done"
    assert asyncio.run(store.get_run(first.run_id)).outcome == "waiting"
    assert jobs.get(db, second.id).state == "queued"


def test_the_worker_will_not_start_on_sqlite_older_than_3_35(
    db: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 34, 1))
    monkeypatch.setattr(sqlite3, "sqlite_version", "3.34.1")

    assert worker.main(["--offline", "--db", str(db)]) == 2

    assert "3.34.1" in capsys.readouterr().err
    assert not db.exists()


def test_the_worker_without_a_model_or_offline_says_how_to_start(
    db: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(settings, "MODEL", None)

    assert worker.main(["--db", str(db)]) == 2

    assert "FACTORY_MODEL" in capsys.readouterr().err


def test_the_web_process_starts_and_serves_with_no_model_key(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "FACTORY_MODEL"):
        monkeypatch.delenv(name, raising=False)
    environment = {
        name: value for name, value in os.environ.items() if not name.endswith("_API_KEY")
    }
    environment["FACTORY_DB"] = str(db)
    imported = subprocess.run(
        [sys.executable, "-c", "import app.api; print(app.api.app.title)"],
        cwd=APP,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert imported.returncode == 0, imported.stderr

    with TestClient(create_app(db)) as client:
        assert client.get("/api/runs").json() == {"runs": [], "more": False}
