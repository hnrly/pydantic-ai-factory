"""The worker does each queued job once, keeps its claim alive, and fails a job honestly."""

from __future__ import annotations

import asyncio
import gc
import signal
import warnings
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel

from pydantic_ai_factory import (
    ApprovalRefused,
    Factory,
    Job,
    Mission,
    Run,
    RunConflict,
    SQLiteStore,
    StationContext,
    StoreBusy,
    station,
)
from pydantic_ai_factory.contracts import run_sync
from pydantic_ai_factory.serve import SQLiteDispatcher, work

MISSION = Mission("A to-do list web page where I can add tasks and tick them off.")


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "factory.db"


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the worker's waits short, so a test that retries stays quick."""
    monkeypatch.setattr("pydantic_ai_factory.serve._worker.POLL_SECONDS", 0.01)
    monkeypatch.setattr("pydantic_ai_factory.serve._worker.FINISH_RETRY_SECONDS", 0.01)


def built(factory: Factory) -> Any:
    """A ``build_factory`` that returns ``factory``, whatever model it is given."""

    def build(model: Model | str) -> Factory:
        return factory

    return build


def serve(factory: Factory, store: SQLiteStore, jobs: SQLiteDispatcher, **options: Any) -> None:
    """Do every queued job, then stop."""
    work(built(factory), store, jobs, model=TestModel(), until_idle=True, **options)


def one_station_factory(seen: list[Any] | None = None) -> Factory:
    @station
    async def design(ctx: StationContext, mission: str) -> str:
        if seen is not None:
            seen.append(ctx.deps)
        return "One page."

    return Factory("software", [design])


def approval_factory(seen: list[Any] | None = None) -> Factory:
    @station(approval=True)
    async def design(ctx: StationContext, mission: str) -> str:
        if seen is not None:
            seen.append(ctx.deps)
        return "One page."

    @station
    async def build(ctx: StationContext, design: str) -> str:
        if seen is not None:
            seen.append(ctx.deps)
        return f"Built: {design}"

    return Factory("software", [design, build])


def get_run(store: SQLiteStore, run_id: str) -> Run:
    return run_sync(store.get_run(run_id))


def get_job(jobs: SQLiteDispatcher, job_id: str) -> Job:
    return run_sync(jobs.job(job_id))


def waiting_run(db: Path) -> tuple[Factory, SQLiteStore, SQLiteDispatcher, Run]:
    """Start a run of the approval factory, and return it waiting for a person."""
    factory, store, jobs = approval_factory(), SQLiteStore(db), SQLiteDispatcher(db)
    started = run_sync(jobs.start(MISSION, by="Ada"))
    serve(factory, store, jobs)
    run = get_run(store, started.run_id)
    assert run.outcome == "waiting"
    return factory, store, jobs, run


class BusyStore(SQLiteStore):
    """A store whose every save of a run finds another writer holding the file too long."""

    async def put_run(self, run: Run, *, replaces: Run | None) -> None:
        raise StoreBusy("the store was busy for longer than 5.0 seconds")


