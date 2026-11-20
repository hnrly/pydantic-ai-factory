"""The board app shows a factory's work as cards, and queues only what the factory asks for."""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Coroutine, Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from pydantic_ai_factory import (
    Artifact,
    AskPerson,
    Escalate,
    Evidence,
    Factory,
    Job,
    Mission,
    Reply,
    Rework,
    Run,
    RunConflict,
    SQLiteStore,
    StationContext,
    Verdict,
    failed,
    gate,
    passed,
    station,
)
from pydantic_ai_factory.contracts import run_sync
from pydantic_ai_factory.serve import BoardUnsupported, SQLiteDispatcher, board_app, utc_now, work

T = TypeVar("T")

LEASE = timedelta(minutes=2)
ADA = {"X-Factory-Name": "Ada"}
BEN = {"X-Factory-Name": "Ben"}
MISSION = "A weekly report on the support queue."
NOW = "pydantic_ai_factory.factory._runner._now"
"""Every time on a run is read from here, so a test can date a save."""


def wait(coro: Coroutine[Any, Any, T]) -> T:
    # The thread loop that ``work`` also uses, so neither leaves the other's loop unclosed.
    return run_sync(coro)


class Clock:
    """The real clock, moved by ``offset``, so a test can claim a job in the past."""

    def __init__(self) -> None:
        self.offset = timedelta(0)

    def __call__(self) -> datetime:
        return utc_now() + self.offset


class Review(BaseModel):
    verdict: str


@gate(version="1")
def good(output: Artifact[Review], evidence: Evidence) -> Verdict:
    return passed("good") if output.payload.verdict == "good" else failed("not good yet")


def show_draft(text: str) -> str:
    return f"# Draft\n\n{text}"


def reports(
    *, version: str = "1", approval: bool = True, verdict: str = "good", rework: bool = False
) -> Factory:
    """A two-station factory: a draft a person may approve, then a review with a gate.

    With ``rework``, a review that is not good sends the work back to the draft, which
    then escalates to a person.
    """

    @station(approval=approval, render=show_draft)
    async def draft(ctx: StationContext, mission: str) -> str:
        if ctx.feedback is not None:
            raise Escalate("Look at the rework", output=f"Second draft of {mission}")
        return f"First draft of {mission}"

    @station(gate=good)
    async def review(ctx: StationContext, text: str) -> Review:
        return Review(verdict=verdict)

    rules = [Rework(review, draft, limit=2)] if rework else []
    return Factory("reports", [draft, review], rework=rules, version=version)


def built(factory: Factory) -> Any:
    def build(model: Model | str) -> Factory:
        return factory

    return build


class Stop(Exception):
    """Raised from ``on_step`` to leave a run saved as running, as a dead worker would."""


async def stop(run: Run) -> None:
    raise Stop


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


def serve(factory: Factory, store: SQLiteStore, jobs: SQLiteDispatcher) -> None:
    work(built(factory), store, jobs, model=TestModel(), until_idle=True)


def started(client: TestClient, mission: str = MISSION) -> str:
    response = client.post("/api/runs", json={"mission": mission}, headers=ADA)
    assert response.status_code == 202, response.text
    return response.json()["run_id"]


def waiting_run(
    client: TestClient, factory: Factory, store: SQLiteStore, jobs: SQLiteDispatcher
) -> Run:
    run_id = started(client)
    serve(factory, store, jobs)
    run = wait(store.get_run(run_id))
    assert run.outcome == "waiting"
    return run


def running_run(factory: Factory, store: SQLiteStore, run_id: str = "run-1") -> Run:
    """A run saved as running at its first station, whose worker then stopped."""
    with pytest.raises(Stop):
        wait(factory.run(MISSION, store=store, run_id=run_id, on_step=stop))
    run = wait(store.get_run(run_id))
    assert run.outcome == "running"
    return run


def cards(client: TestClient, **params: Any) -> dict[str, dict[str, Any]]:
    response = client.get("/api/board", params=params)
    assert response.status_code == 200, response.text
    return {card["run_id"]: card for card in response.json()["cards"]}


