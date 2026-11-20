"""Every store that ships keeps the same rules.

The same tests run against each store through one parametrised fixture. A store written
outside this library should keep these rules too; the Stores page lists them.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import BaseModel

from pydantic_ai_factory import (
    Artifact,
    ArtifactCorrupt,
    ArtifactRef,
    LocalStore,
    MemoryStore,
    Run,
    RunConflict,
    SQLiteStore,
    StoreBusy,
    StoreSchemaMismatch,
    Waiting,
)
from pydantic_ai_factory.contracts import run_sync
from pydantic_ai_factory.factory import RunOutcome

pytestmark = pytest.mark.anyio

WHEN = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
INPUT = ArtifactRef(type="Brief", digest="sha256:" + "0" * 64)
OUTPUT = ArtifactRef(type="Design", digest="sha256:" + "2" * 64)


class Design(BaseModel):
    summary: str


AnyStore = MemoryStore | LocalStore | SQLiteStore


@pytest.fixture(params=["memory", "local", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> AnyStore:
    if request.param == "memory":
        return MemoryStore()
    if request.param == "local":
        return LocalStore(tmp_path)
    return SQLiteStore(tmp_path / "factory.db")


def a_run(
    run_id: str,
    *,
    outcome: RunOutcome = "passed",
    factory: str = "software",
    minutes: int = 0,
    revision: int = 0,
) -> Run:
    """A stored run last changed ``minutes`` after ``WHEN``."""
    updated_at = WHEN + timedelta(minutes=minutes)
    return Run(
        run_id=run_id,
        factory=factory,
        factory_version="1",
        factory_digest="sha256:" + "1" * 64,
        revision=revision,
        outcome=outcome,
        finished=outcome == "passed",
        stopped_at=None if outcome in ("passed", "running") else "design",
        input_ref=INPUT,
        output_ref=OUTPUT if outcome == "passed" else None,
        waiting=Waiting(kind="approval", station="design") if outcome == "waiting" else None,
        created_at=updated_at,
        updated_at=updated_at,
    )


async def put_all(store: AnyStore, *runs: Run) -> None:
    for run in runs:
        await store.put_run(run, replaces=None)


def ids(runs: list[Run]) -> list[str]:
    return [run.run_id for run in runs]


# Artifacts


async def test_an_artifact_comes_back_as_it_was_put(store: AnyStore) -> None:
    artifact = Artifact.create(Design(summary="Split the bill"), created_at=WHEN)

    ref = await store.put(artifact)

    assert ref == artifact.ref
    assert await store.exists(ref)
    assert await store.get(ref, Design) == artifact


async def test_a_store_refuses_an_artifact_that_does_not_match_its_digest(
    store: AnyStore,
) -> None:
    made = Artifact.create(Design(summary="Split the bill"), created_at=WHEN)
    forged = made.model_copy(update={"payload": Design(summary="swapped")})

    with pytest.raises(ArtifactCorrupt):
        await store.put(forged)
    assert not await store.exists(forged.ref)


async def test_an_artifact_asked_for_under_the_wrong_ref_is_caught(
    store: AnyStore,
) -> None:
    ref = await store.put(Artifact.create(Design(summary="x"), created_at=WHEN))

    with pytest.raises(ArtifactCorrupt):
        await store.get(ArtifactRef(type="Brief", digest=ref.digest), Design)


# Runs


async def test_a_run_comes_back_as_it_was_put(store: AnyStore) -> None:
    run = a_run("run-1", outcome="waiting")

    await store.put_run(run, replaces=None)

    assert await store.get_run("run-1") == run


async def test_a_write_from_a_stale_read_is_refused(store: AnyStore) -> None:
    first = a_run("run-1", outcome="waiting")
    await store.put_run(first, replaces=None)
    second = a_run("run-1", outcome="passed", minutes=1, revision=1)
    await store.put_run(second, replaces=first)

    stale = a_run("run-1", outcome="failed", minutes=2, revision=1)
    with pytest.raises(RunConflict, match="moved on"):
        await store.put_run(stale, replaces=first)
    assert await store.get_run("run-1") == second


async def test_a_reused_run_id_is_refused(store: AnyStore) -> None:
    first = a_run("run-1")
    await store.put_run(first, replaces=None)

    with pytest.raises(RunConflict, match="used twice"):
        await store.put_run(a_run("run-1", outcome="failed"), replaces=None)
    assert await store.get_run("run-1") == first


# Listing runs


async def test_an_empty_store_lists_no_runs(store: AnyStore) -> None:
    assert await store.list_runs() == []


async def test_runs_are_listed_newest_first_by_default(store: AnyStore) -> None:
    await put_all(store, a_run("b", minutes=2), a_run("a", minutes=1), a_run("c", minutes=3))

    listed = await store.list_runs()

    assert ids(listed) == ["c", "b", "a"]
    assert listed[0] == await store.get_run("c")


async def test_runs_are_listed_oldest_first_when_asked(store: AnyStore) -> None:
    await put_all(store, a_run("b", minutes=2), a_run("a", minutes=1), a_run("c", minutes=3))

    assert ids(await store.list_runs(order="oldest")) == ["a", "b", "c"]


async def test_runs_changed_at_the_same_moment_are_listed_by_id(
    store: AnyStore,
) -> None:
    await put_all(store, a_run("b"), a_run("c"), a_run("a"))

    assert ids(await store.list_runs()) == ids(await store.list_runs(order="oldest"))


async def test_runs_can_be_listed_by_outcome(store: AnyStore) -> None:
    await put_all(
        store,
        a_run("waiting-1", outcome="waiting", minutes=1),
        a_run("passed", outcome="passed", minutes=2),
        a_run("waiting-2", outcome="waiting", minutes=3),
    )

    waiting = await store.list_runs(outcome="waiting", order="oldest")

    assert ids(waiting) == ["waiting-1", "waiting-2"]


async def test_runs_can_be_listed_by_factory(store: AnyStore) -> None:
    await put_all(
        store,
        a_run("s", factory="software", minutes=1),
        a_run("m", factory="marketing", minutes=2),
    )

    assert ids(await store.list_runs(factory="marketing")) == ["m"]
    assert await store.list_runs(factory="nobody") == []


async def test_both_filters_must_match_for_a_run_to_be_listed(
    store: AnyStore,
) -> None:
    await put_all(
        store,
        a_run("sw", factory="software", outcome="waiting", minutes=1),
        a_run("sp", factory="software", outcome="passed", minutes=2),
        a_run("mw", factory="marketing", outcome="waiting", minutes=3),
    )

    assert ids(await store.list_runs(outcome="waiting", factory="software")) == ["sw"]


async def test_a_listing_stops_at_the_limit(store: AnyStore) -> None:
    await put_all(store, *(a_run(f"run-{n}", minutes=n) for n in range(5)))

    assert ids(await store.list_runs(limit=2)) == ["run-4", "run-3"]
    assert ids(await store.list_runs(order="oldest", limit=2)) == ["run-0", "run-1"]


async def test_a_listing_shows_the_latest_save_of_each_run(
    store: AnyStore,
) -> None:
    first = a_run("run-1", outcome="waiting", minutes=1)
    await put_all(store, first, a_run("run-2", minutes=2))
    moved = a_run("run-1", outcome="passed", minutes=3, revision=1)
    await store.put_run(moved, replaces=first)

    assert await store.list_runs() == [moved, await store.get_run("run-2")]


@pytest.mark.parametrize("limit", [0, -1, 1001])
async def test_a_limit_outside_one_to_a_thousand_is_refused(store: AnyStore, limit: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 1000"):
        await store.list_runs(limit=limit)


async def test_a_limit_of_one_or_a_thousand_is_allowed(store: AnyStore) -> None:
    await put_all(store, a_run("a", minutes=1), a_run("b", minutes=2))

    assert ids(await store.list_runs(limit=1)) == ["b"]
    assert ids(await store.list_runs(limit=1000)) == ["b", "a"]


def test_runs_can_be_read_and_listed_from_code_that_is_not_async(
    store: AnyStore,
) -> None:
    run_sync(put_all(store, a_run("a", outcome="waiting", minutes=1), a_run("b", minutes=2)))

    assert store.get_run_sync("a") == a_run("a", outcome="waiting", minutes=1)
    assert ids(store.list_runs_sync(outcome="waiting", order="oldest", limit=10)) == ["a"]


# SQLiteStore


# Each process loads the run, says it is ready, waits for the go file, then saves.
SAVE_IN_ANOTHER_PROCESS = """
import asyncio
import sys
import time
from pathlib import Path

