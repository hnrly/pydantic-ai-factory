"""SQLiteDispatcher: a queue of jobs in a table of the same SQLite file as SQLiteStore."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
import uuid
from collections.abc import Callable, Generator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from ..contracts import FactoryError
from ..factory import AlreadyRestarted, Answer, Job, JobNotFound, JobState, Mission
from ..stores import StoreBusy

MINIMUM_SQLITE = (3, 35, 0)
"""The first SQLite with ``UPDATE ... RETURNING``, which claiming a job in one step needs."""

STARTED_AGAIN = "Started again"
"""The error on an open job that a restart of its run closed."""

_LIMIT_MAX = 1000

# The table's name differs from the example app's ``jobs`` table, whose rows are not
# moved across. It never touches the store's tables or the file's user_version.
_TABLE = "factory_jobs"

_CREATE = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    job_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    state TEXT NOT NULL,
    queued_by TEXT NOT NULL,
    body TEXT NOT NULL,
    restart_of TEXT,
    refused INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    claimed_until TEXT
);
CREATE INDEX IF NOT EXISTS {_TABLE}_by_time ON {_TABLE} (updated_at);
CREATE INDEX IF NOT EXISTS {_TABLE}_queue ON {_TABLE} (state, created_at);
CREATE INDEX IF NOT EXISTS {_TABLE}_by_run ON {_TABLE} (run_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS {_TABLE}_restart ON {_TABLE} (restart_of)
    WHERE restart_of IS NOT NULL;
"""

_COLUMNS = (
    "job_id, run_id, kind, state, queued_by, body, restart_of, refused, error,"
    " created_at, updated_at, claimed_until"
)


class SQLiteTooOld(FactoryError):
    """The SQLite this Python was built with is older than 3.35, so it cannot claim a job."""


def utc_now() -> datetime:
    """Read the clock, in UTC. ``SQLiteDispatcher`` takes another clock for tests."""
    return datetime.now(UTC)


def _moment(when: datetime) -> str:
    """Write ``when`` in UTC, in a form that sorts as text in time order."""
    if when.utcoffset() is None:
        raise ValueError("a time on a job must carry a time zone, such as UTC")
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _is_busy(error: sqlite3.OperationalError) -> bool:
    # The extended codes, such as SQLITE_BUSY_SNAPSHOT, keep SQLITE_BUSY in their low byte.
    code = getattr(error, "sqlite_errorcode", None)
    return code is not None and code & 0xFF == sqlite3.SQLITE_BUSY


def _job(row: tuple[Any, ...]) -> Job:
    (
        job_id,
        run_id,
        kind,
        state,
        queued_by,
        body,
        restart_of,
        refused,
        error,
        created_at,
        updated_at,
        claimed_until,
    ) = row
    return Job.model_validate(
        {
            "job_id": job_id,
            "run_id": run_id,
            "kind": kind,
            "state": state,
            "by": queued_by,
            **json.loads(body),
            "restart_of": restart_of,
            "refused": bool(refused),
            "error": error,
            "created_at": created_at,
            "updated_at": updated_at,
            "claimed_until": claimed_until,
        }
    )


def _check_lease(lease: timedelta) -> None:
    if lease <= timedelta(0):
        raise ValueError(f"a lease must be longer than nothing, not {lease}")


def _several(value: str | Sequence[str] | None, label: str) -> list[str] | None:
    if value is None:
        return None
    chosen = [value] if isinstance(value, str) else list(value)
    if not chosen:
        raise ValueError(f"{label} must name at least one value, or be left out")
    return chosen


