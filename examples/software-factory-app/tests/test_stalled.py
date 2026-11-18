"""Restarting loses nothing saved, and a run or job that stopped moving shows as stalled."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic_ai_factory import Factory, Run, SQLiteStore

from app import jobs
from app.api import create_app
from tests.support import MISSION, answer, start, work


async def _die(run: Run) -> None:
    raise RuntimeError("the worker was killed mid-station")


def _die_mid_station(db: Path, software: Factory, store: SQLiteStore) -> str:
    """Claim the next start job and save its run as running, as a worker killed mid-station."""
    job = jobs.claim_next(db)
    assert job is not None and job.kind == "start"
    with pytest.raises(RuntimeError):
        asyncio.run(software.run(MISSION, store=store, run_id=job.run_id, on_step=_die))
    return job.run_id


def test_a_running_run_and_its_claimed_job_older_than_the_threshold_show_as_stalled(
    db: Path, software: Factory, store: SQLiteStore
) -> None:
    with TestClient(create_app(db)) as patient:
        run_id = start(patient)
        _die_mid_station(db, software, store)
        fresh = patient.get(f"/api/runs/{run_id}").json()
        assert (fresh["state"], fresh["outcome"]) == ("running", "running")
        assert fresh["jobs"]["start"]["stalled"] is False

    with TestClient(create_app(db, stalled_after=timedelta(0))) as strict:
        stalled = strict.get(f"/api/runs/{run_id}").json()
        assert (stalled["state"], stalled["outcome"]) == ("stalled", "running")
        assert stalled["last_change"] == stalled["updated_at"]
        assert stalled["actions"] == ["start_again"]
        assert stalled["jobs"]["start"]["stalled"] is True
        assert answer(strict, run_id, choice="approve", by="Ada").status_code == 409


def test_a_claimed_start_job_with_no_saved_run_is_shown_as_a_stalled_run(db: Path) -> None:
    with TestClient(create_app(db)) as patient:
        run_id = start(patient)
        assert patient.get(f"/api/runs/{run_id}").json()["state"] == "queued"
        assert jobs.claim_next(db) is not None
        assert patient.get(f"/api/runs/{run_id}").json()["state"] == "running"

    with TestClient(create_app(db, stalled_after=timedelta(0))) as strict:
        run = strict.get(f"/api/runs/{run_id}").json()
        assert (run["state"], run["outcome"], run["actions"]) == ("stalled", None, ["start_again"])
        listed = strict.get("/api/runs").json()["runs"]
        assert [(r["run_id"], r["state"]) for r in listed] == [(run_id, "stalled")]


def test_after_a_restart_a_waiting_run_can_be_answered_a_queued_one_starts_and_one_stalled(
    db: Path, software: Factory
) -> None:
    with TestClient(create_app(db)) as before:
        waiting = start(before, "A to-do list web page.")
        work(db, software, SQLiteStore(db))
        stalled = start(before, "A shopping list web page.")
        _die_mid_station(db, software, SQLiteStore(db))
        queued = start(before, "A reading list web page.")

    # Both processes are started again, with nothing carried over but the file.
    store = SQLiteStore(db)
    with TestClient(create_app(db, stalled_after=timedelta(0))) as after:
        states = {run["run_id"]: run["state"] for run in after.get("/api/runs").json()["runs"]}
        assert states == {waiting: "waiting", stalled: "stalled", queued: "queued"}

        assert answer(after, waiting, choice="approve", by="Ada").status_code == 202
        work(db, software, store)

        states = {run["run_id"]: run["state"] for run in after.get("/api/runs").json()["runs"]}
        assert states == {waiting: "passed", stalled: "stalled", queued: "waiting"}
