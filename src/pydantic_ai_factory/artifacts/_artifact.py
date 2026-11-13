"""The artifact record: what one station hands to the next."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from pydantic import AwareDatetime, BaseModel, Field

from ..contracts import Digest, FactoryError, MethodologyRef, Name, Record, digest_of

PayloadT = TypeVar("PayloadT", bound=BaseModel)
NewPayloadT = TypeVar("NewPayloadT", bound=BaseModel)

SCHEMA_VERSION = 1


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


class Artifact(Record, Generic[PayloadT]):
    """An unchangeable record of what a station handed on.

    Make one with :meth:`create`, never by calling the class, so that ``ref`` is the
    digest of the rest of the record. To correct an artifact, call :meth:`supersede`:
    that makes a new artifact which names the one it replaces. Nothing is edited.
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

    @classmethod
    def create(
        cls,
        payload: NewPayloadT,
        *,
        type: str | None = None,
        produced_by: Producer | None = None,
        inputs: tuple[ArtifactRef, ...] | list[ArtifactRef] = (),
        supersedes: ArtifactRef | None = None,
        mission_id: str | None = None,
        methodologies: tuple[MethodologyRef, ...] | list[MethodologyRef] = (),
        created_at: datetime | None = None,
    ) -> Artifact[NewPayloadT]:
        """Record ``payload`` as an artifact and compute its digest.

        ``type`` names the kind of artifact and defaults to the payload's class name.
        ``inputs`` are the artifacts this one was made from.
        """
        artifact_type = type or payload.__class__.__name__
        draft = Artifact[Any].model_construct(
            ref=ArtifactRef.model_construct(type=artifact_type, digest=""),
            schema_version=SCHEMA_VERSION,
            payload=payload,
            produced_by=produced_by,
            inputs=tuple(inputs),
            supersedes=supersedes,
            mission_id=mission_id,
            methodologies=tuple(methodologies),
            created_at=created_at or datetime.now(UTC),
        )
        body = draft.model_dump(mode="json", exclude={"ref"}, warnings=False)
        ref = ArtifactRef(type=artifact_type, digest=body_digest(artifact_type, body))
        return Artifact[Any].model_validate({**body, "ref": ref, "payload": payload})

    def supersede(
        self, payload: PayloadT, *, produced_by: Producer | None = None
    ) -> Artifact[PayloadT]:
        """Make the corrected artifact that replaces this one.

        The new artifact keeps this one's type, inputs, mission and methodologies,
        and records this one's ref in ``supersedes``.
        """
        return Artifact.create(
            payload,
            type=self.ref.type,
            produced_by=produced_by or self.produced_by,
            inputs=self.inputs,
            supersedes=self.ref,
            mission_id=self.mission_id,
            methodologies=self.methodologies,
        )

    def body(self) -> dict[str, Any]:
        """Return everything the digest covers, as JSON-ready data."""
        return self.model_dump(mode="json", exclude={"ref"})

    def verify(self) -> None:
        """Raise :class:`ArtifactCorrupt` unless ``ref`` matches the record."""
        verify_body(self.ref, self.body())


def body_digest(artifact_type: str, body: dict[str, Any]) -> str:
    """Digest an artifact's type together with the rest of its record."""
    return digest_of({"type": artifact_type, "body": body})


def verify_body(ref: ArtifactRef, body: dict[str, Any]) -> None:
    """Raise :class:`ArtifactCorrupt` unless ``body`` hashes to ``ref.digest``."""
    actual = body_digest(ref.type, body)
    if actual != ref.digest:
        raise ArtifactCorrupt(
            f"artifact {ref} does not match its digest: expected {ref.digest}, found {actual}"
        )
