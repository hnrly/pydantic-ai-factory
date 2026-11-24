"""The board cards each run by the first rule that fits."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pydantic_ai_factory import (
    Answer,
    Factory,
    Job,
    Mission,
    RunConflict,
    SQLiteStore,
)
from pydantic_ai_factory.factory import JobState
from pydantic_ai_factory.serve import SQLiteDispatcher, board_app, utc_now
from pydantic_ai_factory.serve._board.cards import (
    answer_actions,
    link_restarts,
    run_card,
    start_card,
)

from .support import (
    ADA,
    LEASE,
    MISSION,
    Clock,
    Stop,
    built,
    card,
    claim_in_the_past,
    reports,
    running_run,
    serve,
    started,
    stop,
    wait,
    waiting_run,
)

# Columns and cards.


def test_the_board_has_a_column_per_station_then_done_and_stopped(client: TestClient) -> None:
    board = client.get("/api/board").json()

    assert [station["name"] for station in board["shape"]["stations"]] == ["draft", "review"]
    assert board["more"] == {"done": False, "stopped": False}
    assert board["cards"] == []


def test_a_queued_mission_is_a_card_in_missions_before_its_run_is_saved(
    client: TestClient, store: SQLiteStore
) -> None:
    run_id = started(client, "A weekly report.\nWith charts.")

    queued = card(client, run_id)
    assert (queued["column"], queued["station"], queued["state"]) == ("missions", None, "queued")
    assert queued["mission"] == "A weekly report."
    assert wait(store.list_runs()) == []


def test_a_mission_added_through_the_api_appears_in_missions_then_moves_to_its_station(
    client: TestClient, factory: Factory, store: SQLiteStore, jobs: SQLiteDispatcher
) -> None:
    response = client.post("/api/runs", json={"mission": "A to-do list."}, headers=ADA)
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]

    added = card(client, run_id)
    assert (added["column"], added["state"], added["queued"]) == ("missions", "queued", True)

    serve(factory, store, jobs)
    moved = card(client, run_id)
    assert (moved["column"], moved["station"], moved["state"]) == ("station", "draft", "waiting")


def test_a_claimed_mission_with_no_saved_run_stays_in_missions_as_starting(
    client: TestClient, store: SQLiteStore, jobs: SQLiteDispatcher
) -> None:
    run_id = started(client)
    claimed = wait(jobs.claim(LEASE))
    assert claimed is not None and claimed.run_id == run_id

    starting = card(client, run_id)
    assert (starting["column"], starting["station"], starting["state"]) == (
        "missions",
        None,
        "starting",
    )
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


# The rules themselves, read without the app.


def a_job(state: JobState, *, answer: Answer | None = None, lapsed: bool = False) -> Job:
    """A start job, or an answer job when ``answer`` is given, saved just now."""
    now = utc_now()
    return Job(
        job_id=f"job-{state}",
        run_id="run-1",
        kind="start" if answer is None else "answer",
        state=state,
        by="Ada",
        mission=Mission(f"{MISSION}\nMore detail.") if answer is None else None,
        answer=answer,
        error="the worker stopped" if state == "failed" else None,
        created_at=now,
        updated_at=now,
        claimed_until=now + (-LEASE if lapsed else LEASE) if state == "claimed" else None,
    )


def test_rule_1_a_passed_run_is_done_whatever_its_jobs_say(
    factory: Factory, store: SQLiteStore
) -> None:
    run = running_run(factory, store).model_copy(update={"outcome": "passed", "finished": True})

    made = run_card(factory, run, MISSION, [a_job("failed")], utc_now())

    assert (made.column, made.state, made.actions) == ("done", "passed", [])


def test_rule_2_a_run_of_another_shape_can_be_started_again_only_while_it_is_live(
    factory: Factory, store: SQLiteStore
) -> None:
    live = running_run(factory, store).model_copy(update={"factory_digest": "0" * 64})
    ended = live.model_copy(update={"outcome": "failed", "finished": True})

    assert run_card(factory, live, MISSION, [], utc_now()).actions == ["start_again"]
    assert run_card(factory, ended, MISSION, [], utc_now()).actions == []
    assert run_card(factory, ended, MISSION, [], utc_now()).state == "earlier_version"


def test_rule_3_a_waiting_run_needs_you_until_an_answer_is_queued(
    factory: Factory, store: SQLiteStore
) -> None:
    run = wait(factory.run(MISSION, store=store, run_id="run-1"))
    assert run.waiting is not None

    alone = run_card(factory, run, MISSION, [], utc_now())
    answered = run_card(
        factory, run, MISSION, [a_job("queued", answer=run.approve(by="Ada"))], utc_now()
    )

    assert (alone.state, alone.needs_you, alone.queued) == ("waiting", True, False)
    assert (answered.needs_you, answered.queued) == (False, True)
    assert alone.actions == answer_actions(run) == ["approve", "changes", "reject"]


def test_rule_7_a_running_run_whose_job_lapsed_is_stalled_and_rule_8_otherwise_running(
    factory: Factory, store: SQLiteStore
) -> None:
    run = running_run(factory, store)

    stalled = run_card(factory, run, MISSION, [a_job("claimed", lapsed=True)], utc_now())
    running = run_card(factory, run, MISSION, [a_job("claimed")], utc_now())

    assert (stalled.column, stalled.state, stalled.actions) == (
        "stopped",
        "stalled",
        ["start_again"],
    )
    assert (running.column, running.station, running.state) == ("station", run.station, "running")


def test_rules_5_and_6_card_a_start_job_with_no_saved_run_by_its_state() -> None:
    now = utc_now()

    queued = start_card(a_job("queued"), now)
    starting = start_card(a_job("claimed"), now)
    failed = start_card(a_job("failed"), now)
    lapsed = start_card(a_job("claimed", lapsed=True), now)

    assert (queued.column, queued.state, queued.mission) == ("missions", "queued", MISSION)
    assert (starting.column, starting.state) == ("missions", "starting")
    assert (failed.column, failed.state, failed.reason) == (
        "stopped",
        "stalled",
        "the worker stopped",
    )
    assert (lapsed.column, lapsed.state) == ("stopped", "stalled")


def test_a_card_started_again_links_to_its_new_run_and_loses_start_again() -> None:
    made = start_card(a_job("failed"), utc_now())
    again = a_job("queued").model_copy(update={"run_id": "run-2", "restart_of": "run-1"})

    link_restarts([made], {"run-1": again})

    assert (made.restarted_as, made.actions) == ("run-2", [])