from pydantic_ai_factory import RunConflict, SQLiteStore


async def main() -> None:
    store = SQLiteStore(sys.argv[1])
    first = await store.get_run("run-1")
    print("ready", flush=True)
    go = Path(sys.argv[2])
    while not go.exists():
        time.sleep(0.001)
    moved = first.model_copy(update={"revision": 1})
    try:
        await store.put_run(moved, replaces=first)
    except RunConflict as error:
        print(type(error).__name__, flush=True)
    else:
        print("saved", flush=True)


asyncio.run(main())
"""


async def test_two_processes_saving_the_same_revision_make_exactly_one_conflict(
    tmp_path: Path,
) -> None:
    path = tmp_path / "factory.db"
    await SQLiteStore(path).put_run(a_run("run-1", outcome="waiting"), replaces=None)
    go = tmp_path / "go"
    savers = [
        subprocess.Popen(
            [sys.executable, "-c", SAVE_IN_ANOTHER_PROCESS, str(path), str(go)],
            stdout=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    for saver in savers:
        assert saver.stdout is not None
        assert saver.stdout.readline().strip() == "ready"

    go.touch()
    said = sorted(saver.communicate(timeout=60)[0].strip() for saver in savers)

    assert said == ["RunConflict", "saved"]
    assert (await SQLiteStore(path).get_run("run-1")).revision == 1


async def test_a_save_that_cannot_get_the_write_lock_in_time_raises_store_busy(
    tmp_path: Path,
) -> None:
    path = tmp_path / "factory.db"
    first = a_run("run-1", outcome="waiting")
    await SQLiteStore(path).put_run(first, replaces=None)
    impatient = SQLiteStore(path, busy_timeout=0.05)

    with closing(sqlite3.connect(path, isolation_level=None)) as holder:
        holder.execute("BEGIN IMMEDIATE")
        try:
            with pytest.raises(StoreBusy, match="busy"):
                await impatient.put_run(a_run("run-1", revision=1), replaces=first)
        finally:
            holder.execute("ROLLBACK")

    assert await impatient.get_run("run-1") == first


def test_store_busy_is_a_run_conflict() -> None:
    assert issubclass(StoreBusy, RunConflict)


async def test_a_file_with_another_schema_version_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "factory.db"
    await SQLiteStore(path).put_run(a_run("run-1"), replaces=None)
    with closing(sqlite3.connect(path)) as other_version:
        other_version.execute("PRAGMA user_version = 2")

    with pytest.raises(StoreSchemaMismatch, match=r"version 2.*version 1"):
        await SQLiteStore(path).get_run("run-1")


async def test_a_file_with_the_stores_tables_but_no_schema_version_is_refused(
    tmp_path: Path,
) -> None:
    path = tmp_path / "factory.db"
    with closing(sqlite3.connect(path)) as older:
        older.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY, data BLOB)")

    with pytest.raises(StoreSchemaMismatch, match="runs but no schema version"):
        await SQLiteStore(path).list_runs()


async def test_a_sqlite_store_shares_its_file_with_another_programs_tables(
    tmp_path: Path,
) -> None:
    path = tmp_path / "factory.db"
    with closing(sqlite3.connect(path)) as app, app:
        app.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY)")
        app.execute("INSERT INTO jobs (id) VALUES ('job-1')")

    await SQLiteStore(path).put_run(a_run("run-1"), replaces=None)

    assert ids(await SQLiteStore(path).list_runs()) == ["run-1"]
    with closing(sqlite3.connect(path)) as app:
        assert app.execute("SELECT id FROM jobs").fetchall() == [("job-1",)]


async def test_a_sqlite_store_keeps_its_work_in_the_file_across_instances(
    tmp_path: Path,
) -> None:
    path = tmp_path / "factory.db"
    artifact = Artifact.create(Design(summary="Split the bill"), created_at=WHEN)
    await SQLiteStore(path).put(artifact)
    await SQLiteStore(path).put_run(a_run("run-1"), replaces=None)

    reopened = SQLiteStore(str(path))

    assert await reopened.get(artifact.ref, Design) == artifact
    assert ids(await reopened.list_runs()) == ["run-1"]
