"""Stalled work moves to Stopped, and Start again begins it afresh, once."""

from __future__ import annotations

from pathlib import Path

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
    Clock,
    built,
    card,
    cards,
    claim_in_the_past,
    reports,
    running_run,
    serve,
    started,
    wait,
)

# Stalled work.


def test_a_stalled_run_stays_stalled_after_a_thousand_further_start_jobs(
    client: TestClient, factory: Factory, store: SQLiteStore, jobs: SQLiteDispatcher
) -> None:
    run_id = started(client)
    job = wait(jobs.claim(LEASE))
    assert job is not None
    running_run(factory, store, run_id)
    wait(jobs.finish(job.job_id, error="The job failed: the disk is full"))

    async def queue_many() -> None:
        for count in range(1000):
            await jobs.start(Mission(f"Mission {count}."), by="Ada")

    wait(queue_many())
    shown = cards(client)

    assert (shown[run_id]["column"], shown[run_id]["state"]) == ("stopped", "stalled")
    # Every open start is on the board, not only the first thousand jobs read.
    assert sum(card["state"] == "queued" for card in shown.values()) == 1000


def test_a_lapsed_claim_moves_the_card_to_stopped_as_stalled(
    client: TestClient,
    factory: Factory,
    store: SQLiteStore,
    jobs: SQLiteDispatcher,
    clock: Clock,
) -> None:
    run_id = started(client)
    claim_in_the_past(jobs, clock)
    running_run(factory, store, run_id)

    stalled = card(client, run_id)
    assert (stalled["column"], stalled["state"]) == ("stopped", "stalled")
    assert stalled["actions"] == ["start_again"]


def test_start_again_abandons_a_stalled_run_then_queues_a_new_one_and_links_the_two(
    client: TestClient,
    factory: Factory,
    store: SQLiteStore,
    jobs: SQLiteDispatcher,
    clock: Clock,
) -> None:
    run_id = started(client)
    claim_in_the_past(jobs, clock)
    running_run(factory, store, run_id)

    response = client.post(f"/api/runs/{run_id}/restart", headers=ADA)

    assert response.status_code == 202, response.text
    new_run_id = response.json()["run_id"]
    old = wait(store.get_run(run_id))
    assert old.outcome == "did_not_run"
    assert old.abandoned is not None and old.abandoned.reason == "Started again by Ada"
    restart = wait(jobs.job(response.json()["job_id"]))
    assert (restart.restart_of, restart.by) == (run_id, "Ada")
    assert restart.mission is not None and restart.mission.objective == MISSION
    board = cards(client)
    assert (board[run_id]["column"], board[run_id]["state"]) == ("stopped", "did_not_run")
    assert board[run_id]["restarted_as"] == new_run_id
    assert board[run_id]["actions"] == []
    assert (board[new_run_id]["column"], board[new_run_id]["state"]) == ("missions", "queued")


class MovesOnBeforeTheNextSave(SQLiteStore):
    """A store where a slow worker saves the run just before the next save is made."""

    armed = False

    async def put_run(self, run: Run, *, replaces: Run | None) -> None:
        if self.armed and replaces is not None:
            self.armed = False
            moved = replaces.model_copy(
                update={"revision": replaces.revision + 1, "updated_at": utc_now()}
            )
            await super().put_run(moved, replaces=replaces)
        await super().put_run(run, replaces=replaces)


def test_start_again_on_a_run_that_moved_on_gets_409(
    db: Path, factory: Factory, jobs: SQLiteDispatcher, clock: Clock
) -> None:
    store = MovesOnBeforeTheNextSave(db)
    with TestClient(board_app(built(factory), store, jobs)) as client:
        run_id = started(client)
        claim_in_the_past(jobs, clock)
        running_run(factory, store, run_id)
        store.armed = True

        response = client.post(f"/api/runs/{run_id}/restart", headers=ADA)

    assert response.status_code == 409
    assert response.json()["run"]["run_id"] == run_id
    assert wait(store.get_run(run_id)).outcome == "running"
    assert wait(jobs.list_jobs(restart_of=run_id)) == []


def test_a_failed_run_can_be_started_again_once(store: SQLiteStore, jobs: SQLiteDispatcher) -> None:
    factory = reports(approval=False, verdict="bad")
    with TestClient(board_app(built(factory), store, jobs)) as client:
        run_id = started(client)
        serve(factory, store, jobs)
        stopped = card(client, run_id)
        assert (stopped["column"], stopped["state"], stopped["actions"]) == (
            "stopped",
            "failed",
            ["start_again"],
        )

        first = client.post(f"/api/runs/{run_id}/restart", headers=ADA)
        second = client.post(f"/api/runs/{run_id}/restart", headers=BEN)

    assert first.status_code == 202
    assert second.status_code == 409
    assert [job.restart_of for job in wait(jobs.list_jobs(restart_of=run_id))] == [run_id]


def test_an_earlier_version_run_is_in_stopped_with_no_actions(
    store: SQLiteStore, jobs: SQLiteDispatcher
) -> None:
    earlier = reports(approval=False, verdict="bad")
    wait(earlier.run(MISSION, store=store, run_id="earlier"))
    now = reports(approval=False, verdict="bad", version="2")
    assert now.digest != earlier.digest
    with TestClient(board_app(built(now), store, jobs)) as client:
        shown = card(client, "earlier")

    assert (shown["column"], shown["state"], shown["actions"]) == (
        "stopped",
        "earlier_version",
        [],
    )


def test_an_earlier_version_run_that_is_still_running_can_be_started_again(
    store: SQLiteStore, jobs: SQLiteDispatcher
) -> None:
    running_run(reports(), store, "earlier")
    with TestClient(board_app(built(reports(version="2")), store, jobs)) as client:
        shown = card(client, "earlier")
        assert (shown["column"], shown["state"], shown["actions"]) == (
            "stopped",
            "earlier_version",
            ["start_again"],
        )

        response = client.post("/api/runs/earlier/restart", headers=ADA)

    assert response.status_code == 202, response.text
    assert wait(store.get_run("earlier")).outcome == "did_not_run"
