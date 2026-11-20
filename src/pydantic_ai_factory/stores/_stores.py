"""The three stores that ship: one in memory, one in a folder, one in a SQLite file."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import sqlite3
import tempfile
import time
from collections.abc import Generator, Iterable, Sequence
from dataclasses import dataclass
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


@dataclass(frozen=True)
class _Listing:
    """What ``RunStore.list_runs`` was asked for, checked once for every store."""

    outcomes: frozenset[str] | None
    factory: str | None
    order: Literal["newest", "oldest"]
    limit: int
    updated_after: datetime | None
    updated_before: datetime | None
    before_run_id: str | None

    def matches(self, run: Run) -> bool:
        return (
            (self.outcomes is None or run.outcome in self.outcomes)
            and (self.factory is None or run.factory == self.factory)
            and (self.updated_after is None or run.updated_at > self.updated_after)
            and self._before(run)
        )

    def _before(self, run: Run) -> bool:
        if self.updated_before is None:
            return True
        if self.before_run_id is None:
            return run.updated_at < self.updated_before
        # A cursor: runs saved at the cursor's moment come after it by run_id.
        return (run.updated_at, run.run_id) < (self.updated_before, self.before_run_id)


def _listing(
    *,
    outcome: RunOutcome | Sequence[RunOutcome] | None,
    factory: str | None,
    order: Literal["newest", "oldest"],
    limit: int,
    updated_after: datetime | None,
    updated_before: datetime | None,
    before_run_id: str | None,
) -> _Listing:
    """Check what ``list_runs`` was asked for, or raise ``ValueError``."""
    if not 1 <= limit <= _LIMIT_MAX:
        raise ValueError(f"limit must be between 1 and {_LIMIT_MAX}, not {limit}")
    if order not in ("newest", "oldest"):
        raise ValueError(f"order must be 'newest' or 'oldest', not {order!r}")
    for label, moment in (("updated_after", updated_after), ("updated_before", updated_before)):
        if moment is not None and moment.utcoffset() is None:
            raise ValueError(f"{label} must carry a time zone, such as UTC")
    if before_run_id is not None and updated_before is None:
        raise ValueError("before_run_id needs updated_before: together they name the last run seen")
    outcomes: frozenset[str] | None = None
    if outcome is not None:
        outcomes = frozenset((outcome,) if isinstance(outcome, str) else outcome)
        if not outcomes:
            raise ValueError("outcome must name at least one outcome, or be left out")
    return _Listing(outcomes, factory, order, limit, updated_after, updated_before, before_run_id)


def _select_runs(runs: Iterable[Run], listing: _Listing) -> list[Run]:
    """Filter, sort and cut ``runs`` as ``RunStore.list_runs`` describes."""
    chosen = [run for run in runs if listing.matches(run)]
    # Runs changed at the same moment sort by run_id, the same way as updated_at, so a
    # listing never shuffles and (updated_at, run_id) pages back exactly.
    chosen.sort(key=lambda run: (run.updated_at, run.run_id), reverse=listing.order == "newest")
    return chosen[: listing.limit]


class _SyncReads:
    """``get_sync``, ``get_run_sync`` and ``list_runs_sync``, for code that is not async."""

    async def get(self, ref: ArtifactRef, payload_type: Any) -> Artifact[Any]: ...

    async def get_run(self, run_id: str) -> Run: ...

    async def list_runs(
        self,
        *,
        outcome: RunOutcome | Sequence[RunOutcome] | None = None,
        factory: str | None = None,
        order: Literal["newest", "oldest"] = "newest",
        limit: int = 50,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
        before_run_id: str | None = None,
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
        outcome: RunOutcome | Sequence[RunOutcome] | None = None,
        factory: str | None = None,
        order: Literal["newest", "oldest"] = "newest",
        limit: int = 50,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
        before_run_id: str | None = None,
    ) -> list[Run]:
        """List stored runs, from code that is not async. See ``RunStore.list_runs``."""
        return run_sync(
            self.list_runs(
                outcome=outcome,
                factory=factory,
                order=order,
                limit=limit,
                updated_after=updated_after,
                updated_before=updated_before,
                before_run_id=before_run_id,
            )
        )


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
        outcome: RunOutcome | Sequence[RunOutcome] | None = None,
        factory: str | None = None,
        order: Literal["newest", "oldest"] = "newest",
        limit: int = 50,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
        before_run_id: str | None = None,
    ) -> list[Run]:
        """List stored runs by when they last changed. See ``RunStore.list_runs``."""
        listing = _listing(
            outcome=outcome,
            factory=factory,
            order=order,
            limit=limit,
            updated_after=updated_after,
            updated_before=updated_before,
            before_run_id=before_run_id,
        )
        return _select_runs((decode_run(data) for data in self._runs.values()), listing)

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
        outcome: RunOutcome | Sequence[RunOutcome] | None = None,
        factory: str | None = None,
        order: Literal["newest", "oldest"] = "newest",
        limit: int = 50,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
        before_run_id: str | None = None,
    ) -> list[Run]:
        """List stored runs by when they last changed. See ``RunStore.list_runs``.

        It reads every run file, which is quick for the hundreds of runs one machine
        makes. A store for many more should answer from an index.
        """
        listing = _listing(
            outcome=outcome,
            factory=factory,
            order=order,
            limit=limit,
            updated_after=updated_after,
            updated_before=updated_before,
            before_run_id=before_run_id,
        )
        return _select_runs(await asyncio.to_thread(self._read_runs), listing)

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


# Kept in the file's own user_version slot. A new file reads 0 there.
_SCHEMA_VERSION = 2

# The data column holds the record. The other columns of runs copy fields from it,
# so list_runs can filter and sort without reading every run.
_CREATE_TABLES = """
CREATE TABLE artifacts (
    digest TEXT PRIMARY KEY,
    data BLOB NOT NULL
);
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY,
    factory TEXT NOT NULL,
    outcome TEXT NOT NULL,
    revision INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    data BLOB NOT NULL
);
CREATE INDEX runs_by_time ON runs (updated_at);
CREATE INDEX runs_by_outcome ON runs (outcome, updated_at);
CREATE INDEX runs_by_factory ON runs (factory, updated_at);
"""

# Tables that only this store makes. Another program's tables may share the file.
_OWN_TABLES = ("artifacts", "runs")


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
        outcome: RunOutcome | Sequence[RunOutcome] | None = None,
        factory: str | None = None,
        order: Literal["newest", "oldest"] = "newest",
        limit: int = 50,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
        before_run_id: str | None = None,
    ) -> list[Run]:
        """List stored runs by when they last changed. See ``RunStore.list_runs``.

        It answers from an index on ``updated_at``, alone or after ``outcome`` or
        ``factory``. Only the filters given go into the query.
        """
        listing = _listing(
            outcome=outcome,
            factory=factory,
            order=order,
            limit=limit,
            updated_after=updated_after,
            updated_before=updated_before,
            before_run_id=before_run_id,
        )
        rows = await asyncio.to_thread(self._select_runs, listing)
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
        """Create the tables in a new file, and refuse a file of another schema version.

        Reading ``user_version`` costs one look at the file's header, so every
        connection checks it rather than the store remembering a past check.
        """
        version = self._read_version(connection)
        if version == 0:
            # WAL mode is kept in the file, so it is set once, outside any transaction.
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("BEGIN IMMEDIATE")
            try:
                # Another process may have made the tables while this one waited.
                version = self._read_version(connection)
                if version == 0:
                    self._refuse_unversioned_tables(connection)
                    for statement in _CREATE_TABLES.split(";"):
                        if statement.strip():
                            connection.execute(statement)
                    connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
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
    def _read_version(connection: sqlite3.Connection) -> int:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])

    def _refuse_unversioned_tables(self, connection: sqlite3.Connection) -> None:
        """Refuse a file that has this store's tables but no schema version."""
        marks = ", ".join("?" for _ in _OWN_TABLES)
        found = connection.execute(
            f"SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ({marks})",
            _OWN_TABLES,
        ).fetchall()
        if found:
            names = ", ".join(sorted(str(row[0]) for row in found))
            raise StoreSchemaMismatch(
                f"the store at {str(self.path)!r} has tables named {names} but no schema "
                "version, so another version of the library, or another program, made them"
            )

    def _fetch_one(self, query: str, parameters: tuple[Any, ...]) -> tuple[Any, ...] | None:
        with self._connect() as connection:
            return connection.execute(query, parameters).fetchone()

    def _put_artifact(self, digest: str, data: bytes) -> None:
        with self._connect() as connection:
            # An artifact is never changed, so a digest that is already filed is left alone.
            connection.execute(
                "INSERT OR IGNORE INTO artifacts (digest, data) VALUES (?, ?)", (digest, data)
            )

    def _select_runs(self, listing: _Listing) -> list[bytes]:
        direction = "DESC" if listing.order == "newest" else "ASC"
        # Only the filters given go in the query, so SQLite can answer from an index.
        conditions: list[str] = []
        parameters: dict[str, Any] = {"limit": listing.limit}
        if listing.outcomes is not None:
            names = sorted(listing.outcomes)
            marks = ", ".join(f":outcome{n}" for n in range(len(names)))
            conditions.append(f"outcome IN ({marks})")
            parameters.update({f"outcome{n}": name for n, name in enumerate(names)})
        if listing.factory is not None:
            conditions.append("factory = :factory")
            parameters["factory"] = listing.factory
        # updated_at is kept in a form that sorts as text in time order.
        if listing.updated_after is not None:
            conditions.append("updated_at > :after")
            parameters["after"] = _moment(listing.updated_after)
        if listing.updated_before is not None and listing.before_run_id is None:
            conditions.append("updated_at < :before")
            parameters["before"] = _moment(listing.updated_before)
        elif listing.updated_before is not None:
            # The cursor (updated_at, run_id). The first half lets the index find the range.
            conditions.append(
                "updated_at <= :before AND (updated_at < :before OR run_id < :before_run_id)"
            )
            parameters["before"] = _moment(listing.updated_before)
            parameters["before_run_id"] = listing.before_run_id
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        # Runs changed at the same moment sort by run_id the same way, as in the other stores.
        query = (
            f"SELECT data FROM runs{where}"
            f" ORDER BY updated_at {direction}, run_id {direction} LIMIT :limit"
        )
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
