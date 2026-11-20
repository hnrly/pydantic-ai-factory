"""The worker: the one process that runs the factory, and so the one that needs a model key.

    python -m app.worker                  # the model named in FACTORY_MODEL
    python -m app.worker --offline        # the software factory's scripted model
    python -m app.worker --offline=asking # a scripted model that asks a question first

It claims the oldest queued job, does it, marks it done or failed, and claims the next.
When there is none it sleeps briefly. The first Ctrl+C or SIGTERM stops it claiming; it
finishes the job in hand, then exits. A second one exits at once.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import signal
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path
from types import FrameType
from typing import Any

from pydantic import ValidationError
from pydantic_ai import ToolApproved, ToolDenied
from pydantic_ai.exceptions import UserError
from pydantic_ai.models import Model
from pydantic_ai_factory import (
    Answer,
    ApprovalRefused,
    Factory,
    Run,
    RunConflict,
    RunNotFound,
    SQLiteStore,
    StoreBusy,
)
from pydantic_ai_factory_software import offline_model

from . import factory as app_factory
from . import jobs, settings
from .asking import asking_model
from .jobs import Job
from .models import Answer as Request
from .models import problem_with

MINIMUM_SQLITE = (3, 35, 0)
"""The first SQLite with ``UPDATE ... RETURNING``, which claiming a job needs."""

POLL_SECONDS = 0.5
"""How long the worker sleeps when the queue is empty."""

BUSY = "The store was busy, so nothing was saved. Try again."
ANSWERED_FIRST = "Someone answered first, so this answer was not used."

log = logging.getLogger("app.worker")


class JobProblem(Exception):
    """A job cannot be done as given. The message is for the person who queued it."""


async def do_next_job(db: Path, factory: Factory, store: SQLiteStore) -> Job | None:
    """Claim one job, do it, finish it and return it. Return ``None`` if none is queued.

    It never raises for a job that failed: the job comes back ``failed``, with the reason
    in its ``error``.
    """
    job = await asyncio.to_thread(jobs.claim_next, db)
    if job is None:
        return None
    error: str | None = None
    try:
        if job.kind == "start":
            await factory.run(str(job.body["mission"]), store=store, run_id=job.run_id)
        else:
            await _answer(job, factory, store)
    except StoreBusy:
        error = BUSY
    except (RunConflict, ApprovalRefused) as problem:
        error = f"{ANSWERED_FIRST} {problem}"
    except JobProblem as problem:
        error = str(problem)
    except Exception as problem:
        # The worker must carry on with the next job, so any other error fails this one.
        log.exception("job %s failed", job.id)
        error = f"The job failed: {type(problem).__name__}: {problem}"
    return await asyncio.to_thread(jobs.finish, db, job.id, error=error)


async def _answer(job: Job, factory: Factory, store: SQLiteStore) -> Run:
    try:
        answer = Request.model_validate(job.body)
    except ValidationError as problem:
        raise JobProblem(f"The answer could not be read: {problem}") from problem
    try:
        run = await store.get_run(job.run_id)
    except RunNotFound as problem:
        raise JobProblem(f"There is no run {job.run_id!r} to answer.") from problem
    if run.waiting is None or run.revision != answer.revision:
        raise RunConflict(
            f"Run {run.run_id!r} has moved on since revision {answer.revision}; "
            f"it is now {run.outcome!r} at revision {run.revision}."
        )
    return await factory.resume(_build(run, answer), store=store)


def _build(run: Run, answer: Request) -> Answer:
    """Turn the person's answer into the library's answer, with the run's own methods."""
    seen = answer.revision
    if answer.choice == "approve":
        return run.approve(by=answer.by, note=answer.note, revision=seen)
    if answer.choice == "changes":
        return run.request_changes(by=answer.by, note=answer.note or "", revision=seen)
    if answer.choice == "reject":
        return run.reject(by=answer.by, note=answer.note or "", revision=seen)
    if problem := problem_with(answer, run):
        raise JobProblem(problem)
    requests = run.waiting.requests if run.waiting else None
    if requests is None:  # problem_with has checked the run waits on tool calls
        raise JobProblem("The run is not waiting on tool calls.")
    replies = answer.replies or {}
    approvals = answer.approvals or {}
    results = requests.build_results(
        calls=dict(replies),
        approvals={
            call_id: ToolApproved() if allowed else ToolDenied()
            for call_id, allowed in approvals.items()
        },
    )
    return run.reply(by=answer.by, results=results, revision=seen)


def _model(offline: str | None) -> Model | str | None:
    if offline == "asking":
        return asking_model()
    if offline is not None:
        return offline_model()
    return settings.MODEL


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.worker", description="Do the factory's queued jobs."
    )
    parser.add_argument(
        "--offline",
        nargs="?",
        const="scripted",
        choices=("scripted", "asking"),
        help="use a scripted model, so no key is needed; --offline=asking asks a question first",
    )
    parser.add_argument(
        "--db", type=Path, default=settings.DB, help="the SQLite file (default: FACTORY_DB)"
    )
    return parser


class _Stop:
    """Signal handler: the first signal asks the loop to stop, the second exits at once."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.event = asyncio.Event()
        self.asked = False

    def __call__(self, signum: int, frame: FrameType | None) -> None:
        if self.asked:
            os._exit(128 + signum)
        self.asked = True
        print("Stopping after the current job. Press Ctrl+C again to stop now.", flush=True)
        self.loop.call_soon_threadsafe(self.event.set)


async def serve(
    db: Path, factory: Factory, store: SQLiteStore, *, poll: float = POLL_SECONDS
) -> None:
    """Do jobs until the first SIGINT or SIGTERM, then return once the job in hand is done."""
    stop = _Stop(asyncio.get_running_loop())
    signals = [signal.SIGINT, signal.SIGTERM]
    previous: dict[int, Any] = {number: signal.signal(number, stop) for number in signals}
    try:
        while not stop.event.is_set():
            job = await do_next_job(db, factory, store)
            if job is not None:
                outcome = job.state if job.error is None else f"{job.state}: {job.error}"
                print(f"Job {job.id} ({job.kind} run {job.run_id}) {outcome}", flush=True)
                continue
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.event.wait(), poll)
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def main(argv: Sequence[str] | None = None) -> int:
    """Start the worker. Returns 0 after a graceful stop, 2 if it cannot start."""
    # Pydantic AI prints a start-up banner on first use; it has no place in this output.
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
    args = _parser().parse_args(argv)
    if sqlite3.sqlite_version_info < MINIMUM_SQLITE:
        minimum = ".".join(str(part) for part in MINIMUM_SQLITE)
        print(
            f"worker: SQLite {sqlite3.sqlite_version} is too old; it needs {minimum} or later "
            "for UPDATE ... RETURNING.",
            file=sys.stderr,
        )
        return 2
    model = _model(args.offline)
    if model is None:
        print("worker: set FACTORY_MODEL, or add --offline to try it with no key.", file=sys.stderr)
        return 2
    try:
        factory = app_factory.build_factory(model)
    except UserError as error:
        print(f"worker: {error}. Add --offline to try it with no key.", file=sys.stderr)
        return 2
    db: Path = args.db
    jobs.create_table(db)
    name = model if isinstance(model, str) else model.model_name
    print(f"Worker started on {name}, with jobs from {db}.", flush=True)
    asyncio.run(serve(db, factory, SQLiteStore(db)))
    print("Worker stopped.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
