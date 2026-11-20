"""The three stores that ship: one in memory, one in a folder, one in a SQLite file."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import sqlite3
import tempfile
import time
from collections.abc import Generator, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import ValidationError

from ..artifacts import Artifact, ArtifactNotFound, ArtifactRef, decode_artifact, encode_artifact
from ..contracts import FactoryError, run_sync
from ..factory import Run, RunConflict, RunNotFound, RunOutcome

PayloadT = TypeVar("PayloadT")


class StoreBusy(RunConflict):
    """The store was busy, so nothing was saved. Try again.

    Another writer held the store for longer than its ``busy_timeout``. Nobody else
    has answered the run, as far as this save could tell. It is a ``RunConflict``, so
    code that catches ``RunConflict`` still works.
    """


class StoreSchemaMismatch(FactoryError):
    """The store holds data in a shape this version of the library does not read.

    Another version of the library wrote it. Before 1.0 the library does not convert
    old data: start a new store, or read this one with the version that wrote it.
    """


def encode_run(run: Run) -> bytes:
    """Return the bytes to keep for ``run``."""
    return run.model_dump_json().encode("utf-8")


def decode_run(data: bytes) -> Run:
    """Read a run back from the bytes :func:`encode_run` made. Payloads stay plain JSON.

    Bytes that do not read as a run of this version raise :class:`StoreSchemaMismatch`.
    """
    try:
        return Run.model_validate_json(data)
    except ValidationError as error:
        raise StoreSchemaMismatch(
            "a stored run does not read as a run of this version of the library; "
            f"another version may have saved it: {error}"
        ) from error


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


_LIMIT_MAX = 1000


def _check_limit(limit: int, order: str) -> None:
    """Raise ``ValueError`` unless ``limit`` and ``order`` are ones ``list_runs`` takes."""
    if not 1 <= limit <= _LIMIT_MAX:
        raise ValueError(f"limit must be between 1 and {_LIMIT_MAX}, not {limit}")
    if order not in ("newest", "oldest"):
        raise ValueError(f"order must be 'newest' or 'oldest', not {order!r}")


def _select_runs(
    runs: Iterable[Run],
    *,
    outcome: RunOutcome | None,
    factory: str | None,
    order: Literal["newest", "oldest"],
    limit: int,
) -> list[Run]:
    """Filter, sort and cut ``runs`` as ``RunStore.list_runs`` describes."""
    _check_limit(limit, order)
    chosen = [
        run
        for run in runs
        if (outcome is None or run.outcome == outcome)
        and (factory is None or run.factory == factory)
    ]
    # Runs changed at the same moment keep run_id order, so a listing never shuffles.
    chosen.sort(key=lambda run: run.run_id)
    chosen.sort(key=lambda run: run.updated_at, reverse=order == "newest")
    return chosen[:limit]


class _SyncReads:
    """``get_sync``, ``get_run_sync`` and ``list_runs_sync``, for code that is not async."""

    async def get(self, ref: ArtifactRef, payload_type: Any) -> Artifact[Any]: ...

    async def get_run(self, run_id: str) -> Run: ...

    async def list_runs(
        self,
        *,
        outcome: RunOutcome | None = None,
        factory: str | None = None,
        order: Literal["newest", "oldest"] = "newest",
        limit: int = 50,
    ) -> list[Run]: ...

    def get_sync(self, ref: ArtifactRef, payload_type: type[PayloadT]) -> Artifact[PayloadT]:
        """Read an artifact back, from code that is not async."""
        return run_sync(self.get(ref, payload_type))

    def get_run_sync(self, run_id: str) -> Run:
        """Read a run back, from code that is not async."""
        return run_sync(self.get_run(run_id))

    def list_runs_sync(
        self,
        *,
        outcome: RunOutcome | None = None,
        factory: str | None = None,
        order: Literal["newest", "oldest"] = "newest",
        limit: int = 50,
    ) -> list[Run]:
        """List stored runs, from code that is not async. See ``RunStore.list_runs``."""
        return run_sync(self.list_runs(outcome=outcome, factory=factory, order=order, limit=limit))


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

    async def list_runs(
        self,
        *,
        outcome: RunOutcome | None = None,
        factory: str | None = None,
        order: Literal["newest", "oldest"] = "newest",
        limit: int = 50,
    ) -> list[Run]:
        """List stored runs by when they last changed. See ``RunStore.list_runs``."""
        _check_limit(limit, order)
        runs = (decode_run(data) for data in self._runs.values())
        return _select_runs(runs, outcome=outcome, factory=factory, order=order, limit=limit)

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

    async def list_runs(
        self,
        *,
        outcome: RunOutcome | None = None,
        factory: str | None = None,
        order: Literal["newest", "oldest"] = "newest",
        limit: int = 50,
    ) -> list[Run]:
        """List stored runs by when they last changed. See ``RunStore.list_runs``.

        It reads every run file, which is quick for the hundreds of runs one machine
        makes. A store for many more should answer from an index.
        """
        _check_limit(limit, order)
        runs = await asyncio.to_thread(self._read_runs)
        return _select_runs(runs, outcome=outcome, factory=factory, order=order, limit=limit)

    def _read_runs(self) -> list[Run]:
        # Lock files end in .lock and half-written files have no suffix, so neither is read.
        return [decode_run(path.read_bytes()) for path in (self.root / "runs").glob("*.json")]

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


_SCHEMA_VERSION = 1

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS artifacts (
    digest TEXT PRIMARY KEY,
    data BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    factory TEXT NOT NULL,
    outcome TEXT NOT NULL,
    revision INTEGER NOT NULL,
    created_at TEXT,
    updated_at TEXT,
    data BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_by_outcome ON runs (outcome, updated_at);
CREATE TABLE IF NOT EXISTS schema (
    version INTEGER NOT NULL
);
"""