def card(client: TestClient, run_id: str) -> dict[str, Any]:
    return cards(client)[run_id]


def answer_jobs(jobs: SQLiteDispatcher) -> list[Job]:
    return wait(jobs.list_jobs(kind="answer"))


def claim_in_the_past(jobs: SQLiteDispatcher, clock: Clock) -> Job:
    """Claim the oldest queued job so long ago that its claim has lapsed."""
    clock.offset = -timedelta(minutes=10)
    job = wait(jobs.claim(LEASE))
    clock.offset = timedelta(0)
    assert job is not None
    return job


# Columns and cards.


def test_the_board_has_a_column_per_station_then_done_and_stopped(client: TestClient) -> None:
    board = client.get("/api/board").json()

    assert [station["name"] for station in board["shape"]["stations"]] == ["draft", "review"]
    assert board["more"] == {"done": False, "stopped": False}
    assert board["cards"] == []


def test_a_queued_mission_is_a_card_in_the_first_column_before_its_run_is_saved(
    client: TestClient, store: SQLiteStore
) -> None:
    run_id = started(client, "A weekly report.\nWith charts.")

    queued = card(client, run_id)
    assert (queued["column"], queued["station"], queued["state"]) == ("station", "draft", "queued")
    assert queued["mission"] == "A weekly report."
    assert wait(store.list_runs()) == []


def test_a_reworked_run_is_carded_at_the_station_it_went_back_to_with_its_count(
    store: SQLiteStore, jobs: SQLiteDispatcher
) -> None:
    factory = reports(approval=False, verdict="bad", rework=True)
    with TestClient(board_app(built(factory), store, jobs)) as client:
        run_id = started(client)
        serve(factory, store, jobs)

        reworked = card(client, run_id)
    assert (reworked["column"], reworked["station"], reworked["state"]) == (
        "station",
        "draft",
        "waiting",
    )
    assert reworked["rework"] == {"count": 1, "limit": 2}


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
    assert (board[new_run_id]["station"], board[new_run_id]["state"]) == ("draft", "queued")


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


# Answers.


def test_an_answer_to_a_moved_on_run_gets_409_with_the_run_as_it_is_now(
    client: TestClient, factory: Factory, store: SQLiteStore, jobs: SQLiteDispatcher
) -> None:
    run = waiting_run(client, factory, store, jobs)
    wait(jobs.answer(run.approve(by="Ada")))
    serve(factory, store, jobs)

    response = client.post(
        f"/api/runs/{run.run_id}/answer",
        json={"kind": "changes", "revision": run.revision, "note": "Shorter, please."},
        headers=BEN,
    )

    assert response.status_code == 409
    assert response.json()["run"]["state"] == "passed"
    assert len(answer_jobs(jobs)) == 1


def test_an_answer_to_a_run_that_waits_again_at_a_later_revision_gets_409_and_is_not_queued(
    client: TestClient, factory: Factory, store: SQLiteStore, jobs: SQLiteDispatcher
) -> None:
    run = waiting_run(client, factory, store, jobs)
    changes = client.post(
        f"/api/runs/{run.run_id}/answer",
        json={"kind": "changes", "revision": run.revision, "note": "Shorter, please."},
        headers=ADA,
    )
    assert changes.status_code == 202
    serve(factory, store, jobs)
    again = wait(store.get_run(run.run_id))
    assert again.outcome == "waiting" and again.revision > run.revision

    response = client.post(
        f"/api/runs/{run.run_id}/answer",
        json={"kind": "approve", "revision": run.revision},
        headers=BEN,
    )

    assert response.status_code == 409
    assert response.json()["run"]["revision"] == again.revision
    assert len(answer_jobs(jobs)) == 1


