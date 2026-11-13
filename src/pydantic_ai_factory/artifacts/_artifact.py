"""The artifact record: what one station hands to the next."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, PrivateAttr, ValidationError

from ..contracts import Digest, FactoryError, MethodologyRef, Name, Record, digest_of

PayloadT = TypeVar("PayloadT", bound=BaseModel)
NewPayloadT = TypeVar("NewPayloadT", bound=BaseModel)

SCHEMA_VERSION = 1
_PLACEHOLDER = "sha256:" + "0" * 64


class ArtifactRef(Record):
    """A pointer to one artifact: its type and the digest of everything in it."""

    type: Name
    digest: Digest

    def __str__(self) -> str:
        return f"{self.type}@{self.digest[7:19]}"


class Producer(Record):
    """Which station run produced an artifact."""

    station: Name
    station_version: Name = "0"
    run_id: Name
    attempt: int = Field(default=1, ge=1)


class ArtifactCorrupt(FactoryError):
    """An artifact's content does not match the digest it is filed under."""


class ArtifactInvalid(FactoryError):
    """A payload or argument cannot be recorded as an artifact."""


class Untyped(BaseModel):
    """A payload read without its model, for code that handles any artifact.

    Pass it to a store's ``get`` to load an artifact whose payload class you do not
    have. The fields are in ``model_extra``.
    """

    model_config = ConfigDict(extra="allow")


class Artifact(Record, Generic[PayloadT]):
    """An unchangeable record of what a station handed on.

    Make one with :meth:`create`, never by calling the class, so that ``ref`` is the
    digest of the rest of the record. To correct an artifact, call :meth:`supersede`:
    that makes a new artifact which names the one it replaces. Nothing is edited.

    The digest is taken once, from the record as JSON. That JSON is what a store keeps
    and what :meth:`verify` checks. ``payload`` is a typed view of it: treat it as
    read-only, and prefer frozen payload models.
    """

    ref: ArtifactRef
    schema_version: int = SCHEMA_VERSION
    payload: PayloadT
    produced_by: Producer | None = None
    inputs: tuple[ArtifactRef, ...] = ()
    supersedes: ArtifactRef | None = None
    mission_id: Name | None = None
    methodologies: tuple[MethodologyRef, ...] = ()
    created_at: AwareDatetime

    # The JSON the digest was taken from. Set by create() and by a store when it loads.
    _body: dict[str, Any] | None = PrivateAttr(default=None)
    # True when a store loaded this artifact, possibly through a newer payload model.
    _loaded: bool = PrivateAttr(default=False)

    @classmethod
    def create(
        cls,
        payload: NewPayloadT,
        *,
        type: str | None = None,
        produced_by: Producer | None = None,
        inputs: Sequence[ArtifactRef] = (),
        supersedes: ArtifactRef | None = None,
        mission_id: str | None = None,
        methodologies: Sequence[MethodologyRef] = (),
        created_at: datetime | None = None,
    ) -> Artifact[NewPayloadT]:
        """Record ``payload`` as an artifact and compute its digest.

        ``type`` labels the kind of artifact and defaults to the payload's class name.
        ``inputs`` are the artifacts this one was made from. The payload is copied, so
        changing your object afterwards does not change the artifact.

        Raises :class:`ArtifactInvalid` if the payload is not a Pydantic model, or would
        not read back from JSON as the same data.
        """
        if not isinstance(payload, BaseModel):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ArtifactInvalid(
                f"an artifact payload must be a Pydantic model, not {payload.__class__.__name__}"
            )
        payload_json = _round_trip(payload)
        try:
            draft = Artifact[Any].model_validate(
                {
                    "ref": {
                        "type": payload.__class__.__name__ if type is None else type,
                        "digest": _PLACEHOLDER,
                    },
                    "payload": payload.model_copy(deep=True),
                    "produced_by": produced_by,
                    "inputs": tuple(inputs),
                    "supersedes": supersedes,
                    "mission_id": mission_id,
                    "methodologies": tuple(methodologies),
                    "created_at": (created_at or datetime.now(UTC)),
                }
            )
        except ValidationError as error:
            raise ArtifactInvalid(f"the artifact's details are not valid: {error}") from error

        body = draft.model_dump(mode="json", exclude={"ref", "payload"})
        body["created_at"] = draft.created_at.astimezone(UTC).isoformat()
        body["payload"] = payload_json
        try:
            digest = body_digest(draft.ref.type, body)
        except (ValueError, UnicodeError) as error:
            raise ArtifactInvalid(f"the artifact cannot be written as JSON: {error}") from error

        artifact = draft.model_copy(
            update={
                "ref": ArtifactRef(type=draft.ref.type, digest=digest),
                "created_at": draft.created_at.astimezone(UTC),
            }
        )
        artifact._body = body
        return artifact

    def supersede(
        self,
        payload: PayloadT,
        *,
        produced_by: Producer | None = None,
        created_at: datetime | None = None,
    ) -> Artifact[PayloadT]:
        """Make the corrected artifact that replaces this one.

        The new artifact keeps this one's type, inputs, mission and methodologies, and
        records this one's ref in ``supersedes``. It does not inherit the producer:
        say who made the correction with ``produced_by``, or leave it empty.
        """
        return Artifact.create(
            payload,
            type=self.ref.type,
            produced_by=produced_by,
            inputs=self.inputs,
            supersedes=self.ref,
            mission_id=self.mission_id,
            methodologies=self.methodologies,
            created_at=created_at,
        )

    @classmethod
    def from_stored(
        cls, ref: ArtifactRef, body: dict[str, Any], payload_type: type[NewPayloadT]
    ) -> Artifact[NewPayloadT]:
        """Build an artifact from a body that has already been checked against ``ref``.

        This is for stores. Use :func:`decode_artifact`, which does the check first.
        """
        artifact = Artifact[payload_type].model_validate_json(
            json.dumps({**body, "ref": ref.model_dump(mode="json")})
        )
        artifact._body = body
        artifact._loaded = True
        return artifact

    def body(self) -> dict[str, Any]:
        """Return the JSON-ready data that the digest covers."""
        if self._body is not None:
            return self._body
        return self.model_dump(mode="json", exclude={"ref"})

    def verify(self) -> None:
        """Raise :class:`ArtifactCorrupt` unless the record matches its digest.

        For an artifact that was not loaded from a store, this also checks that the
        payload object still holds what was recorded.
        """
        body = self.body()
        verify_body(self.ref, body)
        if not self._loaded and self.payload.model_dump(mode="json") != body["payload"]:
            raise ArtifactCorrupt(f"artifact {self.ref}: the payload was changed after it was made")

    def __eq__(self, other: object) -> bool:
        """Two artifacts are the same artifact when their refs are equal."""
        return isinstance(other, Artifact) and self.ref == other.ref

    def __hash__(self) -> int:
        return hash(self.ref)


