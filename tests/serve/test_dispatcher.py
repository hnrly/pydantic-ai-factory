"""SQLiteDispatcher queues a factory's work in the store's file, and keeps each claim honest."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from pydantic_ai_factory import (
    AlreadyRestarted,
    Approve,
    Dispatcher,
    Job,
    JobNotFound,
    Mission,
    SQLiteStore,
)
from pydantic_ai_factory.serve import STARTED_AGAIN, SQLiteDispatcher, SQLiteTooOld

pytestmark = pytest.mark.anyio

LEASE = timedelta(minutes=2)
SECOND = timedelta(seconds=1)


class Clock:
    """A clock that stands still until a test moves it on."""

    def __init__(self) -> None:
        self.now = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, by: timedelta) -> datetime:
        self.now += by
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def jobs(tmp_path: Path, clock: Clock) -> SQLiteDispatcher:
    return SQLiteDispatcher(tmp_path / "factory.db", clock=clock)


def approve(run_id: str, by: str = "Ada") -> Approve:
    return Approve(
        run_id=run_id,
        revision=1,
        station="design",
        waiting="approval",
        work=None,
        by=by,
        at=datetime(2026, 11, 20, tzinfo=UTC),
    )


async def test_jobs_are_claimed_oldest_first_and_each_only_once(jobs: SQLiteDispatcher) -> None:
    first = await jobs.start(Mission("A to-do list web page."), by="Ada")
    second = await jobs.answer(approve("run-1"))

    claimed = [await jobs.claim(LEASE), await jobs.claim(LEASE), await jobs.claim(LEASE)]

    assert [job.job_id if job else None for job in claimed] == [first.job_id, second.job_id, None]
    assert all(job is not None and job.state == "claimed" for job in claimed[:2])


async def test_a_finished_job_is_done_or_failed_with_its_reason(jobs: SQLiteDispatcher) -> None:
    good = await jobs.start(Mission("A to-do list web page."), by="Ada")
    bad = await jobs.start(Mission("A shopping list web page."), by="Ada")
    await jobs.claim(LEASE)
    await jobs.claim(LEASE)

    assert (await jobs.finish(good.job_id)).state == "done"
    failed = await jobs.finish(bad.job_id, error="It went wrong.", refused=True)

    assert (failed.state, failed.error, failed.refused) == ("failed", "It went wrong.", True)


async def test_reading_a_job_that_does_not_exist_raises_job_not_found(
    jobs: SQLiteDispatcher,
) -> None:
    with pytest.raises(JobNotFound):
        await jobs.job("nothing")
    with pytest.raises(JobNotFound):
        await jobs.finish("nothing")


async def test_a_queued_job_reads_back_with_its_mission_or_its_answer(
    jobs: SQLiteDispatcher,
) -> None:
    mission = Mission("A to-do list web page.", success_criteria=["Tasks can be ticked off."])
    start = await jobs.start(mission, by="Ada")
    answer = await jobs.answer(approve("run-1", by="Bo"))

    assert await jobs.job(start.job_id) == start
    assert (await jobs.job(start.job_id)).mission == mission
    assert await jobs.job(answer.job_id) == answer
    assert (answer.run_id, answer.by) == ("run-1", "Bo")


async def test_a_sqlite_dispatcher_is_a_dispatcher(jobs: SQLiteDispatcher) -> None:
    assert isinstance(jobs, Dispatcher)


async def test_a_running_job_keeps_its_claim_with_a_heartbeat(
    jobs: SQLiteDispatcher, clock: Clock
) -> None:
    await jobs.start(Mission("A to-do list web page."), by="Ada")
    job = await jobs.claim(LEASE)
    assert job is not None

    beat = clock.advance(timedelta(minutes=1))
    assert await jobs.extend(job.job_id, LEASE)
    now = clock.advance(timedelta(minutes=1, seconds=30))  # past the first lease

    kept = await jobs.job(job.job_id)
    assert job.stalled(now)
    assert not kept.stalled(now)
    assert kept.claimed_until == beat + LEASE


async def test_a_lapsed_claim_is_reported_and_never_claimed_again(
    jobs: SQLiteDispatcher, clock: Clock
) -> None:
    first = await jobs.start(Mission("A to-do list web page."), by="Ada")
    second = await jobs.start(Mission("A shopping list web page."), by="Ada")
    await jobs.claim(LEASE)

    now = clock.advance(LEASE + timedelta(seconds=1))

    [claimed] = await jobs.list_jobs(state="claimed")
    assert claimed.job_id == first.job_id
    assert claimed.stalled(now)
    again = await jobs.claim(LEASE)
    assert again is not None and again.job_id == second.job_id
    assert await jobs.claim(LEASE) is None


async def test_a_worker_whose_claim_lapsed_may_still_finish_its_job(
    jobs: SQLiteDispatcher, clock: Clock
) -> None:
    job = await jobs.start(Mission("A to-do list web page."), by="Ada")
    await jobs.claim(LEASE)
    clock.advance(LEASE * 3)

    finished = await jobs.finish(job.job_id)

    assert finished.state == "done"
    assert not finished.stalled(clock())


async def test_every_change_to_a_job_moves_its_updated_at(
    jobs: SQLiteDispatcher, clock: Clock
) -> None:
    queued = await jobs.start(Mission("A to-do list web page."), by="Ada")
    seen = [queued.updated_at]

    clock.advance(timedelta(seconds=1))
    claimed = await jobs.claim(LEASE)
    assert claimed is not None
    seen.append(claimed.updated_at)
    clock.advance(timedelta(seconds=1))
    await jobs.extend(queued.job_id, LEASE)
    seen.append((await jobs.job(queued.job_id)).updated_at)
    clock.advance(timedelta(seconds=1))
    seen.append((await jobs.finish(queued.job_id)).updated_at)

    start = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
    assert seen == [start + timedelta(seconds=n) for n in range(4)]
    assert queued.created_at == start


async def test_a_restart_moves_the_updated_at_of_the_job_it_closes(
    jobs: SQLiteDispatcher, clock: Clock
) -> None:
    old = await jobs.start(Mission("A to-do list web page."), by="Ada")
    later = clock.advance(timedelta(minutes=5))

    await jobs.start(Mission("A to-do list web page."), by="Bo", restart_of=old.run_id)

    assert (await jobs.job(old.job_id)).updated_at == later


async def test_a_job_holds_a_mission_or_an_answer_to_match_its_kind() -> None:
    now = datetime(2026, 11, 20, tzinfo=UTC)
    common = {"job_id": "j1", "run_id": "run-1", "state": "queued", "by": "Ada"}
    times = {"created_at": now, "updated_at": now}
    mission = Mission("A to-do list web page.")

    Job.model_validate({**common, **times, "kind": "start", "mission": mission})
    Job.model_validate({**common, **times, "kind": "answer", "answer": approve("run-1")})
    for wrong in (
        {"kind": "start"},
        {"kind": "answer"},
        {"kind": "start", "mission": mission, "answer": approve("run-1")},
        {"kind": "answer", "mission": mission},
        {"kind": "answer", "answer": approve("another-run")},
    ):
        with pytest.raises(ValidationError):
            Job.model_validate({**common, **times, **wrong})


async def test_extend_after_a_lapse_returns_false_and_leaves_the_job_stalled(
    jobs: SQLiteDispatcher, clock: Clock
) -> None:
    job = await jobs.start(Mission("A to-do list web page."), by="Ada")
    claimed = await jobs.claim(LEASE)
    assert claimed is not None
    now = clock.advance(LEASE)

    assert not await jobs.extend(job.job_id, LEASE)

    after = await jobs.job(job.job_id)
    assert after.stalled(now)
    assert after.claimed_until == claimed.claimed_until


async def test_a_restart_closes_the_old_job_and_a_second_restart_is_refused(
    jobs: SQLiteDispatcher,
) -> None:
    old = await jobs.start(Mission("A to-do list web page."), by="Ada")
    await jobs.claim(LEASE)

    new = await jobs.start(Mission("A to-do list web page."), by="Bo", restart_of=old.run_id)

    closed = await jobs.job(old.job_id)
    assert (closed.state, closed.error, closed.refused) == ("failed", STARTED_AGAIN, False)
    assert (new.state, new.restart_of) == ("queued", old.run_id)
    assert new.run_id != old.run_id
    with pytest.raises(AlreadyRestarted):
        await jobs.start(Mission("A to-do list web page."), by="Cy", restart_of=old.run_id)
    assert [job.job_id for job in await jobs.list_jobs(kind="start")] == [
        new.job_id,
        old.job_id,
    ]


async def test_a_run_that_was_never_queued_can_still_be_started_again(
    jobs: SQLiteDispatcher,
) -> None:
    new = await jobs.start(Mission("A to-do list web page."), by="Bo", restart_of="lost-run")

    assert [job.job_id for job in await jobs.list_jobs(restart_of="lost-run")] == [new.job_id]


async def test_finishing_a_job_that_a_restart_closed_keeps_the_restart_link(
    jobs: SQLiteDispatcher,
) -> None:
    old = await jobs.start(Mission("A to-do list web page."), by="Ada")
    await jobs.claim(LEASE)
    new = await jobs.start(Mission("A to-do list web page."), by="Bo", restart_of=old.run_id)

    finished = await jobs.finish(old.job_id)

    assert (finished.state, finished.error) == ("failed", STARTED_AGAIN)
    [linked] = await jobs.list_jobs(restart_of=[old.run_id])
    assert linked.job_id == new.job_id


async def test_jobs_are_listed_newest_change_first_and_filtered(
    jobs: SQLiteDispatcher, clock: Clock
) -> None:
    first = await jobs.start(Mission("A to-do list web page."), by="Ada")
    clock.advance(timedelta(seconds=1))
    second = await jobs.start(Mission("A shopping list web page."), by="Ada")
    clock.advance(timedelta(seconds=1))
    answer = await jobs.answer(approve(first.run_id))
    middle = clock.advance(timedelta(seconds=1))
    await jobs.claim(LEASE)  # the first start job changes last

    listed = await jobs.list_jobs()
    assert [job.job_id for job in listed] == [first.job_id, answer.job_id, second.job_id]
    assert [job.job_id for job in await jobs.list_jobs(state=["queued"], kind="start")] == [
        second.job_id
    ]
    assert [job.job_id for job in await jobs.list_jobs(updated_after=middle - SECOND)] == [
        first.job_id
    ]
    assert [job.job_id for job in await jobs.list_jobs(updated_before=middle)] == [
        answer.job_id,
        second.job_id,
    ]
    assert len(await jobs.list_jobs(limit=1)) == 1
    with pytest.raises(ValueError, match="limit"):
        await jobs.list_jobs(limit=0)


async def test_latest_per_run_filters_each_runs_newest_job(
    jobs: SQLiteDispatcher, clock: Clock
) -> None:
    start = await jobs.start(Mission("A to-do list web page."), by="Ada")
    clock.advance(timedelta(seconds=1))
    answer = await jobs.answer(approve(start.run_id))
    clock.advance(timedelta(seconds=1))
    other = await jobs.start(Mission("A shopping list web page."), by="Ada")
    clock.advance(timedelta(seconds=1))
    await jobs.claim(LEASE)  # the start job is claimed, but its run's newest job is queued

    latest = await jobs.list_jobs(latest_per_run=True)
    assert [job.job_id for job in latest] == [other.job_id, answer.job_id]
    assert await jobs.list_jobs(latest_per_run=True, state="claimed") == []


async def test_latest_per_run_skips_refused_jobs_to_find_the_job_driving_the_run(
    jobs: SQLiteDispatcher, clock: Clock
) -> None:
    start = await jobs.start(Mission("A to-do list web page."), by="Ada")
    await jobs.claim(LEASE)
    await jobs.finish(start.job_id)
    clock.advance(SECOND)
    first = await jobs.answer(approve(start.run_id, by="Ada"))
    clock.advance(SECOND)
    second = await jobs.answer(approve(start.run_id, by="Bo"))
    clock.advance(SECOND)
    # Answer A saves the run, then fails, and the run could not be abandoned.
    claimed = await jobs.claim(LEASE)
    assert claimed is not None and claimed.job_id == first.job_id
    await jobs.finish(first.job_id, error="The job failed: RuntimeError: the disk is full")
    clock.advance(SECOND)
    # Answer B is refused, so it never touched the run.
    await jobs.claim(LEASE)
    await jobs.finish(second.job_id, error="Someone answered first: moved on", refused=True)

    [latest] = await jobs.list_jobs(latest_per_run=True)
    assert (latest.job_id, latest.state, latest.refused) == (first.job_id, "failed", False)
    [failed] = await jobs.list_jobs(latest_per_run=True, state="failed")
    assert failed.job_id == first.job_id


async def test_the_jobs_share_a_file_with_the_store_and_leave_its_version_alone(
    tmp_path: Path,
) -> None:
    path = tmp_path / "factory.db"
    jobs = SQLiteDispatcher(path)
    await jobs.start(Mission("A to-do list web page."), by="Ada")
    store = SQLiteStore(path)

    assert await store.list_runs() == []
    with sqlite3.connect(path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    connection.close()
    assert (version, mode) == (2, "wal")
    assert len(await SQLiteDispatcher(path).list_jobs()) == 1


async def test_a_sqlite_older_than_3_35_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 34, 1))
    monkeypatch.setattr(sqlite3, "sqlite_version", "3.34.1")

    with pytest.raises(SQLiteTooOld, match=r"3\.34\.1"):
        SQLiteDispatcher(tmp_path / "factory.db")
    assert not (tmp_path / "factory.db").exists()