def test_the_answer_body_cannot_set_the_station_or_the_work(
    client: TestClient, factory: Factory, store: SQLiteStore, jobs: SQLiteDispatcher
) -> None:
    run = waiting_run(client, factory, store, jobs)

    response = client.post(
        f"/api/runs/{run.run_id}/answer",
        json={
            "kind": "approve",
            "revision": run.revision,
            "station": "review",
            "work": {"type": "Review", "digest": "0" * 64},
        },
        headers=ADA,
    )

    assert response.status_code == 422
    assert answer_jobs(jobs) == []
    queued = client.post(
        f"/api/runs/{run.run_id}/answer",
        json={"kind": "approve", "revision": run.revision},
        headers=ADA,
    )
    assert queued.status_code == 202
    (job,) = answer_jobs(jobs)
    assert job.answer is not None and job.answer.station == "draft"
    assert run.waiting is not None and job.answer.work == run.waiting.artifact


def test_an_answer_with_no_name_gets_400(
    client: TestClient, factory: Factory, store: SQLiteStore, jobs: SQLiteDispatcher
) -> None:
    run = waiting_run(client, factory, store, jobs)

    responses = [
        client.post(
            f"/api/runs/{run.run_id}/answer", json={"kind": "approve", "revision": run.revision}
        ),
        client.post(
            f"/api/runs/{run.run_id}/answer",
            json={"kind": "approve", "revision": run.revision},
            headers={"X-Factory-Name": "  "},
        ),
        client.post("/api/runs", json={"mission": MISSION}),
        client.post(f"/api/runs/{run.run_id}/restart"),
    ]

    assert [response.status_code for response in responses] == [400, 400, 400, 400]
    assert {response.json()["detail"] for response in responses} == {"Type your name first"}
    assert answer_jobs(jobs) == []


# Which rule decides.


def test_a_run_saved_as_passed_is_done_even_if_its_job_lapsed(
    client: TestClient, store: SQLiteStore, jobs: SQLiteDispatcher, clock: Clock
) -> None:
    run_id = started(client)
    claim_in_the_past(jobs, clock)
    wait(reports(approval=False).run(MISSION, store=store, run_id=run_id))

    shown = card(client, run_id)
    assert (shown["column"], shown["state"], shown["actions"]) == ("done", "passed", [])


def test_a_running_run_whose_job_failed_is_stalled_in_stopped(
    client: TestClient, factory: Factory, store: SQLiteStore, jobs: SQLiteDispatcher
) -> None:
    run_id = started(client)
    job = wait(jobs.claim(LEASE))
    assert job is not None
    running_run(factory, store, run_id)
    wait(jobs.finish(job.job_id, error="The job failed: the disk is full"))

    shown = card(client, run_id)
    assert (shown["column"], shown["state"], shown["actions"]) == (
        "stopped",
        "stalled",
        ["start_again"],
    )
    assert shown["reason"] == "The job failed: the disk is full"


def test_a_started_again_card_shows_its_link_instead_of_start_again(
    store: SQLiteStore, jobs: SQLiteDispatcher
) -> None:
    factory = reports(approval=False, verdict="bad")
    wait(factory.run(MISSION, store=store, run_id="first"))
    again = wait(jobs.start(Mission(MISSION), by="Ada", restart_of="first"))
    with TestClient(board_app(built(factory), store, jobs)) as client:
        shown = card(client, "first")

    assert (shown["state"], shown["restarted_as"], shown["actions"]) == (
        "failed",
        again.run_id,
        [],
    )


def test_a_start_job_that_failed_before_saving_is_a_stalled_card_in_stopped_with_its_error(
    client: TestClient, jobs: SQLiteDispatcher
) -> None:
    run_id = started(client)
    job = wait(jobs.claim(LEASE))
    assert job is not None
    wait(jobs.finish(job.job_id, error="The store was busy for too long, so the job stopped."))

    shown = card(client, run_id)
    assert (shown["column"], shown["state"], shown["actions"]) == (
        "stopped",
        "stalled",
        ["start_again"],
    )
    assert shown["reason"] == "The store was busy for too long, so the job stopped."
    assert shown["mission"] == MISSION


def test_a_failed_start_can_be_started_again(client: TestClient, jobs: SQLiteDispatcher) -> None:
    run_id = started(client)
    job = wait(jobs.claim(LEASE))
    assert job is not None
    wait(jobs.finish(job.job_id, error="The store was busy for too long, so the job stopped."))

    response = client.post(f"/api/runs/{run_id}/restart", headers=ADA)

    assert response.status_code == 202, response.text
    restart = wait(jobs.job(response.json()["job_id"]))
    assert restart.restart_of == run_id
    assert restart.mission is not None and restart.mission.objective == MISSION
    assert card(client, run_id)["restarted_as"] == restart.run_id


