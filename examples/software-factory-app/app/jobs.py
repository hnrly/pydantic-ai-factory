"""The jobs table: the queue between the web process and the worker.

It lives in the same SQLite file as the store, in a table of its own that the library
never reads. A job is ``start`` (run a mission) or ``answer`` (carry on a waiting run).
It moves from ``queued`` to ``claimed`` to ``done`` or ``failed``.

Each function opens its own connection and closes it before returning, so the web
process and the worker can call them at the same time.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from .models import Answer

JobKind = Literal["start", "answer"]
JobState = Literal["queued", "claimed", "done", "failed"]

_CREATE = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    run_id TEXT NOT NULL,
    body TEXT NOT NULL,
    state TEXT NOT NULL,
    claimed_at TEXT,
    finished_at TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS jobs_by_state ON jobs (state, created_at);
CREATE INDEX IF NOT EXISTS jobs_by_run ON jobs (run_id, kind, created_at)
"""

_COLUMNS = "id, created_at, kind, run_id, body, state, claimed_at, finished_at, error"


class JobNotFound(Exception):
    """No job is stored under the id that was asked for."""


class Job(BaseModel):
    """One job in the queue.

    For ``start``, ``body`` is ``{"mission": ..., "by": ...}`` and ``run_id`` is the id
    the run will be saved under. For ``answer``, ``body`` is an :class:`Answer` as JSON
    and ``run_id`` is the run being answered. ``error`` says why a failed job failed.
    """

    id: str
    kind: JobKind
    run_id: str
    body: dict[str, Any]
    state: JobState
    created_at: datetime
    claimed_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None


def _now() -> datetime:
    return datetime.now(UTC)


@contextlib.contextmanager
def _connect(db: Path) -> Generator[sqlite3.Connection]:
    """Open a connection with the jobs table in place, and close it after."""
    db.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None leaves transactions to SQLite: each statement commits alone.
    connection = sqlite3.connect(db, timeout=5.0, isolation_level=None)
    try:
        connection.executescript(_CREATE)
        yield connection
    finally:
        connection.close()


def _job(row: tuple[Any, ...]) -> Job:
    id_, created_at, kind, run_id, body, state, claimed_at, finished_at, error = row
    return Job(
        id=id_,
        created_at=created_at,
        kind=kind,
        run_id=run_id,
        body=json.loads(body),
        state=state,
        claimed_at=claimed_at,
        finished_at=finished_at,
        error=error,
    )


def create_table(db: Path) -> None:
    """Create the jobs table in ``db`` if it is not there. Safe to call again."""
    with _connect(db) as connection:
        # The store sets the same mode; the web process reads while the worker writes.
        connection.execute("PRAGMA journal_mode = WAL")


def _add(db: Path, kind: JobKind, run_id: str, body: dict[str, Any]) -> Job:
    job = Job(
        id=uuid.uuid4().hex, kind=kind, run_id=run_id, body=body, state="queued", created_at=_now()
    )
    with _connect(db) as connection:
        connection.execute(
            f"INSERT INTO jobs ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL)",
            (job.id, job.created_at.isoformat(), kind, run_id, json.dumps(body), "queued"),
        )
    return job


def add_start(db: Path, mission: str, by: str) -> Job:
    """Queue a new run of ``mission``. The run's id is chosen now, so it can be linked to."""
    return _add(db, "start", uuid.uuid4().hex, {"mission": mission, "by": by})


def add_answer(db: Path, run_id: str, answer: Answer) -> Job:
    """Queue ``answer`` to the run ``run_id``."""
    return _add(db, "answer", run_id, answer.model_dump(mode="json"))


def claim_next(db: Path) -> Job | None:
    """Claim the oldest queued job, or return ``None`` when there is none.

    It is one ``UPDATE ... RETURNING`` statement, so two workers never claim one job.
    """
    with _connect(db) as connection:
        row = connection.execute(
            f"""
            UPDATE jobs SET state = 'claimed', claimed_at = ?
            WHERE id = (
                SELECT id FROM jobs WHERE state = 'queued' ORDER BY created_at, rowid LIMIT 1
            )
            RETURNING {_COLUMNS}
            """,
            (_now().isoformat(),),
        ).fetchone()
    return None if row is None else _job(row)


def finish(db: Path, job_id: str, *, error: str | None = None) -> Job:
    """Mark a job ``done``, or ``failed`` with ``error`` saying why."""
    with _connect(db) as connection:
        row = connection.execute(
            f"""
            UPDATE jobs SET state = ?, finished_at = ?, error = ?
            WHERE id = ?
            RETURNING {_COLUMNS}
            """,
            ("done" if error is None else "failed", _now().isoformat(), error, job_id),
        ).fetchone()
    if row is None:
        raise JobNotFound(f"no job is stored under {job_id!r}")
    return _job(row)


def get(db: Path, job_id: str) -> Job:
    """Read one job. Raises :class:`JobNotFound` if there is none with that id."""
    with _connect(db) as connection:
        row = connection.execute(f"SELECT {_COLUMNS} FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise JobNotFound(f"no job is stored under {job_id!r}")
    return _job(row)


def latest_for_run(db: Path, run_id: str) -> dict[str, Job]:
    """The latest job of each kind for ``run_id``, keyed by kind. Failed jobs count."""
    with _connect(db) as connection:
        rows = connection.execute(
            f"SELECT {_COLUMNS} FROM jobs WHERE run_id = ? ORDER BY created_at, rowid",
            (run_id,),
        ).fetchall()
    latest: dict[str, Job] = {}
    for row in rows:
        job = _job(row)
        latest[job.kind] = job
    return latest


def open_starts(db: Path) -> list[Job]:
    """Every start job that is queued or claimed, oldest first.

    Their runs may not be saved yet, so a list of all runs adds them from here.
    """
    with _connect(db) as connection:
        rows = connection.execute(
            f"""
            SELECT {_COLUMNS} FROM jobs
            WHERE kind = 'start' AND state IN ('queued', 'claimed')
            ORDER BY created_at, rowid
            """
        ).fetchall()
    return [_job(row) for row in rows]
