"""Artifacts are unchangeable, content-addressed and checked against their digest."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError, computed_field

from pydantic_ai_factory import (
    Artifact,
    ArtifactCorrupt,
    ArtifactInvalid,
    ArtifactNotFound,
    ArtifactRef,
    ArtifactStore,
    LocalStore,
    MemoryStore,
    MethodologyRef,
    Producer,
    Untyped,
)
from pydantic_ai_factory.artifacts import decode_artifact, encode_artifact
from pydantic_ai_factory.contracts import canonical_json, digest_of

pytestmark = pytest.mark.anyio

WHEN = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
PRODUCER = Producer(station="design", station_version="1", run_id="run-1", attempt=2)
STAMP = MethodologyRef(name="review", version="1")


class Brief(BaseModel):
    goal: str


class Design(BaseModel):
    summary: str
    steps: list[str] = []


def design(summary: str = "Split the bill evenly", **details: Any) -> Artifact[Design]:
    details.setdefault("created_at", WHEN)
    return Artifact.create(Design(summary=summary, steps=["add", "divide"]), **details)


BRIEF = Artifact.create(Brief(goal="Split a bill"), created_at=WHEN)


@pytest.fixture(params=["memory", "local"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> ArtifactStore:
    return MemoryStore() if request.param == "memory" else LocalStore(tmp_path)


# --- canonical form and digests


def test_canonical_json_is_the_same_whatever_the_key_order() -> None:
    assert canonical_json({"b": 1, "a": [2, {"d": 3, "c": "é"}]}) == canonical_json(
        {"a": [2, {"c": "é", "d": 3}], "b": 1}
    )
    assert canonical_json({"a": "é"}) == b'{"a":"\xc3\xa9"}'


def test_canonical_json_refuses_numbers_that_are_not_json() -> None:
    with pytest.raises(ValueError, match="Out of range float"):
        digest_of({"x": float("nan")})


def test_the_digest_of_a_known_artifact_never_changes() -> None:
    # If this digest changes, every stored artifact and every approval is invalidated.
    # Change it only on purpose, together with SCHEMA_VERSION.
    assert design().ref.digest == (
        "sha256:7831714553c35f4f7719268196d888bde03a8c3ad81c3564129e256204a38422"
    )
    assert design().body() == {
        "schema_version": 1,
        "payload": {"summary": "Split the bill evenly", "steps": ["add", "divide"]},
        "produced_by": None,
        "inputs": [],
        "supersedes": None,
        "mission_id": None,
        "methodologies": [],
        "created_at": "2026-01-02T03:04:05+00:00",
    }
    assert digest_of({"a": 1}) == (
        "sha256:015abd7f5cc57a2dd94b7590f04ad8084273905ee33ec5cebeae62276a97f862"
    )


def test_the_same_content_always_gives_the_same_digest() -> None:
    assert design().ref == design().ref
    assert len(design().ref.digest) == 71


@pytest.mark.parametrize(
    "other",
    [
        design("Something else"),
        design(type="Proposal"),
        design(produced_by=PRODUCER),
        design(inputs=[BRIEF.ref]),
        design(supersedes=BRIEF.ref),
        design(mission_id="m-1"),
        design(methodologies=[STAMP]),
        design(created_at=WHEN + timedelta(seconds=1)),
    ],
    ids=["payload", "type", "producer", "inputs", "supersedes", "mission", "methodology", "time"],
)
def test_any_change_to_the_record_changes_the_digest(other: Artifact[Design]) -> None:
    assert other.ref.digest != design().ref.digest


def test_the_same_instant_in_another_time_zone_gives_the_same_digest() -> None:
    elsewhere = WHEN.astimezone(timezone(timedelta(hours=2)))

    assert design(created_at=elsewhere).ref == design().ref
    assert design(created_at=elsewhere).created_at.utcoffset() == timedelta(0)


def test_the_type_defaults_to_the_payload_class_name() -> None:
    assert design().ref.type == "Design"
    assert str(design().ref) == f"Design@{design().ref.digest[7:19]}"


def test_an_artifact_records_how_it_was_made() -> None:
    made = design(
        produced_by=PRODUCER, inputs=[BRIEF.ref], mission_id="mission-1", methodologies=[STAMP]
    )

    assert made.inputs == (BRIEF.ref,)
    assert made.produced_by == PRODUCER
    assert made.mission_id == "mission-1"
    assert [str(ref) for ref in made.methodologies] == ["review@1"]
    assert made.created_at == WHEN


# --- an artifact does not change


def test_an_artifact_cannot_be_edited() -> None:
    with pytest.raises(ValidationError, match="frozen"):
        design().payload = Design(summary="changed")  # type: ignore[misc]


def test_changing_the_object_you_passed_in_does_not_change_the_artifact() -> None:
    mine = Design(summary="Split the bill evenly", steps=["add"])
    artifact = Artifact.create(mine, created_at=WHEN)

    mine.summary = "changed"
    mine.steps.append("more")

    assert artifact.payload == Design(summary="Split the bill evenly", steps=["add"])
    artifact.verify()


def test_changing_the_payload_in_place_is_caught() -> None:
    artifact = design()
    artifact.payload.steps.append("sneaky")

    with pytest.raises(ArtifactCorrupt, match="the payload was changed after it was made"):
        artifact.verify()


def test_a_copy_with_a_swapped_payload_is_caught() -> None:
    forged = design().model_copy(update={"payload": Design(summary="swapped")})

    with pytest.raises(ArtifactCorrupt):
        forged.verify()


def test_an_artifact_built_by_hand_with_the_wrong_digest_is_caught() -> None:
    by_hand = Artifact[Design](
        ref=ArtifactRef(type="Design", digest="sha256:" + "a" * 64),
        payload=Design(summary="x"),
        created_at=WHEN,
    )

    with pytest.raises(ArtifactCorrupt, match="does not match its digest"):
        by_hand.verify()


def test_an_artifact_can_be_used_in_a_set() -> None:
    assert len({design(), design(), design("Something else")}) == 2


def test_a_correction_is_a_new_artifact_that_names_the_old_one() -> None:
    first = design(
        produced_by=PRODUCER, inputs=[BRIEF.ref], mission_id="m-1", methodologies=[STAMP]
    )
    before = first.model_dump_json()

    second = first.supersede(Design(summary="Evenly, rounding to pennies"), created_at=WHEN)

    assert first.model_dump_json() == before
    assert second.supersedes == first.ref
    assert second.ref != first.ref
    assert (second.ref.type, second.inputs, second.mission_id, second.methodologies) == (
        "Design",
        (BRIEF.ref,),
        "m-1",
        (STAMP,),
    )
    assert second.produced_by is None
    assert second == first.supersede(Design(summary="Evenly, rounding to pennies"), created_at=WHEN)


# --- what create refuses


class NotSerialisable:
    """A plain class with no schema, which Pydantic cannot serialise."""


def test_a_payload_pydantic_cannot_serialise_is_refused() -> None:
    with pytest.raises(ArtifactInvalid, match="Pydantic cannot serialise a NotSerialisable"):
        Artifact.create(NotSerialisable())


async def test_a_str_payload_round_trips_through_a_store(store: ArtifactStore) -> None:
    artifact = Artifact.create("a plain note")
    await store.put(artifact)

    loaded = await store.get(artifact.ref, str)

    assert (artifact.ref.type, loaded.payload, loaded.ref) == ("str", "a plain note", artifact.ref)


@pytest.mark.parametrize(
    ("details", "message"),
    [
        ({"type": ""}, "ref.type"),
        ({"type": " padded "}, "ref.type"),
        ({"created_at": datetime(2026, 1, 1)}, "created_at"),
        ({"mission_id": ""}, "mission_id"),
    ],
    ids=["empty-type", "padded-type", "naive-time", "empty-mission"],
)
def test_invalid_details_are_refused_with_the_field_named(
    details: dict[str, Any], message: str
) -> None:
    with pytest.raises(ArtifactInvalid, match=message):
        Artifact.create(Design(summary="x"), **details)


class Aliased(BaseModel):
    user_name: str = Field(alias="userName")


class Hidden(BaseModel):
    shown: str
    secret: str = Field(default="default", exclude=True)


class Base(BaseModel):
    a: int


class Sub(Base):
    b: int


class Wrapper(BaseModel):
    inner: Base


class Loose(BaseModel):
    mapping: dict[Any, int]


class NotJson(BaseModel):
    value: float


@pytest.mark.parametrize(
    "payload",
    [
        Aliased(userName="ada"),
        Hidden(shown="x", secret="s"),
        Wrapper(inner=Sub(a=1, b=2)),
        Loose(mapping={1: 1, "1": 2}),
        NotJson(value=float("nan")),
    ],
    ids=["alias", "excluded-field", "subclass-in-base-field", "mixed-keys", "nan"],
)
def test_a_payload_that_would_not_read_back_the_same_is_refused_at_creation(
    payload: BaseModel,
) -> None:
    with pytest.raises(ArtifactInvalid):
        Artifact.create(payload)


class Colour(Enum):
    RED = "red"


class Rich(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    when: datetime
    ident: uuid.UUID
    amount: Decimal
    colour: Colour
    pair: tuple[int, str]
    nested: Design
    numbers: dict[int, str]

    @computed_field
    @property
    def label(self) -> str:
        return f"{self.colour.value}:{self.amount}"


RICH = Rich(
    when=WHEN,
    ident=uuid.UUID(int=7),
    amount=Decimal("1.10"),
    colour=Colour.RED,
    pair=(1, "a"),
    nested=Design(summary="n"),
    numbers={1: "one"},
)


async def test_a_strict_payload_with_rich_types_reads_back_equal(store: ArtifactStore) -> None:
    class Plain(BaseModel):
        model_config = ConfigDict(strict=True)

        when: datetime
        ident: uuid.UUID
        amount: Decimal
        colour: Colour
        pair: tuple[int, str]
        nested: Design
        numbers: dict[int, str]

    payload = Plain(**RICH.model_dump(exclude={"label"}))
    artifact = Artifact.create(payload)

    loaded = await store.get(await store.put(artifact), Plain)

    assert loaded.payload == payload
    loaded.verify()


def test_a_computed_field_on_a_model_that_forbids_extras_is_refused() -> None:
    class Forbidding(BaseModel):
        model_config = ConfigDict(extra="forbid")

        a: int

        @computed_field
        @property
        def doubled(self) -> int:
            return self.a * 2

    with pytest.raises(ArtifactInvalid, match="does not read back from its own JSON"):
        Artifact.create(Forbidding(a=1))


# --- stores


async def test_a_stored_artifact_reads_back_equal_and_typed(store: ArtifactStore) -> None:
    original = design(produced_by=PRODUCER, inputs=[BRIEF.ref], methodologies=[STAMP])

    ref = await store.put(original)
    loaded = await store.get(ref, Design)

    assert ref == original.ref
    assert await store.exists(ref)
    assert isinstance(loaded.payload, Design)
    assert loaded == original
    loaded.verify()


async def test_putting_the_same_artifact_twice_is_harmless(store: ArtifactStore) -> None:
    assert await store.put(design()) == await store.put(design())


async def test_a_loaded_artifact_can_be_put_in_another_store(store: ArtifactStore) -> None:
    loaded = await store.get(await store.put(design()), Design)
    second = MemoryStore()

    assert await second.put(loaded) == design().ref
    assert (await second.get(design().ref, Design)).payload == design().payload


async def test_a_missing_artifact_is_reported_not_invented(store: ArtifactStore) -> None:
    missing = design().ref

    assert not await store.exists(missing)
    with pytest.raises(ArtifactNotFound, match="no artifact is filed under Design@"):
        await store.get(missing, Design)


async def test_a_ref_with_the_right_digest_and_the_wrong_type_is_caught(
    store: ArtifactStore,
) -> None:
    ref = await store.put(design())
    mislabelled = ArtifactRef(type="Brief", digest=ref.digest)

    with pytest.raises(ArtifactCorrupt, match="filed under the wrong ref"):
        await store.get(mislabelled, Design)


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


async def test_an_artifact_can_be_read_without_its_payload_class(store: ArtifactStore) -> None:
    ref = await store.put(design(inputs=[BRIEF.ref]))

    loaded = await store.get(ref, Untyped)

    assert loaded.payload.model_extra == {
        "summary": "Split the bill evenly",
        "steps": ["add", "divide"],
    }
    assert loaded.inputs == (BRIEF.ref,)
    loaded.verify()


# --- a payload model that changes after an artifact was stored


class DesignV2(BaseModel):
    summary: str
    steps: list[str] = []
    owner: str = "nobody"


class DesignSlim(BaseModel):
    summary: str


@pytest.mark.parametrize("newer", [DesignV2, DesignSlim], ids=["added-field", "removed-field"])
async def test_an_old_artifact_survives_a_changed_payload_model(
    store: ArtifactStore, newer: type[BaseModel]
) -> None:
    ref = await store.put(design())

    loaded = await store.get(ref, newer)

    assert loaded.ref == ref
    loaded.verify()
    assert loaded.body() == design().body()
    assert await MemoryStore().put(loaded) == ref


# --- damage on disk


def _stored(store: LocalStore, ref: ArtifactRef) -> dict[str, Any]:
    return json.loads(store.path_for(ref).read_text(encoding="utf-8"))


async def test_a_file_changed_on_disk_is_caught_on_the_way_out(tmp_path: Path) -> None:
    store = LocalStore(tmp_path)
    ref = await store.put(design())
    stored = _stored(store, ref)
    stored["payload"]["summary"] = "tampered"
    store.path_for(ref).write_text(json.dumps(stored), encoding="utf-8")

    with pytest.raises(ArtifactCorrupt, match="does not match its digest"):
        await store.get(ref, Design)


@pytest.mark.parametrize(
    "content",
    ["not json", '"abc"', "1", "null", "[]", "{}", '{"ref": 1}', '{"ref": {}, "x": NaN}'],
)
async def test_an_unreadable_file_is_corrupt_not_a_crash(tmp_path: Path, content: str) -> None:
    store = LocalStore(tmp_path)
    ref = await store.put(design())
    store.path_for(ref).write_text(content, encoding="utf-8")

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


async def test_putting_a_good_artifact_repairs_a_damaged_file(tmp_path: Path) -> None:
    store = LocalStore(tmp_path)
    ref = await store.put(design())
    store.path_for(ref).write_text("", encoding="utf-8")

    await store.put(design())

    assert (await store.get(ref, Design)).payload == design().payload


async def test_local_files_are_named_by_digest_and_leave_no_temporary_files(
    tmp_path: Path,
) -> None:
    store = LocalStore(tmp_path)
    ref = await store.put(design())
    await store.put(design())
    hexdigest = ref.digest.removeprefix("sha256:")

    files = [p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()]

    assert files == [f"artifacts/{hexdigest[:2]}/{hexdigest}.json"]


# --- building a store


def test_encode_and_decode_are_the_two_halves_a_store_needs() -> None:
    artifact = design()

    data = encode_artifact(artifact)

    assert decode_artifact(artifact.ref, data, Design) == artifact
    with pytest.raises(ArtifactCorrupt):
        decode_artifact(artifact.ref, data.replace(b"evenly", b"oddly!"), Design)


def test_a_ref_must_carry_a_real_digest() -> None:
    with pytest.raises(ValidationError):
        ArtifactRef(type="Design", digest="sha256:nope")