def test_a_waiting_run_whose_answer_job_lapsed_stays_waiting_without_start_again(
    client: TestClient,
    factory: Factory,
    store: SQLiteStore,
    jobs: SQLiteDispatcher,
    clock: Clock,
) -> None:
    run = waiting_run(client, factory, store, jobs)
    wait(jobs.answer(run.approve(by="Ada")))
    claim_in_the_past(jobs, clock)

    shown = card(client, run.run_id)
    assert (shown["column"], shown["station"], shown["state"]) == ("station", "draft", "waiting")
    assert shown["actions"] == ["approve", "changes", "reject"]


def test_a_losing_answer_job_does_not_stall_the_run_the_winning_job_is_driving(
    client: TestClient, factory: Factory, store: SQLiteStore, jobs: SQLiteDispatcher
) -> None:
    run = waiting_run(client, factory, store, jobs)
    winner = wait(jobs.answer(run.approve(by="Ada")))
    wait(jobs.answer(run.request_changes(by="Ben", note="Shorter, please.")))
    claimed = wait(jobs.claim(LEASE))
    assert claimed is not None and claimed.job_id == winner.job_id
    assert winner.answer is not None
    with pytest.raises(Stop):
        wait(factory.resume(winner.answer, store=store, on_step=stop))
    loser = wait(jobs.claim(LEASE))
    assert loser is not None and loser.answer is not None
    with pytest.raises(RunConflict):
        wait(factory.resume(loser.answer, store=store))
    wait(jobs.finish(loser.job_id, error="Someone answered first:", refused=True))

    shown = card(client, run.run_id)
    assert (shown["column"], shown["station"], shown["state"]) == ("station", "review", "running")


def test_a_card_whose_answer_job_lapsed_is_back_in_needs_you(
    client: TestClient,
    factory: Factory,
    store: SQLiteStore,
    jobs: SQLiteDispatcher,
    clock: Clock,
) -> None:
    run = waiting_run(client, factory, store, jobs)
    assert card(client, run.run_id)["needs_you"] is True
    client.post(
        f"/api/runs/{run.run_id}/answer",
        json={"kind": "approve", "revision": run.revision},
        headers=ADA,
    )
    queued = card(client, run.run_id)
    assert (queued["queued"], queued["needs_you"]) == (True, False)

    claim_in_the_past(jobs, clock)

    lapsed = card(client, run.run_id)
    assert (lapsed["queued"], lapsed["needs_you"]) == (False, True)


def test_a_running_run_with_no_job_shows_as_running_in_its_column(
    client: TestClient, factory: Factory, store: SQLiteStore
) -> None:
    run = running_run(factory, store, "scripted")

    shown = card(client, "scripted")
    assert (shown["column"], shown["station"], shown["state"], shown["actions"]) == (
        "station",
        "draft",
        "running",
        [],
    )
    assert shown["updated_at"] == run.updated_at.isoformat().replace("+00:00", "Z")


# Identity.


def test_a_name_outside_latin_1_survives_the_header(
    client: TestClient, jobs: SQLiteDispatcher
) -> None:
    response = client.post(
        "/api/runs", json={"mission": MISSION}, headers={"X-Factory-Name": quote("Łukasz")}
    )

    assert response.status_code == 202
    assert wait(jobs.job(response.json()["job_id"])).by == "Łukasz"


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
    monkeypatch.setattr("pydantic_ai_factory.serve._app.PAGE", 2)
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


# Building the app.


def test_a_factory_whose_first_station_takes_no_mission_is_refused(
    store: SQLiteStore, jobs: SQLiteDispatcher
) -> None:
    @station
    async def count(ctx: StationContext, number: int) -> str:
        return str(number)

    with pytest.raises(BoardUnsupported, match="count"):
        board_app(built(Factory("numbers", [count])), store, jobs)