def _round_trip(payload: BaseModel) -> dict[str, Any]:
    """Dump a payload to JSON-ready data, and prove it reads back as the same payload."""
    model = payload.__class__
    hint = (
        "Check for field aliases, excluded or computed fields, mixed-type dictionary keys, "
        "and fields typed as a base class that hold a subclass."
    )
    try:
        dumped: dict[str, Any] = payload.model_dump(mode="json")
        again = model.model_validate_json(json.dumps(dumped))
    except (ValidationError, ValueError, TypeError, UnicodeError) as error:
        raise ArtifactInvalid(
            f"a {model.__name__} payload does not read back from its own JSON: {error}. {hint}"
        ) from error
    if again != payload or again.model_dump(mode="json") != dumped:
        raise ArtifactInvalid(
            f"a {model.__name__} payload changes when it is written to JSON and read back. {hint}"
        )
    return dumped


def body_digest(artifact_type: str, body: dict[str, Any]) -> str:
    """Digest an artifact's type together with the rest of its record."""
    return digest_of({"type": artifact_type, "body": body})


def verify_body(ref: ArtifactRef, body: dict[str, Any]) -> None:
    """Raise :class:`ArtifactCorrupt` unless ``body`` hashes to ``ref.digest``."""
    try:
        actual = body_digest(ref.type, body)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ArtifactCorrupt(f"artifact {ref} cannot be checked: {error}") from error
    if actual != ref.digest:
        raise ArtifactCorrupt(
            f"artifact {ref} does not match its digest: expected {ref.digest}, found {actual}"
        )