class BreaksOnSecondSave(SQLiteStore):
    """A store that saves the first run, then fails the next save once."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.saves = 0

    async def put_run(self, run: Run, *, replaces: Run | None) -> None:
        self.saves += 1
        if self.saves == 2:
            raise RuntimeError("the disk is full")
        await super().put_run(run, replaces=replaces)


class FailsOnSecondSave(SQLiteStore):
    """A store that saves the first run, then raises the given error on the next save."""

    def __init__(self, path: Path, error: Exception) -> None:
        super().__init__(path)
        self.error = error
        self.saves = 0

    async def put_run(self, run: Run, *, replaces: Run | None) -> None:
        self.saves += 1
        if self.saves == 2:
            raise self.error
        await super().put_run(run, replaces=replaces)


class BusyOnFirstFinish(SQLiteDispatcher):
    """A dispatcher whose first finish finds the jobs busy."""

    finishes: int = 0

    async def finish(self, job_id: str, *, error: str | None = None, refused: bool = False) -> Job:
        self.finishes += 1
        if self.finishes == 1:
            raise StoreBusy("the jobs were busy for longer than 5.0 seconds")
        return await super().finish(job_id, error=error, refused=refused)


class FailsOnFirstClaim(SQLiteDispatcher):
    """A dispatcher whose first claim raises the given error."""

    def __init__(self, path: Path, error: Exception) -> None:
        super().__init__(path)
        self.error = error
        self.claims = 0

    async def claim(self, lease: timedelta) -> Job | None:
        self.claims += 1
        if self.claims == 1:
            raise self.error
        return await super().claim(lease)


class CountingDispatcher(SQLiteDispatcher):
    """Counts the heartbeats that kept a claim alive."""

    extended: int = 0

    async def extend(self, job_id: str, lease: timedelta) -> bool:
        kept = await super().extend(job_id, lease)
        self.extended += kept
        return kept


def test_a_start_job_runs_its_mission_under_the_job_s_run_id(db: Path) -> None:
    store, jobs = SQLiteStore(db), SQLiteDispatcher(db)
    job = run_sync(jobs.start(MISSION, by="Ada"))

    serve(one_station_factory(), store, jobs)

    assert get_job(jobs, job.job_id).state == "done"
    run = get_run(store, job.run_id)
    assert (run.outcome, run.output) == ("passed", "One page.")


def test_a_busy_store_fails_the_job_with_its_own_message(db: Path) -> None:
    store, jobs = BusyStore(db), SQLiteDispatcher(db)
    job = run_sync(jobs.start(MISSION, by="Ada"))

    serve(one_station_factory(), store, jobs)

    failed = get_job(jobs, job.job_id)
    assert (failed.state, failed.refused) == ("failed", False)
    assert failed.error is not None and "busy" in failed.error
    assert run_sync(SQLiteStore(db).list_runs()) == []


def test_the_worker_finishes_the_job_in_hand_on_the_first_signal_then_stops(db: Path) -> None:
    sent: list[bool] = []

    @station
    async def design(ctx: StationContext, mission: str) -> str:
        if not sent:
            sent.append(True)
            signal.raise_signal(signal.SIGTERM)
        return "One page."

    store, jobs = SQLiteStore(db), SQLiteDispatcher(db)
    first = run_sync(jobs.start(MISSION, by="Ada"))
    second = run_sync(jobs.start(Mission("A shopping list web page."), by="Ada"))
    before = signal.getsignal(signal.SIGTERM)

    serve(Factory("software", [design]), store, jobs)

    assert get_job(jobs, first.job_id).state == "done"
    assert get_run(store, first.run_id).outcome == "passed"
    assert get_job(jobs, second.job_id).state == "queued"
    assert signal.getsignal(signal.SIGTERM) is before


def test_the_worker_extends_its_claim_while_a_job_runs(db: Path) -> None:
    @station
    async def design(ctx: StationContext, mission: str) -> str:
        await asyncio.sleep(0.4)
        return "One page."

    store, jobs = SQLiteStore(db), CountingDispatcher(db)
    job = run_sync(jobs.start(MISSION, by="Ada"))

    serve(Factory("software", [design]), store, jobs, lease=timedelta(milliseconds=200))

    assert get_job(jobs, job.job_id).state == "done"
    assert jobs.extended >= 3


def test_the_worker_passes_deps_to_run_and_resume(db: Path) -> None:
    seen: list[Any] = []
    deps = {"repository": "todo"}
    factory, store, jobs = approval_factory(seen), SQLiteStore(db), SQLiteDispatcher(db)
    started = run_sync(jobs.start(MISSION, by="Ada"))
    serve(factory, store, jobs, deps=deps)
    waiting = get_run(store, started.run_id)

    run_sync(jobs.answer(waiting.approve(by="Ada")))
    serve(factory, store, jobs, deps=deps)

    assert get_run(store, started.run_id).outcome == "passed"
    assert seen == [deps, deps]


def test_an_answer_job_resumes_its_run_with_the_answer_it_holds(db: Path) -> None:
    factory, store, jobs, run = waiting_run(db)
    job = run_sync(jobs.answer(run.request_changes(by="Bo", note="Two pages, please.")))

    serve(factory, store, jobs)

    assert get_job(jobs, job.job_id).state == "done"
    resumed = get_run(store, run.run_id)
    assert resumed.outcome == "waiting"
    assert [(answer.kind, answer.by) for answer in resumed.answers] == [("changes", "Bo")]


def test_a_worker_that_catches_an_error_abandons_the_run_with_the_error_as_reason(
    db: Path,
) -> None:
    store, jobs = BreaksOnSecondSave(db), SQLiteDispatcher(db)
    job = run_sync(jobs.start(MISSION, by="Ada"))

    serve(one_station_factory(), store, jobs)

    failed = get_job(jobs, job.job_id)
    assert (failed.state, failed.refused) == ("failed", False)
    assert failed.error is not None and "the disk is full" in failed.error
    run = get_run(store, job.run_id)
    assert run.outcome == "did_not_run"
    assert run.abandoned is not None
    assert (run.abandoned.reason, run.abandoned.by) == (failed.error, "worker")


def test_a_refused_answer_job_never_abandons_the_run(db: Path) -> None:
    factory, store, jobs, run = waiting_run(db)
    stale = run.approve(by="Bo")
    # Another worker has taken the run on with someone else's answer, and is driving it.
    moving = Run.model_validate(
        {
            **dict(run),
            "revision": run.revision + 1,
            "outcome": "running",
            "waiting": None,
            "stopped_at": None,
        }
    )
    run_sync(store.put_run(moving, replaces=run))
    job = run_sync(jobs.answer(stale))

    serve(factory, store, jobs)

    failed = get_job(jobs, job.job_id)
    assert (failed.state, failed.refused) == ("failed", True)
    assert failed.error is not None and failed.error.startswith("Someone answered first:")
    after = get_run(store, run.run_id)
    assert (after.outcome, after.revision, after.abandoned) == ("running", moving.revision, None)


def test_a_conflict_after_the_job_saved_its_run_fails_the_job_unrefused_and_leaves_the_run(
    db: Path,
) -> None:
    store = FailsOnSecondSave(db, RunConflict("the stored run has moved on"))
    jobs = SQLiteDispatcher(db)
    job = run_sync(jobs.start(MISSION, by="Ada"))

    serve(one_station_factory(), store, jobs)

    failed = get_job(jobs, job.job_id)
    assert (failed.state, failed.refused) == ("failed", False)
    assert failed.error is not None
    assert failed.error.startswith("The run moved on while this job was working on it:")
    after = get_run(store, job.run_id)
    # The run is just as the job first saved it: nothing abandoned it.
    assert (after.outcome, after.revision, after.abandoned) == ("running", 0, None)


def test_a_slow_worker_whose_run_was_abandoned_by_start_again_fails_its_job_unrefused(
    db: Path,
) -> None:
    store, jobs = SQLiteStore(db), SQLiteDispatcher(db)
    made: list[Factory] = []

    @station
    async def design(ctx: StationContext, mission: str) -> str:
        # While this worker is slow, a person abandons the run, as Start again does.
        stuck = await store.get_run(ctx.run_id)
        await made[0].abandon(stuck, reason="Started again by Bo", by="Bo", store=store)
        return "One page."

    factory = Factory("software", [design])
    made.append(factory)
    job = run_sync(jobs.start(MISSION, by="Ada"))

    serve(factory, store, jobs)

    failed = get_job(jobs, job.job_id)
    assert (failed.state, failed.refused) == ("failed", False)
    assert failed.error is not None
    assert failed.error.startswith("The run moved on while this job was working on it:")
    after = get_run(store, job.run_id)
    assert after.abandoned is not None
    assert (after.outcome, after.abandoned.by) == ("did_not_run", "Bo")


def test_an_approval_refused_after_the_job_saved_its_run_is_not_marked_refused(
    db: Path,
) -> None:
    store = FailsOnSecondSave(db, ApprovalRefused("the run is not waiting"))
    jobs = SQLiteDispatcher(db)
    job = run_sync(jobs.start(MISSION, by="Ada"))

    serve(one_station_factory(), store, jobs)

    failed = get_job(jobs, job.job_id)
    assert (failed.state, failed.refused, failed.error) == (
        "failed",
        False,
        "the run is not waiting",
    )


def test_a_busy_finish_is_tried_again_and_the_worker_carries_on(db: Path) -> None:
    store, jobs = SQLiteStore(db), BusyOnFirstFinish(db)
    first = run_sync(jobs.start(MISSION, by="Ada"))
    second = run_sync(jobs.start(Mission("A shopping list web page."), by="Ada"))

    serve(one_station_factory(), store, jobs)

    assert jobs.finishes == 3
    assert get_job(jobs, first.job_id).state == "done"
    assert get_job(jobs, second.job_id).state == "done"


def test_an_error_in_one_turn_of_the_loop_does_not_stop_the_worker(db: Path) -> None:
    store, jobs = SQLiteStore(db), FailsOnFirstClaim(db, RuntimeError("the disk went away"))
    job = run_sync(jobs.start(MISSION, by="Ada"))

    serve(one_station_factory(), store, jobs)

    assert jobs.claims >= 2
    assert get_job(jobs, job.job_id).state == "done"


def test_a_busy_claim_until_idle_waits_and_looks_again_instead_of_stopping(db: Path) -> None:
    store, jobs = SQLiteStore(db), FailsOnFirstClaim(db, StoreBusy("the jobs were busy"))
    job = run_sync(jobs.start(MISSION, by="Ada"))

    serve(one_station_factory(), store, jobs)

    assert get_job(jobs, job.job_id).state == "done"


def test_two_answers_for_one_revision_leave_the_second_failed_as_answered_first(
    db: Path,
) -> None:
    factory, store, jobs, run = waiting_run(db)
    first = run_sync(jobs.answer(run.approve(by="Ada")))
    second = run_sync(jobs.answer(run.reject(by="Bo", note="Not this one.")))

    serve(factory, store, jobs)

    assert get_job(jobs, first.job_id).state == "done"
    lost = get_job(jobs, second.job_id)
    assert (lost.state, lost.refused) == ("failed", True)
    assert lost.error is not None and lost.error.startswith("Someone answered first:")
    after = get_run(store, run.run_id)
    assert after.outcome == "passed"
    assert [answer.by for answer in after.answers] == ["Ada"]


def test_a_job_that_another_restart_closed_stays_closed_after_the_worker_finishes(
    db: Path,
) -> None:
    store, jobs = SQLiteStore(db), SQLiteDispatcher(db)
    restarted: list[Job] = []

    @station
    async def design(ctx: StationContext, mission: str) -> str:
        if not restarted:
            [old] = await jobs.list_jobs(state="claimed")
            restarted.append(await jobs.start(MISSION, by="Bo", restart_of=old.run_id))
        return "One page."

    old = run_sync(jobs.start(MISSION, by="Ada"))

    serve(Factory("software", [design]), store, jobs)

    closed = get_job(jobs, old.job_id)
    assert (closed.state, closed.error) == ("failed", "Started again")
    assert get_job(jobs, restarted[0].job_id).state == "done"


def test_the_worker_and_the_store_s_sync_helpers_share_one_event_loop_in_one_process(
    db: Path,
) -> None:
    store, jobs = SQLiteStore(db), SQLiteDispatcher(db)
    first = run_sync(jobs.start(MISSION, by="Ada"))
    second = run_sync(jobs.start(Mission("A shopping list web page."), by="Ada"))
    runs: list[Run] = []

    with warnings.catch_warnings():
        # Any loop left unclosed would warn, and so fail the test.
        warnings.simplefilter("error")
        for job in (first, second):
            work(built(one_station_factory()), store, jobs, model=TestModel(), until_idle=True)
            runs.append(store.get_run_sync(job.run_id))
        gc.collect()

    assert [run.outcome for run in runs] == ["passed", "passed"]
