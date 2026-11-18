"""Helpers the tests share: doing queued jobs as the worker would, and calling the API."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic_ai_factory import Factory, SQLiteStore

from app.jobs import Job
from app.worker import do_next_job

MISSION = "A to-do list web page where I can add tasks and tick them off."


def work(db: Path, factory: Factory, store: SQLiteStore) -> list[Job]:
    """Do every queued job, as the worker would, and return them in the order done."""
    done: list[Job] = []
    while (job := asyncio.run(do_next_job(db, factory, store))) is not None:
        done.append(job)
    return done


def start(client: TestClient, mission: str = MISSION, by: str = "Ada") -> str:
    """Start a run through the API and return its id."""
    response = client.post("/api/runs", json={"mission": mission, "by": by})
    assert response.status_code == 202, response.text
    return response.json()["run_id"]


def answer(client: TestClient, run_id: str, **body: object):
    """Answer a run through the API, at the revision the run is at now."""
    revision = client.get(f"/api/runs/{run_id}").json()["revision"]
    return client.post(f"/api/runs/{run_id}/answer", json={"revision": revision, **body})