class SQLiteDispatcher:
    """Keeps a factory's queued jobs in a ``factory_jobs`` table of the SQLite file at ``path``.

    It is meant for the same file as :class:`SQLiteStore`, and shares it without touching
    the store's tables or its schema version. Like the store, each call opens its own
    connection in a worker thread, waits up to ``busy_timeout`` seconds for another
    writer, and raises :class:`StoreBusy` after that. ``clock`` gives every time it
    records, so tests can move time on.

    ``start``, ``answer``, ``job`` and ``list_jobs`` make it a :class:`Dispatcher`.
    ``claim``, ``extend`` and ``finish`` are for the worker. A claim lasts for a lease,
    which the worker extends with a heartbeat. A claim that lapses is reported as stalled
    and never handed to another worker. Nothing is retried.

    Raises :class:`SQLiteTooOld` if Python's SQLite is older than 3.35.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], datetime] = utc_now,
        busy_timeout: float = 5.0,
    ) -> None:
        if sqlite3.sqlite_version_info < MINIMUM_SQLITE:
            minimum = ".".join(str(part) for part in MINIMUM_SQLITE)
            raise SQLiteTooOld(
                f"SQLite {sqlite3.sqlite_version} is too old; SQLiteDispatcher needs {minimum} "
                "or later for UPDATE ... RETURNING"
            )
        self.path = Path(path)
        self.clock = clock
        self.busy_timeout = busy_timeout

    # The Dispatcher protocol.

    async def start(self, mission: Mission, *, by: str, restart_of: str | None = None) -> Job:
        """Queue a new run of ``mission``. Its run id is chosen now, so it can be linked to.

        With ``restart_of``, any open job of that old run fails with "Started again", in
        the same transaction. Raises :class:`AlreadyRestarted` if a job already names
        ``restart_of``.
        """
        now = self._now()
        job = Job(
            job_id=uuid.uuid4().hex,
            run_id=uuid.uuid4().hex,
            kind="start",
            state="queued",
            by=by,
            mission=mission,
            restart_of=restart_of,
            created_at=now,
            updated_at=now,
        )
        body = json.dumps({"mission": mission.model_dump(mode="json")})
        await asyncio.to_thread(self._insert, job, body)
        return job

    async def answer(self, answer: Answer) -> Job:
        """Queue ``answer``, to be applied to the run it names, as ``answer.by``."""
        now = self._now()
        job = Job(
            job_id=uuid.uuid4().hex,
            run_id=answer.run_id,
            kind="answer",
            state="queued",
            by=answer.by,
            answer=answer,
            created_at=now,
            updated_at=now,
        )
        body = json.dumps({"answer": answer.model_dump(mode="json")})
        await asyncio.to_thread(self._insert, job, body)
        return job

    async def job(self, job_id: str) -> Job:
        """Read one job. Raises :class:`JobNotFound` if there is none with that id."""
        row = await asyncio.to_thread(
            self._fetch_one, f"SELECT {_COLUMNS} FROM {_TABLE} WHERE job_id = ?", (job_id,)
        )
        if row is None:
            raise JobNotFound(f"no job is queued under {job_id!r}")
        return _job(row)

    async def list_jobs(
        self,
        *,
        kind: Literal["start", "answer"] | None = None,
        state: JobState | Sequence[JobState] | None = None,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
        latest_per_run: bool = False,
        restart_of: str | Sequence[str] | None = None,
        limit: int = 100,
    ) -> list[Job]:
        """List jobs, newest change first, filtered by what is given.

        With ``latest_per_run``, only each run's newest job that was not refused is
        considered, by when it was queued, and the other filters then apply to it. So
        ``state="claimed"`` with it lists the runs whose driving job is claimed. Only the
        filters given go into the query, which reads from an index on ``updated_at``.
        """
        if not 1 <= limit <= _LIMIT_MAX:
            raise ValueError(f"limit must be between 1 and {_LIMIT_MAX}, not {limit}")
        conditions: list[str] = []
        parameters: list[Any] = []
        if kind is not None:
            conditions.append("kind = ?")
            parameters.append(kind)
        for column, given in (
            ("state", _several(state, "state")),
            ("restart_of", _several(restart_of, "restart_of")),
        ):
            if given is not None:
                conditions.append(f"{column} IN ({', '.join('?' for _ in given)})")
                parameters.extend(given)
        if updated_after is not None:
            conditions.append("updated_at > ?")
            parameters.append(_moment(updated_after))
        if updated_before is not None:
            conditions.append("updated_at < ?")
            parameters.append(_moment(updated_before))
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        source = (
            f"(SELECT {_COLUMNS}, seq FROM (SELECT {_COLUMNS}, rowid AS seq, ROW_NUMBER() OVER "
            f"(PARTITION BY run_id ORDER BY created_at DESC, rowid DESC) AS newest "
            f"FROM {_TABLE} WHERE refused = 0) WHERE newest = 1)"
            if latest_per_run
            else f"(SELECT {_COLUMNS}, rowid AS seq FROM {_TABLE})"
        )
        query = f"SELECT {_COLUMNS} FROM {source}{where} ORDER BY updated_at DESC, seq DESC LIMIT ?"
        rows = await asyncio.to_thread(self._fetch_all, query, (*parameters, limit))
        return [_job(row) for row in rows]

    # The worker's calls.

    async def claim(self, lease: timedelta) -> Job | None:
        """Claim the oldest queued job for ``lease``, or return ``None`` when none is queued.

        It is one ``UPDATE ... RETURNING`` statement, so two workers never claim one job.
        """
        _check_lease(lease)
        now = self._now()
        row = await asyncio.to_thread(
            self._fetch_one,
            f"""
            UPDATE {_TABLE} SET state = 'claimed', claimed_until = ?, updated_at = ?
            WHERE job_id = (
                SELECT job_id FROM {_TABLE} WHERE state = 'queued'
                ORDER BY created_at, rowid LIMIT 1
            )
            RETURNING {_COLUMNS}
            """,
            (_moment(now + lease), _moment(now)),
        )
        return None if row is None else _job(row)

    async def extend(self, job_id: str, lease: timedelta) -> bool:
        """Extend a claim to ``lease`` from now. Returns ``False`` once the claim has lapsed.

        A lapsed claim is never revived, so a late heartbeat cannot un-stall a job.
        """
        _check_lease(lease)
        now = self._now()
        row = await asyncio.to_thread(
            self._fetch_one,
            f"""
            UPDATE {_TABLE} SET claimed_until = ?, updated_at = ?
            WHERE job_id = ? AND state = 'claimed' AND claimed_until > ?
            RETURNING job_id
            """,
            (_moment(now + lease), _moment(now), job_id, _moment(now)),
        )
        return row is not None

    async def finish(self, job_id: str, *, error: str | None = None, refused: bool = False) -> Job:
        """Mark a claimed job ``done``, or ``failed`` with ``error`` saying why.

        ``refused`` says the job failed at the check and never touched the run; it needs
        an ``error``. A job whose claim lapsed may still be finished. A job that is not
        claimed, such as one a restart closed, is returned as it is, unchanged. Raises
        :class:`JobNotFound` if there is no such job.
        """
        if refused and error is None:
            raise ValueError("a refused job needs an error saying why")
        now = self._now()
        row = await asyncio.to_thread(
            self._finish, job_id, "done" if error is None else "failed", error, refused, now
        )
        if row is None:
            raise JobNotFound(f"no job is queued under {job_id!r}")
        return _job(row)

    # Inside a worker thread.

    def _now(self) -> datetime:
        now = self.clock()
        _moment(now)  # refuse a clock with no time zone before anything is written
        return now

    @contextlib.contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        """Open a connection with the jobs table in place, and close it after."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None leaves transactions to us: each one starts with BEGIN.
        connection = sqlite3.connect(self.path, timeout=self.busy_timeout, isolation_level=None)
        try:
            try:
                self._create_table(connection)
                yield connection
            except sqlite3.OperationalError as error:
                if _is_busy(error):
                    raise StoreBusy(
                        f"the jobs at {str(self.path)!r} were busy for longer than "
                        f"{self.busy_timeout} seconds, so nothing was saved; try again"
                    ) from error
                raise
        finally:
            connection.close()

    @staticmethod
    def _create_table(connection: sqlite3.Connection) -> None:
        """Make the jobs table if the file has none. Checking costs one read."""
        found = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (_TABLE,)
        ).fetchone()
        if found is not None:
            return
        # The store sets the same mode; the web process reads while the worker writes.
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("BEGIN IMMEDIATE")
        try:
            for statement in _CREATE.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise

    def _fetch_one(self, query: str, parameters: tuple[Any, ...]) -> tuple[Any, ...] | None:
        with self._connect() as connection:
            return connection.execute(query, parameters).fetchone()

    def _fetch_all(self, query: str, parameters: tuple[Any, ...]) -> list[tuple[Any, ...]]:
        with self._connect() as connection:
            return connection.execute(query, parameters).fetchall()

    def _insert(self, job: Job, body: str) -> None:
        with self._connect() as connection:
            # BEGIN IMMEDIATE takes the write lock first, so a restart closes the old
            # run's jobs and queues the new one as one change.
            connection.execute("BEGIN IMMEDIATE")
            try:
                if job.restart_of is not None:
                    taken = connection.execute(
                        f"SELECT run_id FROM {_TABLE} WHERE restart_of = ?", (job.restart_of,)
                    ).fetchone()
                    if taken is not None:
                        raise AlreadyRestarted(
                            f"run {job.restart_of!r} was started again already, as run {taken[0]!r}"
                        )
                    connection.execute(
                        f"""
                        UPDATE {_TABLE} SET state = 'failed', error = ?, updated_at = ?
                        WHERE run_id = ? AND state IN ('queued', 'claimed')
                        """,
                        (STARTED_AGAIN, _moment(job.updated_at), job.restart_of),
                    )
                connection.execute(
                    f"INSERT INTO {_TABLE} ({_COLUMNS})"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?, NULL)",
                    (
                        job.job_id,
                        job.run_id,
                        job.kind,
                        job.state,
                        job.by,
                        body,
                        job.restart_of,
                        _moment(job.created_at),
                        _moment(job.updated_at),
                    ),
                )
                connection.execute("COMMIT")
            except sqlite3.IntegrityError as error:
                connection.execute("ROLLBACK")
                if job.restart_of is None:
                    raise
                # The unique index on restart_of catches a restart that raced this one.
                raise AlreadyRestarted(
                    f"run {job.restart_of!r} was started again already"
                ) from error
            except BaseException:
                connection.execute("ROLLBACK")
                raise

    def _finish(
        self, job_id: str, state: str, error: str | None, refused: bool, now: datetime
    ) -> tuple[Any, ...] | None:
        with self._connect() as connection:
            row = connection.execute(
                f"""
                UPDATE {_TABLE} SET state = ?, error = ?, refused = ?, updated_at = ?
                WHERE job_id = ? AND state = 'claimed'
                RETURNING {_COLUMNS}
                """,
                (state, error, int(refused), _moment(now), job_id),
            ).fetchone()
            if row is not None:
                return row
            return connection.execute(
                f"SELECT {_COLUMNS} FROM {_TABLE} WHERE job_id = ?", (job_id,)
            ).fetchone()
