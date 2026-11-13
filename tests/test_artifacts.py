"""Artifacts are unchangeable, content-addressed and checked against their digest."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from pydantic_ai_factory import (
    Artifact,
    ArtifactCorrupt,
    ArtifactNotFound,
    ArtifactRef,
    ArtifactStore,
    LocalStore,
    MemoryStore,
    MethodologyRef,
    Producer,
)
from pydantic_ai_factory.contracts import canonical_json, digest_of

pytestmark = pytest.mark.anyio

WHEN = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


class Brief(BaseModel):
    goal: str


class Design(BaseModel):
    summary: str
    steps: list[str] = []


def design(summary: str = "Split the bill evenly") -> Artifact[Design]:
    return Artifact.create(Design(summary=summary, steps=["add", "divide"]), created_at=WHEN)


@pytest.fixture(params=["memory", "local"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> ArtifactStore:
    return MemoryStore() if request.param == "memory" else LocalStore(tmp_path)


def test_canonical_json_is_the_same_whatever_the_key_order() -> None:
    assert canonical_json({"b": 1, "a": [2, {"d": 3, "c": "é"}]}) == canonical_json(
        {"a": [2, {"c": "é", "d": 3}], "b": 1}
    )
    assert canonical_json({"a": "é"}) == b'{"a":"\xc3\xa9"}'


def test_canonical_json_refuses_numbers_that_are_not_json() -> None:
    with pytest.raises(ValueError, match="Out of range float"):
        digest_of({"x": float("nan")})


def test_the_same_content_always_gives_the_same_digest() -> None:
    assert design().ref == design().ref
    assert design().ref.digest.startswith("sha256:")
    assert len(design().ref.digest) == 71


@pytest.mark.parametrize(
    "other",
    [
        Artifact.create(Design(summary="Something else"), created_at=WHEN),
        Artifact.create(Design(summary="Split the bill evenly"), created_at=WHEN),
        Artifact.create(
            Design(summary="Split the bill evenly", steps=["add", "divide"]),
            type="Proposal",
            created_at=WHEN,
        ),
        Artifact.create(
            Design(summary="Split the bill evenly", steps=["add", "divide"]),
            mission_id="m-1",
            created_at=WHEN,
        ),
        Artifact.create(
            Design(summary="Split the bill evenly", steps=["add", "divide"]),
            created_at=datetime(2027, 1, 1, tzinfo=UTC),
        ),
    ],
    ids=["payload", "payload-field", "type", "mission", "time"],
)
def test_any_change_to_the_record_changes_the_digest(other: Artifact[Design]) -> None:
    assert other.ref.digest != design().ref.digest


def test_the_type_defaults_to_the_payload_class_name() -> None:
    assert design().ref.type == "Design"
    assert str(design().ref) == f"Design@{design().ref.digest[7:19]}"


def test_an_artifact_records_how_it_was_made() -> None:
    brief = Artifact.create(Brief(goal="Split a bill"), created_at=WHEN)
    made = Artifact.create(
        Design(summary="Evenly"),
        produced_by=Producer(station="design", station_version="1", run_id="run-1", attempt=2),
        inputs=[brief.ref],
        mission_id="mission-1",
        methodologies=[MethodologyRef(name="elephant-goldfish", version="1")],
        created_at=WHEN,
    )

    assert made.inputs == (brief.ref,)
    assert made.produced_by == Producer(
        station="design", station_version="1", run_id="run-1", attempt=2
    )
    assert made.mission_id == "mission-1"
    assert [str(ref) for ref in made.methodologies] == ["elephant-goldfish@1"]
    assert made.created_at == WHEN


def test_an_artifact_cannot_be_edited() -> None:
    with pytest.raises(ValidationError, match="frozen"):
        design().payload = Design(summary="changed")  # type: ignore[misc]


def test_a_correction_is_a_new_artifact_that_names_the_old_one() -> None:
    first = Artifact.create(
        Design(summary="Evenly"), inputs=[design().ref], mission_id="m-1", created_at=WHEN
    )
    before = first.model_dump_json()

    second = first.supersede(Design(summary="Evenly, rounding to pennies"))

    assert first.model_dump_json() == before
    assert second.supersedes == first.ref
    assert second.ref != first.ref
    assert (second.ref.type, second.inputs, second.mission_id) == ("Design", first.inputs, "m-1")


def test_an_artifact_made_by_hand_with_the_wrong_digest_fails_its_check() -> None:
    honest = design()
    forged = honest.model_copy(update={"payload": Design(summary="swapped")})

    honest.verify()
    with pytest.raises(ArtifactCorrupt, match="does not match its digest"):
        forged.verify()


async def test_a_stored_artifact_reads_back_equal_and_typed(store: ArtifactStore) -> None:
    original = design()

    ref = await store.put(original)
    loaded = await store.get(ref, Design)

    assert ref == original.ref
    assert await store.exists(ref)
    assert isinstance(loaded.payload, Design)
    assert loaded.model_dump() == original.model_dump()


async def test_putting_the_same_artifact_twice_is_harmless(store: ArtifactStore) -> None:
    assert await store.put(design()) == await store.put(design())


async def test_a_missing_artifact_is_reported_not_invented(store: ArtifactStore) -> None:
    missing = design().ref

    assert not await store.exists(missing)
    with pytest.raises(ArtifactNotFound, match="no artifact is filed under Design@"):
        await store.get(missing, Design)


async def test_a_store_refuses_an_artifact_that_does_not_match_its_digest(
    store: ArtifactStore,
) -> None:
    forged = design().model_copy(update={"payload": Design(summary="swapped")})

    with pytest.raises(ArtifactCorrupt):
        await store.put(forged)
    assert not await store.exists(forged.ref)


async def test_a_payload_that_does_not_fit_the_asked_type_is_refused(
    store: ArtifactStore,
) -> None:
    ref = await store.put(design())

    with pytest.raises(ValidationError):
        await store.get(ref, Brief)


async def test_a_file_changed_on_disk_is_caught_on_the_way_out(tmp_path: Path) -> None:
    store = LocalStore(tmp_path)
    ref = await store.put(design())
    path = store.path_for(ref)
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["payload"]["summary"] = "tampered"
    path.write_text(json.dumps(stored), encoding="utf-8")

    with pytest.raises(ArtifactCorrupt, match="does not match its digest"):
        await store.get(ref, Design)


async def test_an_unreadable_file_is_corrupt_not_a_crash(tmp_path: Path) -> None:
    store = LocalStore(tmp_path)
    ref = await store.put(design())
    store.path_for(ref).write_text("not json", encoding="utf-8")

    with pytest.raises(ArtifactCorrupt, match="cannot be read"):
        await store.get(ref, Design)


async def test_a_file_filed_under_another_ref_is_caught(tmp_path: Path) -> None:
    store = LocalStore(tmp_path)
    first, second = design(), design("Something else")
    await store.put(first)
    await store.put(second)
    store.path_for(first.ref).write_bytes(store.path_for(second.ref).read_bytes())

    with pytest.raises(ArtifactCorrupt, match="wrong ref"):
        await store.get(first.ref, Design)


async def test_local_files_are_named_by_digest_and_leave_no_temporary_files(
    tmp_path: Path,
) -> None:
    store = LocalStore(tmp_path)
    ref = await store.put(design())
    hexdigest = ref.digest.removeprefix("sha256:")

    files = [
        path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file()
    ]

    assert files == [f"artifacts/{hexdigest[:2]}/{hexdigest}.json"]


def test_a_ref_must_carry_a_real_digest() -> None:
    with pytest.raises(ValidationError):
        ArtifactRef(type="Design", digest="sha256:nope")


def test_both_stores_satisfy_the_store_protocol(tmp_path: Path) -> None:
    assert isinstance(MemoryStore(), ArtifactStore)
    assert isinstance(LocalStore(tmp_path), ArtifactStore)
