"""The run panel shows who started a run, its work, its track and its history."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi.testclient import TestClient

from pydantic_ai_factory import (
    Factory,
    Rework,
    SQLiteStore,
    StationContext,
    station,
)
from pydantic_ai_factory.methodologies import Finding, GoldfishReport, GoldfishResult
from pydantic_ai_factory.serve import SQLiteDispatcher, board_app

from .support import (
    BEN,
    MISSION,
    Clock,
    Review,
    built,
    claim_in_the_past,
    good,
    reports,
    running_run,
    serve,
    started,
    wait,
    waiting_run,
)


def test_a_runs_output_is_shown_in_its_stations_format(
    client: TestClient, factory: Factory, store: SQLiteStore, jobs: SQLiteDispatcher
) -> None:
    run = waiting_run(client, factory, store, jobs)
    waiting = client.get(f"/api/runs/{run.run_id}").json()
    assert (waiting["output"], waiting["output_format"]) == (
        f"First draft of {MISSION}",
        "markdown",
    )

    wait(jobs.answer(run.approve(by="Ada")))
    serve(factory, store, jobs)
    finished = client.get(f"/api/runs/{run.run_id}").json()

    assert finished["state"] == "passed"
    assert finished["output_format"] == "json"
    assert json.loads(finished["output"]) == {"verdict": "good"}
    assert [answer["by"] for answer in finished["answers"]] == ["Ada"]


# The run panel: who, when, the track and the history.


def detail(client: TestClient, run_id: str) -> dict[str, Any]:
    response = client.get(f"/api/runs/{run_id}")
    assert response.status_code == 200, response.text
    return response.json()


def test_a_waiting_run_says_who_started_it_when_and_since_when_it_waits(
    client: TestClient, factory: Factory, store: SQLiteStore, jobs: SQLiteDispatcher
) -> None:
    run = waiting_run(client, factory, store, jobs)

    shown = detail(client, run.run_id)

    assert shown["started_by"] == "Ada"
    assert datetime.fromisoformat(shown["created_at"]) == run.created_at
    assert datetime.fromisoformat(shown["waiting_since"]) == run.updated_at
    assert shown["factory_version"] == "1"


def test_the_track_lists_every_station_with_the_work_at_its_station_and_attempt(
    client: TestClient, factory: Factory, store: SQLiteStore, jobs: SQLiteDispatcher
) -> None:
    run = waiting_run(client, factory, store, jobs)

    track = detail(client, run.run_id)["track"]

    assert track == [
        {"station": "draft", "state": "here", "attempt": 1},
        {"station": "review", "state": "ahead", "attempt": None},
    ]


def test_a_queued_mission_has_every_station_ahead_and_says_who_added_it(
    client: TestClient,
) -> None:
    run_id = started(client)

    shown = detail(client, run_id)

    assert [stop["state"] for stop in shown["track"]] == ["ahead", "ahead"]
    assert shown["started_by"] == "Ada"
    assert [event["title"] for event in shown["history"]] == ["Mission added by Ada"]


def test_a_reworked_run_shows_its_second_attempt_on_the_track_and_in_its_history(
    store: SQLiteStore, jobs: SQLiteDispatcher
) -> None:
    factory = reports(approval=False, verdict="bad", rework=True)
    with TestClient(board_app(built(factory), store, jobs)) as client:
        run_id = started(client)
        serve(factory, store, jobs)

        shown = detail(client, run_id)

    assert shown["track"] == [
        {"station": "draft", "state": "here", "attempt": 2},
        {"station": "review", "state": "ahead", "attempt": None},
    ]
    titles = [event["title"] for event in shown["history"]]
    assert titles[0] == "Escalated to a person"
    assert "draft started, attempt 2" in titles
    assert "Gate failed" in titles
    assert titles[-1] == "Mission added by Ada"


def test_a_passed_run_has_every_station_done_and_its_history_ends_in_done(
    client: TestClient, factory: Factory, store: SQLiteStore, jobs: SQLiteDispatcher
) -> None:
    run = waiting_run(client, factory, store, jobs)
    wait(jobs.answer(run.approve(by="Ben", note="Looks right.")))
    serve(factory, store, jobs)

    shown = detail(client, run.run_id)

    assert [(stop["state"], stop["attempt"]) for stop in shown["track"]] == [
        ("done", 1),
        ("done", 1),
    ]
    history = shown["history"]
    assert [event["kind"] for event in history] == [
        "done",
        "gate",
        "attempt",
        "answer",
        "attempt",
        "mission",
    ]
    approved = history[3]
    assert (approved["title"], approved["detail"], approved["by"]) == (
        "Approved by Ben",
        "Looks right.",
        "Ben",
    )
    assert approved["at"] is not None
    assert history[1]["detail"] == "good"
    assert history[2]["at"] is None, "an attempt's start time is not recorded, so none is given"


def test_an_answer_to_an_escalation_after_rework_ran_out_sits_before_the_next_station(
    store: SQLiteStore, jobs: SQLiteDispatcher
) -> None:
    @station()
    async def draft(ctx: StationContext, mission: str) -> str:
        return f"Draft of {mission}"

    @station(gate=good)
    async def review(ctx: StationContext, text: str) -> Review:
        return Review(verdict="bad")

    @station(approval=True)
    async def publish(ctx: StationContext, review: Review) -> str:
        return "Published"

    factory = Factory(
        "escalating",
        [draft, review, publish],
        rework=[Rework(review, draft, limit=1, on_limit="escalate")],
    )
    with TestClient(board_app(built(factory), store, jobs)) as client:
        run_id = started(client)
        serve(factory, store, jobs)
        escalated = wait(store.get_run(run_id))
        assert escalated.waiting is not None and escalated.waiting.kind == "escalation"
        wait(jobs.answer(escalated.approve(by="Ben")))
        serve(factory, store, jobs)
        publishing = wait(store.get_run(run_id))
        assert publishing.waiting is not None and publishing.waiting.station == "publish"
        wait(jobs.answer(publishing.approve(by="Cy")))
        serve(factory, store, jobs)

        history = detail(client, run_id)["history"]

    titles = [event["title"] for event in reversed(history)]
    assert titles == [
        "Mission added by Ada",
        "draft started, attempt 1",
        "review started, attempt 1",
        "Gate failed",
        "draft started, attempt 2",
        "review started, attempt 2",
        "Gate failed",
        "Approved by Ben",
        "publish started, attempt 1",
        "Approved by Cy",
        "Done",
    ]


def report(round: int, *findings: Finding) -> GoldfishReport:
    return GoldfishReport(
        round=round,
        rounds=2,
        draft_digest="sha256:" + "0" * 64,
        results=(GoldfishResult(goldfish="critique", stage="critique", findings=findings),),
    )


def test_the_history_shows_each_review_round_from_the_runs_evidence_and_counts_the_rest(
    store: SQLiteStore, jobs: SQLiteDispatcher
) -> None:
    blank = Finding(
        id="r1-critique-1",
        goldfish="critique",
        stage="critique",
        severity="Major",
        issue="A blank task is not covered.",
    )
    nit = blank.model_copy(update={"id": "r1-critique-2", "severity": "Nit", "issue": "Typo."})

    @station(approval=True)
    async def design(ctx: StationContext, mission: str) -> str:
        await ctx.record(report(1, blank, nit))
        await ctx.record(report(2))
        await ctx.record({"checked": True}, type="Checklist")
        return mission

    factory = Factory("reviewed", [design])
    with TestClient(board_app(built(factory), store, jobs)) as client:
        run_id = started(client)
        serve(factory, store, jobs)

        history = detail(client, run_id)["history"]

    titles = [(event["kind"], event["title"]) for event in history]
    assert titles == [
        ("waiting", "Waiting for approval"),
        ("evidence", "1 piece of evidence recorded"),
        ("review", "Goldfish round 2: clean"),
        ("review", "Goldfish round 1: 2 findings"),
        ("attempt", "design started, attempt 1"),
        ("mission", "Mission added by Ada"),
    ]
    assert history[3]["detail"] == "Major: A blank task is not covered. Nit: Typo."
    assert history[3]["at"] is not None


def test_a_stalled_run_started_again_shows_the_lapse_and_who_started_it_again_with_a_link(
    client: TestClient,
    factory: Factory,
    store: SQLiteStore,
    jobs: SQLiteDispatcher,
    clock: Clock,
) -> None:
    run_id = started(client)
    claim_in_the_past(jobs, clock)
    running_run(factory, store, run_id)

    stalled = detail(client, run_id)
    assert stalled["track"][0] == {"station": "draft", "state": "stopped", "attempt": 1}
    assert [event["title"] for event in stalled["history"]] == [
        "Claim lapsed. No word from the worker.",
        "draft started, attempt 1",
        "Mission added by Ada",
    ]
    assert stalled["history"][0]["at"] == stalled["job"]["claimed_until"]

    restarted = client.post(f"/api/runs/{run_id}/restart", headers=BEN).json()["run_id"]
    ended = detail(client, run_id)

    newest = ended["history"][0]
    assert (newest["kind"], newest["title"], newest["by"], newest["run_id"]) == (
        "restarted",
        "Started again by Ben",
        "Ben",
        restarted,
    )
    assert ended["history"][1]["title"] == "Ended by Ben"
