"""What the board tests share: a small factory, a clock, and ways to drive the app."""

from __future__ import annotations

from collections.abc import Coroutine
from datetime import datetime, timedelta
from typing import Any, TypeVar

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel

from pydantic_ai_factory import (
    Artifact,
    Escalate,
    Evidence,
    Factory,
    Job,
    OutputFormat,
    Rework,
    Run,
    SQLiteStore,
    StationContext,
    Verdict,
    failed,
    gate,
    passed,
    station,
)
from pydantic_ai_factory.contracts import run_sync
from pydantic_ai_factory.serve import SQLiteDispatcher, utc_now, work

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


def reports(
    *, version: str = "1", approval: bool = True, verdict: str = "good", rework: bool = False
) -> Factory:
    """A two-station factory: a draft a person may approve, then a review with a gate.

    With ``rework``, a review that is not good sends the work back to the draft, which
    then escalates to a person.
    """

    @station(approval=approval, output_format=OutputFormat.MARKDOWN)
    async def draft(ctx: StationContext, mission: str) -> str:
        if ctx.feedback is not None:
            raise Escalate("Look at the rework", output=f"Second draft of {mission}")
        return f"First draft of {mission}"

    @station(gate=good, output_format=OutputFormat.JSON)
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