def _moment(when: datetime) -> str:
    """Write ``when`` in UTC, in a form that sorts as text in time order."""
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _is_busy(error: sqlite3.OperationalError) -> bool:
    # The extended codes, such as SQLITE_BUSY_SNAPSHOT, keep SQLITE_BUSY in their low byte.
    code = getattr(error, "sqlite_errorcode", None)
    return code is not None and code & 0xFF == sqlite3.SQLITE_BUSY


class SQLiteStore(_SyncReads):
    """Keeps artifacts and runs in one SQLite file at ``path``.

    It creates the file and its tables on first use. Each call opens its own connection
    in a worker thread and closes it before returning, so no connection is shared. The
    file uses SQLite's write-ahead log, so one process can read while another writes.

    Saving a run is a compare-and-swap inside one write transaction. A save that waits
    longer than ``busy_timeout`` seconds for another writer raises :class:`StoreBusy`.
    A file written with another schema version raises :class:`StoreSchemaMismatch`.
    """

    def __init__(self, path: str | Path, *, busy_timeout: float = 5.0) -> None:
        self.path = Path(path)
        self.busy_timeout = busy_timeout

    async def put(self, artifact: Artifact[Any]) -> ArtifactRef:
        data = encode_artifact(artifact)
        await asyncio.to_thread(self._put_artifact, artifact.ref.digest, data)
        return artifact.ref

    async def get(self, ref: ArtifactRef, payload_type: Any) -> Artifact[Any]:
        row = await asyncio.to_thread(
            self._fetch_one, "SELECT data FROM artifacts WHERE digest = ?", (ref.digest,)
        )
        if row is None:
            raise ArtifactNotFound(f"no artifact is filed under {ref}")
        return decode_artifact(ref, row[0], payload_type)

    async def exists(self, ref: ArtifactRef) -> bool:
        row = await asyncio.to_thread(
            self._fetch_one, "SELECT 1 FROM artifacts WHERE digest = ?", (ref.digest,)
        )
        return row is not None

    async def get_run(self, run_id: str) -> Run:
        row = await asyncio.to_thread(
            self._fetch_one, "SELECT data FROM runs WHERE run_id = ?", (run_id,)
        )
        if row is None:
            raise RunNotFound(f"no run is stored under {run_id!r}")
        return decode_run(row[0])

    async def put_run(self, run: Run, *, replaces: Run | None) -> None:
        await asyncio.to_thread(self._swap_run, run, replaces)

    async def list_runs(
        self,
        *,
        outcome: RunOutcome | None = None,
        factory: str | None = None,
        order: Literal["newest", "oldest"] = "newest",
        limit: int = 50,
    ) -> list[Run]:
        """List stored runs by when they last changed. See ``RunStore.list_runs``.

        It answers from an index on ``outcome`` and ``updated_at``.
        """
        _check_limit(limit, order)
        rows = await asyncio.to_thread(self._select_runs, outcome, factory, order, limit)
        return [decode_run(data) for data in rows]

    @contextlib.contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        """Open a connection to a file with this store's schema, and close it after."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None leaves transactions to us: each one starts with BEGIN.
        connection = sqlite3.connect(self.path, timeout=self.busy_timeout, isolation_level=None)
        try:
            try:
                self._check_schema(connection)
                yield connection
            except sqlite3.OperationalError as error:
                if _is_busy(error):
                    raise StoreBusy(
                        f"the store at {str(self.path)!r} was busy for longer than "
                        f"{self.busy_timeout} seconds, so nothing was saved; try again"
                    ) from error
                raise
        finally:
            connection.close()

    def _check_schema(self, connection: sqlite3.Connection) -> None:
        """Create the tables in a new file, and refuse a file of another schema version."""
        version = self._read_version(connection)
        if version is None:
            # WAL mode is kept in the file, so it is set once, outside any transaction.
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("BEGIN IMMEDIATE")
            try:
                for statement in _CREATE_TABLES.split(";"):
                    if statement.strip():
                        connection.execute(statement)
                # Another process may have made the tables while this one waited.
                version = self._read_version(connection)
                if version is None:
                    connection.execute(
                        "INSERT INTO schema (version) VALUES (?)", (_SCHEMA_VERSION,)
                    )
                    version = _SCHEMA_VERSION
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        if version != _SCHEMA_VERSION:
            raise StoreSchemaMismatch(
                f"the store at {str(self.path)!r} has schema version {version}, but this "
                f"version of the library reads only schema version {_SCHEMA_VERSION}"
            )

    @staticmethod
    def _read_version(connection: sqlite3.Connection) -> int | None:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema'"
        ).fetchone()
        if table is None:
            return None
        row = connection.execute("SELECT version FROM schema").fetchone()
        return None if row is None else int(row[0])

    def _fetch_one(self, query: str, parameters: tuple[Any, ...]) -> tuple[Any, ...] | None:
        with self._connect() as connection:
            return connection.execute(query, parameters).fetchone()

    def _put_artifact(self, digest: str, data: bytes) -> None:
        with self._connect() as connection:
            # An artifact is never changed, so a digest that is already filed is left alone.
            connection.execute(
                "INSERT OR IGNORE INTO artifacts (digest, data) VALUES (?, ?)", (digest, data)
            )

    def _select_runs(
        self,
        outcome: RunOutcome | None,
        factory: str | None,
        order: Literal["newest", "oldest"],
        limit: int,
    ) -> list[bytes]:
        direction = "DESC" if order == "newest" else "ASC"
        # Runs changed at the same moment keep run_id order, as in the other stores.
        query = (
            "SELECT data FROM runs"
            " WHERE (:outcome IS NULL OR outcome = :outcome)"
            " AND (:factory IS NULL OR factory = :factory)"
            f" ORDER BY updated_at {direction}, run_id"
            " LIMIT :limit"
        )
        parameters = {"outcome": outcome, "factory": factory, "limit": limit}
        with self._connect() as connection:
            return [row[0] for row in connection.execute(query, parameters).fetchall()]

    def _swap_run(self, run: Run, replaces: Run | None) -> None:
        data = encode_run(run)
        with self._connect() as connection:
            # BEGIN IMMEDIATE takes the write lock before the read, so no other writer
            # can move the run on between the check and the save.
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT data FROM runs WHERE run_id = ?", (run.run_id,)
                ).fetchone()
                _check_swap(run, replaces, None if row is None else decode_run(row[0]))
                connection.execute(
                    "INSERT OR REPLACE INTO runs"
                    " (run_id, factory, outcome, revision, created_at, updated_at, data)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        run.run_id,
                        run.factory,
                        run.outcome,
                        run.revision,
                        _moment(run.created_at),
                        _moment(run.updated_at),
                        data,
                    ),
                )
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
