"""The acceptance test for one row of the roadmap's "Today" table.

It lives here, not in the library's own ``tests/test_acceptance.py``, because it needs
FastAPI, which the library's environment does not install.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from pydantic_ai_factory import Factory, SQLiteStore

from tests.support import MISSION, work


def test_you_can_run_a_factory_behind_a_web_page_with_its_work_done_by_a_separate_worker(
    client: TestClient, db: Path, software: Factory, store: SQLiteStore
) -> None:
    run_id = client.post("/api/runs", json={"mission": MISSION, "by": "Ada"}).json()["run_id"]
    work(db, software, store)
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["state"] == "waiting"

    client.post(
        f"/api/runs/{run_id}/answer",
        json={"choice": "approve", "by": "Ada", "revision": run["revision"]},
    )
    work(db, software, store)

    assert client.get(f"/api/runs/{run_id}").json()["state"] == "passed"
