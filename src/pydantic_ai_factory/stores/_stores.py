"""The two stores that ship: one in memory, one in a folder."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import tempfile
import time
from pathlib import Path
from typing import Any, TypeVar

from ..artifacts import Artifact, ArtifactNotFound, ArtifactRef, decode_artifact, encode_artifact
from ..contracts import run_sync
from ..factory import Run, RunConflict, RunNotFound

PayloadT = TypeVar("PayloadT")


def encode_run(run: Run) -> bytes:
    """Return the bytes to keep for ``run``."""
    return run.model_dump_json().encode("utf-8")


def decode_run(data: bytes) -> Run:
    """Read a run back from the bytes :func:`encode_run` made. Payloads stay plain JSON."""
    return Run.model_validate_json(data)


def _check_swap(run: Run, replaces: Run | None, current: Run | None) -> None:
    """Raise :class:`RunConflict` unless ``run`` may replace ``current``."""
    if replaces is not None and replaces.run_id != run.run_id:
        raise RunConflict(f"run {run.run_id!r} cannot replace run {replaces.run_id!r}")
    if current is None and replaces is None:
        return
    if current is None:
        raise RunConflict(f"run {run.run_id!r} is not stored, so there is nothing to replace")
    if replaces is None:
        raise RunConflict(f"run {run.run_id!r} is already stored; its id was used twice")
    if current.revision != replaces.revision:
        raise RunConflict(
            f"run {run.run_id!r} has moved on since it was read; load it again with get_run"
        )


class _SyncReads:
    """``get_sync`` and ``get_run_sync``, for code that is not async."""

    async def get(self, ref: ArtifactRef, payload_type: Any) -> Artifact[Any]: ...

    async def get_run(self, run_id: str) -> Run: ...

    def get_sync(self, ref: ArtifactRef, payload_type: type[PayloadT]) -> Artifact[PayloadT]:
        """Read an artifact back, from code that is not async."""
        return run_sync(self.get(ref, payload_type))

    def get_run_sync(self, run_id: str) -> Run:
        """Read a run back, from code that is not async."""
        return run_sync(self.get_run(run_id))


class MemoryStore(_SyncReads):
    """Keeps artifacts and runs in memory. Use it in tests and short-lived scripts."""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}
        self._runs: dict[str, bytes] = {}

    async def put(self, artifact: Artifact[Any]) -> ArtifactRef:
        self._data.setdefault(artifact.ref.digest, encode_artifact(artifact))
        return artifact.ref

    async def get(self, ref: ArtifactRef, payload_type: Any) -> Artifact[Any]:
        try:
            data = self._data[ref.digest]
        except KeyError:
            raise ArtifactNotFound(f"no artifact is filed under {ref}") from None
        return decode_artifact(ref, data, payload_type)

    async def exists(self, ref: ArtifactRef) -> bool:
        return ref.digest in self._data

    async def get_run(self, run_id: str) -> Run:
        try:
            return decode_run(self._runs[run_id])
        except KeyError:
            raise RunNotFound(f"no run is stored under {run_id!r}") from None

    async def put_run(self, run: Run, *, replaces: Run | None) -> None:
        stored = self._runs.get(run.run_id)
        _check_swap(run, replaces, decode_run(stored) if stored is not None else None)
        self._runs[run.run_id] = encode_run(run)

    def __len__(self) -> int:
        return len(self._data)


class LocalStore(_SyncReads):
    """Keeps each artifact and each run as one JSON file under ``root``.

    Artifact files are named by digest and runs by a digest of their id, so a path can
    never be chosen by a payload. Each file is written to a temporary name and then
    moved into place, so a crash cannot leave half a file behind. Saving a run takes a
    lock file for the swap, so two processes cannot both replace the same run.
    """

    lock_timeout = 5.0

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def path_for(self, ref: ArtifactRef) -> Path:
        """Return where the artifact for ``ref`` is, or would be, kept."""
        hexdigest = ref.digest.removeprefix("sha256:")
        return self.root / "artifacts" / hexdigest[:2] / f"{hexdigest}.json"

    def run_path_for(self, run_id: str) -> Path:
        """Return where the run ``run_id`` is, or would be, kept."""
        return self.root / "runs" / f"{hashlib.sha256(run_id.encode()).hexdigest()}.json"

    async def put(self, artifact: Artifact[Any]) -> ArtifactRef:
        data = encode_artifact(artifact)
        await asyncio.to_thread(self._write, self.path_for(artifact.ref), data)
        return artifact.ref

    async def get(self, ref: ArtifactRef, payload_type: Any) -> Artifact[Any]:
        try:
            data = await asyncio.to_thread(self.path_for(ref).read_bytes)
        except FileNotFoundError:
            raise ArtifactNotFound(f"no artifact is filed under {ref}") from None
        return decode_artifact(ref, data, payload_type)

    async def exists(self, ref: ArtifactRef) -> bool:
        return await asyncio.to_thread(self.path_for(ref).is_file)

    async def get_run(self, run_id: str) -> Run:
        try:
            data = await asyncio.to_thread(self.run_path_for(run_id).read_bytes)
        except FileNotFoundError:
            raise RunNotFound(f"no run is stored under {run_id!r}") from None
        return decode_run(data)

    async def put_run(self, run: Run, *, replaces: Run | None) -> None:
        await asyncio.to_thread(self._swap_run, run, replaces)

    def _swap_run(self, run: Run, replaces: Run | None) -> None:
        path = self.run_path_for(run.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = path.with_suffix(".lock")
        deadline = time.monotonic() + self.lock_timeout
        while True:
            try:
                handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                if time.monotonic() > deadline:
                    raise RunConflict(
                        f"run {run.run_id!r} is being saved by someone else; try again"
                    ) from None
                time.sleep(0.01)
        try:
            os.close(handle)
            try:
                current: Run | None = decode_run(path.read_bytes())
            except FileNotFoundError:
                current = None
            _check_swap(run, replaces, current)
            self._write(path, encode_run(run))
        finally:
            lock.unlink(missing_ok=True)

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        # A file that already holds exactly these bytes is left alone. One that holds
        # anything else was damaged, or is a run being replaced, and is rewritten.
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
