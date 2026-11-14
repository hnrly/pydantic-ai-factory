"""Where artifacts are kept. A store is write-once: nothing in it is ever changed."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import tempfile
from pathlib import Path
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


class MemoryStore:
    """Keeps artifacts in memory. Use it in tests and short-lived runs."""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    async def put(self, artifact: Artifact[Any]) -> ArtifactRef:
        self._data.setdefault(artifact.ref.digest, encode_artifact(artifact))
        return artifact.ref

    async def get(self, ref: ArtifactRef, payload_type: type[PayloadT]) -> Artifact[PayloadT]:
        try:
            data = self._data[ref.digest]
        except KeyError:
            raise ArtifactNotFound(f"no artifact is filed under {ref}") from None
        return decode_artifact(ref, data, payload_type)

    async def exists(self, ref: ArtifactRef) -> bool:
        return ref.digest in self._data

    def __len__(self) -> int:
        return len(self._data)


class LocalStore:
    """Keeps each artifact as one JSON file under ``root``.

    Files are named by digest, so a path can never be chosen by a payload. Each file
    is written to a temporary name and then moved into place, so a crash cannot leave
    half an artifact behind. It uses no platform-specific calls.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def path_for(self, ref: ArtifactRef) -> Path:
        """Return where the artifact for ``ref`` is, or would be, kept."""
        hexdigest = ref.digest.removeprefix("sha256:")
        return self.root / "artifacts" / hexdigest[:2] / f"{hexdigest}.json"

    async def put(self, artifact: Artifact[Any]) -> ArtifactRef:
        data = encode_artifact(artifact)
        await asyncio.to_thread(self._write, self.path_for(artifact.ref), data)
        return artifact.ref

    async def get(self, ref: ArtifactRef, payload_type: type[PayloadT]) -> Artifact[PayloadT]:
        try:
            data = await asyncio.to_thread(self.path_for(ref).read_bytes)
        except FileNotFoundError:
            raise ArtifactNotFound(f"no artifact is filed under {ref}") from None
        return decode_artifact(ref, data, payload_type)

    async def exists(self, ref: ArtifactRef) -> bool:
        return await asyncio.to_thread(self.path_for(ref).is_file)

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        # A file that already holds exactly these bytes is left alone. One that holds
        # anything else was damaged, and is replaced with the checked content.
        with contextlib.suppress(OSError):
            if path.read_bytes() == data:
                return
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".incoming-")
        try:
            with os.fdopen(handle, "wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            # mkstemp makes the file private; give it the usual permissions for a new file.
            umask = os.umask(0)
            os.umask(umask)
            os.chmod(temporary, 0o666 & ~umask)
            try:
                os.replace(temporary, path)
            except PermissionError:
                # On Windows the move fails while another writer holds the same file open.
                # That writer is storing the same bytes, so its file is as good as ours.
                if not path.is_file():
                    raise
                Path(temporary).unlink(missing_ok=True)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
