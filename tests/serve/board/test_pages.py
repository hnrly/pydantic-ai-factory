"""The board polls for what changed, and pages back through its columns."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from pydantic_ai_factory import (
    Factory,
    Mission,
    Run,
    SQLiteStore,
)
from pydantic_ai_factory.serve import SQLiteDispatcher, board_app, utc_now

from .support import (
    ADA,
    BEN,
    LEASE,
    MISSION,
    NOW,
    Clock,
    built,
    card,
    cards,
    reports,
    running_run,
    started,
    wait,
    waiting_run,
)

# Polling.


def test_a_poll_since_returns_a_card_whose_answer_job_failed_after_the_last_poll(
    client: TestClient,
    factory: Factory,
    store: SQLiteStore,
    jobs: SQLiteDispatcher,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    an_hour_ago = utc_now() - timedelta(hours=1)
    with monkeypatch.context() as patch:
        patch.setattr(NOW, lambda: an_hour_ago)
        old = wait(reports(approval=False).run(MISSION, store=store, run_id="old"))
    run = waiting_run(client, factory, store, jobs)
    since = client.get("/api/board").json()["since"]
    queued = client.post(
        f"/api/runs/{run.run_id}/answer",
        json={"kind": "approve", "revision": run.revision},
        headers=ADA,
    )
    assert queued.status_code == 202
    assert card(client, run.run_id)["needs_you"] is False
    claimed = wait(jobs.claim(LEASE))
    assert claimed is not None
    wait(jobs.finish(claimed.job_id, error="The disk is full"))

    polled = cards(client, since=since)

    assert old.run_id not in polled
    assert polled[run.run_id]["state"] == "waiting"
    assert (polled[run.run_id]["queued"], polled[run.run_id]["needs_you"]) == (False, True)


def test_a_save_committed_during_a_poll_is_returned_by_the_next_poll(
    client: TestClient, store: SQLiteStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    before_the_poll = utc_now()
    since = datetime.fromisoformat(client.get("/api/board").json()["since"])
    assert since <= before_the_poll - timedelta(seconds=9)
    # The worker committed this save while the first poll was reading, so the first poll
    # did not see it, though it is dated before the poll began.
    during = before_the_poll - timedelta(seconds=1)
    monkeypatch.setattr(NOW, lambda: during)
    wait(reports(approval=False).run(MISSION, store=store, run_id="during"))

    assert "during" in cards(client, since=since.isoformat())


def test_a_poll_returns_a_failed_run_started_again_elsewhere_without_start_again(
    store: SQLiteStore, jobs: SQLiteDispatcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = reports(approval=False, verdict="bad")
    an_hour_ago = utc_now() - timedelta(hours=1)
    with monkeypatch.context() as patch:
        patch.setattr(NOW, lambda: an_hour_ago)
        wait(factory.run(MISSION, store=store, run_id="first"))
    with TestClient(board_app(built(factory), store, jobs)) as client:
        since = client.get("/api/board").json()["since"]
        response = client.post("/api/runs/first/restart", headers=BEN)
        assert response.status_code == 202, response.text

        polled = cards(client, since=since)

    assert (polled["first"]["restarted_as"], polled["first"]["actions"]) == (
        response.json()["run_id"],
        [],
    )


def test_a_poll_returns_a_failed_start_started_again_elsewhere_without_start_again(
    client: TestClient, jobs: SQLiteDispatcher, clock: Clock
) -> None:
    clock.offset = -timedelta(hours=1)
    run_id = started(client)
    job = wait(jobs.claim(LEASE))
    assert job is not None
    wait(jobs.finish(job.job_id, error="The store was busy for too long."))
    clock.offset = timedelta(0)
    since = client.get("/api/board").json()["since"]
    response = client.post(f"/api/runs/{run_id}/restart", headers=BEN)
    assert response.status_code == 202, response.text

    polled = cards(client, since=since)

    assert (polled[run_id]["state"], polled[run_id]["actions"]) == ("stalled", [])
    assert polled[run_id]["restarted_as"] == response.json()["run_id"]


# Paging.


def test_a_board_load_with_many_finished_runs_never_reads_each_run(
    store: SQLiteStore, jobs: SQLiteDispatcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = reports(approval=False)

    async def finish_many() -> None:
        for count in range(120):
            job = await jobs.start(Mission(f"Mission {count}."), by="Ada")
            await factory.run(MISSION, store=store, run_id=job.run_id)
            await jobs.claim(LEASE)
            await jobs.finish(job.job_id)

    wait(finish_many())
    reads: list[str] = []
    get_run = store.get_run

    async def counted(run_id: str) -> Run:
        reads.append(run_id)
        return await get_run(run_id)

    monkeypatch.setattr(store, "get_run", counted)
    with TestClient(board_app(built(factory), store, jobs)) as client:
        board = client.get("/api/board").json()
        for column in ("done", "stopped", "all"):
            response = client.get("/api/runs", params={"column": column})
            assert response.status_code == 200, response.text

    assert len(board["cards"]) == 20
    assert reads == []


def test_see_all_on_stopped_pages_through_failed_runs_stalled_runs_and_failed_starts_together(
    store: SQLiteStore,
    jobs: SQLiteDispatcher,
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pydantic_ai_factory.serve._board.pages.PAGE", 2)
    now = utc_now()
    failing = reports(approval=False, verdict="bad")
    with monkeypatch.context() as patch:
        three_hours_ago = now - timedelta(hours=3)
        patch.setattr(NOW, lambda: three_hours_ago)
        # Two failed runs saved at the same moment: the run id breaks the tie.
        wait(failing.run(MISSION, store=store, run_id="failed-a"))
        wait(failing.run(MISSION, store=store, run_id="failed-b"))
    clock.offset = -timedelta(hours=2, minutes=30)
    wait(jobs.start(Mission(MISSION), by="Ada"))
    stalled_job = wait(jobs.claim(LEASE))
    assert stalled_job is not None
    with monkeypatch.context() as patch:
        two_hours_ago = now - timedelta(hours=2)
        patch.setattr(NOW, lambda: two_hours_ago)
        running_run(failing, store, stalled_job.run_id)
    clock.offset = -timedelta(hours=1)
    wait(jobs.start(Mission("A failed start."), by="Ada"))
    failed_start = wait(jobs.claim(LEASE))
    assert failed_start is not None
    wait(jobs.finish(failed_start.job_id, error="The store was busy for too long."))
    clock.offset = timedelta(0)

    with TestClient(board_app(built(failing), store, jobs)) as client:
        first = client.get("/api/runs", params={"column": "stopped"}).json()
        second = client.get(
            "/api/runs", params={"column": "stopped", "before": first["before"]}
        ).json()

    assert [card["run_id"] for card in first["cards"]] == [
        failed_start.run_id,
        stalled_job.run_id,
    ]
    assert [card["state"] for card in first["cards"]] == ["stalled", "stalled"]
    assert [card["run_id"] for card in second["cards"]] == ["failed-b", "failed-a"]
    assert second["before"] is None


def test_see_all_pages_back_through_done_50_at_a_time(
    client: TestClient, store: SQLiteStore
) -> None:
    passing = reports(approval=False)
    for number in range(51):
        wait(passing.run(MISSION, store=store, run_id=f"run-{number:02}"))

    first = client.get("/api/runs", params={"column": "done"}).json()
    second = client.get("/api/runs", params={"column": "done", "before": first["before"]}).json()

    assert len(first["cards"]) == 50
    assert first["cards"][0]["run_id"] == "run-50"
    assert [card["run_id"] for card in second["cards"]] == ["run-00"]
    assert second["before"] is None
    assert client.get("/api/board").json()["more"] == {"done": True, "stopped": False}
