"""Every store that ships keeps the same rules.

The same tests run against each store through one parametrised fixture. A store written
outside this library should keep these rules too; the Stores page lists them.
"""

from __future__ import annotations

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


@pytest.fixture(params=["memory", "local"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> MemoryStore | LocalStore:
    return MemoryStore() if request.param == "memory" else LocalStore(tmp_path)


def a_run(
    run_id: str,
    *,
    outcome: RunOutcome = "passed",
    factory: str = "software",
    minutes: int | None = 0,
    revision: int = 0,
) -> Run:
    """A stored run last changed ``minutes`` after ``WHEN``, or never timed if ``None``."""
    updated_at = None if minutes is None else WHEN + timedelta(minutes=minutes)
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


async def put_all(store: MemoryStore | LocalStore, *runs: Run) -> None:
    for run in runs:
        await store.put_run(run, replaces=None)


def ids(runs: list[Run]) -> list[str]:
    return [run.run_id for run in runs]


# Artifacts


async def test_an_artifact_comes_back_as_it_was_put(store: MemoryStore | LocalStore) -> None:
    artifact = Artifact.create(Design(summary="Split the bill"), created_at=WHEN)

    ref = await store.put(artifact)

    assert ref == artifact.ref
    assert await store.exists(ref)
    assert await store.get(ref, Design) == artifact


async def test_a_store_refuses_an_artifact_that_does_not_match_its_digest(
    store: MemoryStore | LocalStore,
) -> None:
    made = Artifact.create(Design(summary="Split the bill"), created_at=WHEN)
    forged = made.model_copy(update={"payload": Design(summary="swapped")})

    with pytest.raises(ArtifactCorrupt):
        await store.put(forged)
    assert not await store.exists(forged.ref)


async def test_an_artifact_asked_for_under_the_wrong_ref_is_caught(
    store: MemoryStore | LocalStore,
) -> None:
    ref = await store.put(Artifact.create(Design(summary="x"), created_at=WHEN))

    with pytest.raises(ArtifactCorrupt):
        await store.get(ArtifactRef(type="Brief", digest=ref.digest), Design)


# Runs


async def test_a_run_comes_back_as_it_was_put(store: MemoryStore | LocalStore) -> None:
    run = a_run("run-1", outcome="waiting")

    await store.put_run(run, replaces=None)

    assert await store.get_run("run-1") == run


async def test_a_write_from_a_stale_read_is_refused(store: MemoryStore | LocalStore) -> None:
    first = a_run("run-1", outcome="waiting")
    await store.put_run(first, replaces=None)
    second = a_run("run-1", outcome="passed", minutes=1, revision=1)
    await store.put_run(second, replaces=first)

    stale = a_run("run-1", outcome="failed", minutes=2, revision=1)
    with pytest.raises(RunConflict, match="moved on"):
        await store.put_run(stale, replaces=first)
    assert await store.get_run("run-1") == second


async def test_a_reused_run_id_is_refused(store: MemoryStore | LocalStore) -> None:
    first = a_run("run-1")
    await store.put_run(first, replaces=None)

    with pytest.raises(RunConflict, match="used twice"):
        await store.put_run(a_run("run-1", outcome="failed"), replaces=None)
    assert await store.get_run("run-1") == first


# Listing runs


async def test_an_empty_store_lists_no_runs(store: MemoryStore | LocalStore) -> None:
    assert await store.list_runs() == []


async def test_runs_are_listed_newest_first_by_default(store: MemoryStore | LocalStore) -> None:
    await put_all(store, a_run("b", minutes=2), a_run("a", minutes=1), a_run("c", minutes=3))

    listed = await store.list_runs()

    assert ids(listed) == ["c", "b", "a"]
    assert listed[0] == await store.get_run("c")


async def test_runs_are_listed_oldest_first_when_asked(store: MemoryStore | LocalStore) -> None:
    await put_all(store, a_run("b", minutes=2), a_run("a", minutes=1), a_run("c", minutes=3))

    assert ids(await store.list_runs(order="oldest")) == ["a", "b", "c"]


async def test_runs_changed_at_the_same_moment_are_listed_by_id(
    store: MemoryStore | LocalStore,
) -> None:
    await put_all(store, a_run("b"), a_run("c"), a_run("a"))

    assert ids(await store.list_runs()) == ids(await store.list_runs(order="oldest"))


async def test_a_run_with_no_updated_at_is_listed_last_in_either_order(
    store: MemoryStore | LocalStore,
) -> None:
    await put_all(store, a_run("old", minutes=None), a_run("a", minutes=1), a_run("b", minutes=2))

    assert ids(await store.list_runs()) == ["b", "a", "old"]
    assert ids(await store.list_runs(order="oldest")) == ["a", "b", "old"]


async def test_runs_can_be_listed_by_outcome(store: MemoryStore | LocalStore) -> None:
    await put_all(
        store,
        a_run("waiting-1", outcome="waiting", minutes=1),
        a_run("passed", outcome="passed", minutes=2),
        a_run("waiting-2", outcome="waiting", minutes=3),
    )

    waiting = await store.list_runs(outcome="waiting", order="oldest")

    assert ids(waiting) == ["waiting-1", "waiting-2"]


async def test_runs_can_be_listed_by_factory(store: MemoryStore | LocalStore) -> None:
    await put_all(
        store,
        a_run("s", factory="software", minutes=1),
        a_run("m", factory="marketing", minutes=2),
    )

    assert ids(await store.list_runs(factory="marketing")) == ["m"]
    assert await store.list_runs(factory="nobody") == []


async def test_both_filters_must_match_for_a_run_to_be_listed(
    store: MemoryStore | LocalStore,
) -> None:
    await put_all(
        store,
        a_run("sw", factory="software", outcome="waiting", minutes=1),
        a_run("sp", factory="software", outcome="passed", minutes=2),
        a_run("mw", factory="marketing", outcome="waiting", minutes=3),
    )

    assert ids(await store.list_runs(outcome="waiting", factory="software")) == ["sw"]


async def test_a_listing_stops_at_the_limit(store: MemoryStore | LocalStore) -> None:
    await put_all(store, *(a_run(f"run-{n}", minutes=n) for n in range(5)))

    assert ids(await store.list_runs(limit=2)) == ["run-4", "run-3"]
    assert ids(await store.list_runs(order="oldest", limit=2)) == ["run-0", "run-1"]


async def test_a_listing_shows_the_latest_save_of_each_run(
    store: MemoryStore | LocalStore,
) -> None:
    first = a_run("run-1", outcome="waiting", minutes=1)
    await put_all(store, first, a_run("run-2", minutes=2))
    moved = a_run("run-1", outcome="passed", minutes=3, revision=1)
    await store.put_run(moved, replaces=first)

    assert await store.list_runs() == [moved, await store.get_run("run-2")]


@pytest.mark.parametrize("limit", [0, -1, 1001])
async def test_a_limit_outside_one_to_a_thousand_is_refused(
    store: MemoryStore | LocalStore, limit: int
) -> None:
    with pytest.raises(ValueError, match="between 1 and 1000"):
        await store.list_runs(limit=limit)


async def test_a_limit_of_one_or_a_thousand_is_allowed(store: MemoryStore | LocalStore) -> None:
    await put_all(store, a_run("a", minutes=1), a_run("b", minutes=2))

    assert ids(await store.list_runs(limit=1)) == ["b"]
    assert ids(await store.list_runs(limit=1000)) == ["b", "a"]


def test_runs_can_be_read_and_listed_from_code_that_is_not_async(
    store: MemoryStore | LocalStore,
) -> None:
    run_sync(put_all(store, a_run("a", outcome="waiting", minutes=1), a_run("b", minutes=2)))

    assert store.get_run_sync("a") == a_run("a", outcome="waiting", minutes=1)
    assert ids(store.list_runs_sync(outcome="waiting", order="oldest", limit=10)) == ["a"]