def test_without_a_built_interface_the_root_names_the_build_command(
    factory: Factory,
    store: SQLiteStore,
    jobs: SQLiteDispatcher,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr("pydantic_ai_factory.serve._app.STATIC", tmp_path / "not-built")
    with caplog.at_level(logging.WARNING, logger="pydantic_ai_factory.serve"):
        app = board_app(built(factory), store, jobs)

    with TestClient(app) as client:
        page = client.get("/")

    assert page.status_code == 200
    assert "npm --prefix web/board ci &amp;&amp; npm --prefix web/board run build" in page.text
    assert "npm --prefix web/board" in caplog.text


def test_a_runs_output_is_rendered_by_its_station_and_falls_back_to_json(
    client: TestClient, factory: Factory, store: SQLiteStore, jobs: SQLiteDispatcher
) -> None:
    run = waiting_run(client, factory, store, jobs)
    waiting = client.get(f"/api/runs/{run.run_id}").json()
    assert (waiting["output"], waiting["output_format"]) == (
        f"# Draft\n\nFirst draft of {MISSION}",
        "markdown",
    )

    wait(jobs.answer(run.approve(by="Ada")))
    serve(factory, store, jobs)
    finished = client.get(f"/api/runs/{run.run_id}").json()

    assert finished["state"] == "passed"
    assert finished["output_format"] == "json"
    assert json.loads(finished["output"]) == {"verdict": "good"}
    assert [answer["by"] for answer in finished["answers"]] == ["Ada"]


def test_the_web_process_serves_the_board_with_no_model_key(
    store: SQLiteStore, jobs: SQLiteDispatcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic_ai_factory_software import SoftwareFactory

    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    given: list[Model | str] = []

    def build(model: Model | str) -> Factory:
        given.append(model)
        return SoftwareFactory(model)

    with TestClient(board_app(build, store, jobs)) as client:
        response = client.get("/api/board")

    assert response.status_code == 200
    assert [station["name"] for station in response.json()["shape"]["stations"]] == ["design"]
    assert [type(model) for model in given] == [TestModel]


def test_a_reply_to_an_agents_question_is_built_from_the_runs_wait_and_queued(
    store: SQLiteStore, jobs: SQLiteDispatcher
) -> None:
    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        question = ToolCallPart("ask_person", {"question": "Which colour?"}, "q1")
        return ModelResponse(parts=[question])

    asker = Agent(FunctionModel(model), name="asker", capabilities=[AskPerson()])
    factory = Factory("colours", [station(asker)])
    run = wait(factory.run(MISSION, store=store, run_id="asking"))
    with TestClient(board_app(built(factory), store, jobs)) as client:
        shown = client.get("/api/runs/asking").json()
        wrong = client.post(
            "/api/runs/asking/answer",
            json={"kind": "reply", "revision": run.revision, "replies": {"q9": "Blue."}},
            headers=ADA,
        )
        right = client.post(
            "/api/runs/asking/answer",
            json={"kind": "reply", "revision": run.revision, "replies": {"q1": "Blue."}},
            headers=ADA,
        )

    assert shown["actions"] == ["reply"]
    assert [call["question"] for call in shown["wait"]["questions"]] == ["Which colour?"]
    assert wrong.status_code == 422
    assert right.status_code == 202, right.text
    (job,) = answer_jobs(jobs)
    assert isinstance(job.answer, Reply)
    assert job.answer.results.calls == {"q1": "Blue."}
    assert job.answer.results.answered_by == {"q1": "Ada"}


def test_without_the_web_extra_board_app_says_to_install_it(
    factory: Factory,
    store: SQLiteStore,
    jobs: SQLiteDispatcher,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pydantic_ai_factory.serve as serve_module

    monkeypatch.setitem(sys.modules, "fastapi", None)
    monkeypatch.delitem(sys.modules, "pydantic_ai_factory.serve._app")
    monkeypatch.delattr(serve_module, "_app")

    with pytest.raises(ImportError, match=r"pydantic-ai-factory\[web\]"):
        board_app(built(factory), store, jobs)
