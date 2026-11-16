"""Where artifacts are kept. A store is write-once: nothing in it is ever changed."""

from __future__ import annotations

import json
from typing import Any, Protocol, TypeVar, cast

from ..contracts import FactoryError, canonical_json
from ._artifact import Artifact, ArtifactCorrupt, ArtifactRef, verify_body

PayloadT = TypeVar("PayloadT")


class ArtifactNotFound(FactoryError):
    """No artifact is filed under the ref that was asked for."""


class ArtifactStore(Protocol):
    """Keeps artifacts by their digest.

    ``put`` is idempotent: putting the same artifact twice is harmless. A store checks
    every artifact against its digest on the way in and on the way out. Build one from
    :func:`encode_artifact` and :func:`decode_artifact`, which do those checks.
    """

    async def put(self, artifact: Artifact[Any]) -> ArtifactRef:
        """File an artifact and return its ref."""
        ...

    async def get(self, ref: ArtifactRef, payload_type: type[PayloadT]) -> Artifact[PayloadT]:
        """Read an artifact back, with its payload as ``payload_type``."""
        ...

    async def exists(self, ref: ArtifactRef) -> bool:
        """Say whether an artifact is filed under ``ref``."""
        ...


def encode_artifact(artifact: Artifact[Any]) -> bytes:
    """Check an artifact against its digest and return the bytes to keep."""
    artifact.verify()
    return canonical_json({"ref": artifact.ref.model_dump(mode="json"), **artifact.body()})


def decode_artifact(
    ref: ArtifactRef, data: bytes, payload_type: type[PayloadT]
) -> Artifact[PayloadT]:
    """Check stored bytes against ``ref`` and return the artifact they hold.

    Raises :class:`ArtifactCorrupt` if the bytes cannot be read, are filed under another
    ref or do not match the digest. Raises ``pydantic.ValidationError`` if the stored
    payload does not fit ``payload_type``.
    """
    try:
        stored: object = json.loads(data)
        if not isinstance(stored, dict) or "ref" not in stored:
            raise TypeError("the content is not an artifact record")
        record = cast("dict[str, Any]", stored)
        body = {key: value for key, value in record.items() if key != "ref"}
        stored_ref = ArtifactRef.model_validate(record["ref"])
    except (ValueError, TypeError, RecursionError) as error:
        raise ArtifactCorrupt(f"artifact {ref} cannot be read: {error}") from error
    if stored_ref != ref:
        raise ArtifactCorrupt(f"artifact {ref} is filed under the wrong ref: {stored_ref}")
    verify_body(ref, body)
    return Artifact.from_stored(ref, body, payload_type)
