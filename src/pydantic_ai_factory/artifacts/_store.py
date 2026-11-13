"""Where artifacts are kept. A store is write-once: nothing in it is ever changed."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from ..contracts import FactoryError, canonical_json
from ._artifact import Artifact, ArtifactCorrupt, ArtifactRef, verify_body

PayloadT = TypeVar("PayloadT", bound=BaseModel)


class ArtifactNotFound(FactoryError):
    """No artifact is filed under the ref that was asked for."""


@runtime_checkable
class ArtifactStore(Protocol):
    """Keeps artifacts by their digest.

    ``put`` is idempotent: putting the same artifact twice is harmless. Every store
    checks an artifact against its digest on the way in and on the way out.
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


def _encode(artifact: Artifact[Any]) -> bytes:
    body = artifact.body()
    verify_body(artifact.ref, body)
    return canonical_json({"ref": artifact.ref.model_dump(mode="json"), **body})


def _decode(ref: ArtifactRef, data: bytes, payload_type: type[PayloadT]) -> Artifact[PayloadT]:
    try:
        stored: dict[str, Any] = json.loads(data)
        stored_ref = ArtifactRef.model_validate(stored.pop("ref"))
    except (ValueError, KeyError, TypeError) as error:
        raise ArtifactCorrupt(f"artifact {ref} cannot be read: {error}") from error
    if stored_ref != ref:
        raise ArtifactCorrupt(f"artifact {ref} is filed under the wrong ref: {stored_ref}")
    verify_body(ref, stored)
    return Artifact[payload_type].model_validate({**stored, "ref": ref})


class MemoryStore:
    """Keeps artifacts in memory. Use it in tests and short-lived runs."""

    def __init__(self) -> None:
        self._data: dict[ArtifactRef, bytes] = {}

    async def put(self, artifact: Artifact[Any]) -> ArtifactRef:
        self._data.setdefault(artifact.ref, _encode(artifact))
        return artifact.ref

    async def get(self, ref: ArtifactRef, payload_type: type[PayloadT]) -> Artifact[PayloadT]:
        try:
            data = self._data[ref]
        except KeyError:
            raise ArtifactNotFound(f"no artifact is filed under {ref}") from None
        return _decode(ref, data, payload_type)

    async def exists(self, ref: ArtifactRef) -> bool:
        return ref in self._data

    def __len__(self) -> int:
        return len(self._data)


class LocalStore:
    """Keeps each artifact as one JSON file under ``root``.

    Files are named by digest, so a path can never be chosen by a payload. Each file
    is written to a temporary name and then moved into place, so a crash cannot leave
    half an artifact behind. Works the same on Linux, macOS and Windows.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def path_for(self, ref: ArtifactRef) -> Path:
        """Return where the artifact for ``ref`` is, or would be, kept."""
        hexdigest = ref.digest.removeprefix("sha256:")
        return self.root / "artifacts" / hexdigest[:2] / f"{hexdigest}.json"

    async def put(self, artifact: Artifact[Any]) -> ArtifactRef:
        await asyncio.to_thread(self._write, self.path_for(artifact.ref), _encode(artifact))
        return artifact.ref

    async def get(self, ref: ArtifactRef, payload_type: type[PayloadT]) -> Artifact[PayloadT]:
        try:
            data = await asyncio.to_thread(self.path_for(ref).read_bytes)
        except FileNotFoundError:
            raise ArtifactNotFound(f"no artifact is filed under {ref}") from None
        return _decode(ref, data, payload_type)

    async def exists(self, ref: ArtifactRef) -> bool:
        return await asyncio.to_thread(self.path_for(ref).is_file)

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        if path.is_file():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".incoming-")
        try:
            with os.fdopen(handle, "wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
