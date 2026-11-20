"""The worker: the one process that runs the factory, and so the one that needs a model key."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from types import FrameType
from typing import Any

from pydantic_ai.models import Model

from ..contracts import run_sync
from ..factory import ApprovalRefused, Factory, Job, Run, RunConflict, Store
from ..stores import StoreBusy
from ._sqlite import SQLiteDispatcher

POLL_SECONDS = 0.5
"""How long the worker waits before it looks for a job again, when none is queued."""

FINISH_ATTEMPTS = 3
"""How many times the worker tries to finish a job while the jobs are busy."""

FINISH_RETRY_SECONDS = 0.5
"""How long the worker waits before it tries again to finish a job."""

BUSY = "The store was busy for too long, so the job stopped."
ANSWERED_FIRST = "Someone answered first:"
MOVED_ON = "The run moved on while this job was working on it:"

log = logging.getLogger("pydantic_ai_factory.serve")


def work(
    build_factory: Callable[[Model | str], Factory],
    store: Store,
    dispatcher: SQLiteDispatcher,
    *,
    model: Model | str,
    lease: timedelta = timedelta(minutes=2),
    deps: Any = None,
    until_idle: bool = False,
) -> None:
    """Do the factory's queued jobs, one at a time, until told to stop.

    It builds the factory once, with ``build_factory(model)``. It then claims the oldest
    queued job, does it, finishes it as done or failed, and claims the next. A start
    job runs its mission under the job's run id; an answer job resumes its run with the
    answer. ``deps`` goes to every run and resume, as a script would pass it.

    While a job runs, the claim is extended every ``lease / 4``. A claim that lapses is
    reported as stalled and never given to another worker, but this worker may still
    finish the job.

    A job that saved nothing and then failed with ``RunConflict`` or ``ApprovalRefused``
    is finished as refused, and its run is left alone. A ``RunConflict`` after the job
    saved the run means the run moved on without it, so the job fails, not refused, and
    the run is left alone. Any other error, ``StoreBusy`` included, abandons the run as
    this job last saved it, with the error as the reason and ``"worker"`` as who ended
    it, before the job is finished as failed. A job that saved nothing abandons nothing.

    If the jobs are busy when a job is finished, the worker tries again a few times, then
    logs it and carries on. Any other error in one turn of the loop is logged, and the
    worker carries on with the next.

    The first SIGINT or SIGTERM stops it claiming. It finishes the job in hand, then
    returns; a second signal acts as it would without the worker. With ``until_idle``,
    it also returns once no job is queued.
    """
    if lease <= timedelta(0):
        raise ValueError(f"a lease must be longer than nothing, not {lease}")
    factory = build_factory(model)
    # Through the library's run_sync, not asyncio.run: it reuses the thread's loop, as the
    # store's sync helpers do, so mixing them with ``work`` in one process leaks no loop.
    run_sync(_work(factory, store, dispatcher, lease=lease, deps=deps, until_idle=until_idle))


class _Stop:
    """Signal handler: the first signal asks the loop to stop, and puts back the old handlers.

    So a second signal does what it would have done without the worker.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.event = asyncio.Event()
        self.previous: dict[int, Any] = {}

    def install(self) -> None:
        # Python handles signals only on the main thread.
        if threading.current_thread() is not threading.main_thread():
            return
        for number in (signal.SIGINT, signal.SIGTERM):
            self.previous[number] = signal.signal(number, self)

    def restore(self) -> None:
        for number, handler in self.previous.items():
            signal.signal(number, handler)
        self.previous.clear()

    def __call__(self, signum: int, frame: FrameType | None) -> None:
        log.info("Stopping after the current job. Send the signal again to stop now.")
        self.restore()
        self.loop.call_soon_threadsafe(self.event.set)


async def _work(
    factory: Factory,
    store: Store,
    dispatcher: SQLiteDispatcher,
    *,
    lease: timedelta,
    deps: Any,
    until_idle: bool,
) -> None:
    stop = _Stop(asyncio.get_running_loop())
    stop.install()
    try:
        while not stop.event.is_set():
            try:
                idle = await _turn(factory, store, dispatcher, lease=lease, deps=deps)
            except Exception:
                # One bad turn must not end the worker: log it, wait, and look again.
                log.exception("The worker hit an error; carrying on with the next job.")
                idle = None
            if idle is False:
                continue
            if idle is True and until_idle:
                return
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.event.wait(), POLL_SECONDS)
    finally:
        stop.restore()


async def _turn(
    factory: Factory, store: Store, dispatcher: SQLiteDispatcher, *, lease: timedelta, deps: Any
) -> bool | None:
    """Claim one job and do it.

    Returns ``True`` when no job is queued, ``False`` when a job was done, and ``None``
    when the jobs were too busy to say.
    """
    try:
        job = await dispatcher.claim(lease)
    except StoreBusy:
        log.warning("The jobs were busy, so no job was claimed; trying again.")
        return None
    if job is None:
        return True
    done = await _do(job, factory, store, dispatcher, lease=lease, deps=deps)
    if done is not None:
        outcome = done.state if done.error is None else f"{done.state}: {done.error}"
        log.info("Job %s (%s run %s) %s", done.job_id, done.kind, done.run_id, outcome)
    return False


@dataclass
class _Saved:
    """The run as this job last saved it, kept by ``on_step``."""

    run: Run | None = None

    async def __call__(self, run: Run) -> None:
        self.run = run


async def _do(
    job: Job,
    factory: Factory,
    store: Store,
    dispatcher: SQLiteDispatcher,
    *,
    lease: timedelta,
    deps: Any,
) -> Job | None:
    """Do one claimed job and finish it. It never raises for a job that failed.

    Returns the finished job, or ``None`` if the jobs stayed too busy to finish it.
    """
    saved = _Saved()
    heartbeat = asyncio.create_task(_heartbeat(dispatcher, job.job_id, lease))
    error: str | None = None
    refused = False
    abandon = True
    try:
        if job.mission is not None:
            await factory.run(job.mission, store=store, run_id=job.run_id, deps=deps, on_step=saved)
        elif job.answer is not None:
            await factory.resume(job.answer, store=store, deps=deps, on_step=saved)
    except StoreBusy:
        error = BUSY
    except RunConflict as problem:
        # Before the job saved anything, the check refused it; after, the run moved on
        # without it. Either way the run is someone else's now, so it is left alone.
        refused, abandon = saved.run is None, False
        error = f"{ANSWERED_FIRST if refused else MOVED_ON} {problem}"
    except ApprovalRefused as problem:
        error, refused = str(problem), saved.run is None
    except Exception as problem:
        # The worker must carry on with the next job, so any other error fails this one.
        log.exception("Job %s failed", job.job_id)
        error = f"The job failed: {type(problem).__name__}: {problem}"
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
    if error is not None and abandon and saved.run is not None:
        await _abandon(factory, saved.run, reason=error, store=store)
    return await _finish(dispatcher, job.job_id, error=error, refused=refused)


async def _finish(
    dispatcher: SQLiteDispatcher, job_id: str, *, error: str | None, refused: bool
) -> Job | None:
    """Finish the job, trying again while the jobs are busy. Gives up with a log entry."""
    for attempt in range(1, FINISH_ATTEMPTS + 1):
        try:
            return await dispatcher.finish(job_id, error=error, refused=refused)
        except StoreBusy:
            if attempt == FINISH_ATTEMPTS:
                break
            log.warning("The jobs were busy, so job %s was not finished; trying again.", job_id)
            await asyncio.sleep(FINISH_RETRY_SECONDS)
    log.error(
        "Job %s could not be finished after %d tries; it will show as stalled.",
        job_id,
        FINISH_ATTEMPTS,
    )
    return None


async def _abandon(factory: Factory, run: Run, *, reason: str, store: Store) -> None:
    """End the run as this job last saved it. If it has moved on since, nothing changes."""
    try:
        await factory.abandon(run, reason=reason, by="worker", store=store)
    except Exception:
        log.exception("Run %s could not be abandoned", run.run_id)


async def _heartbeat(dispatcher: SQLiteDispatcher, job_id: str, lease: timedelta) -> None:
    """Extend the claim every ``lease / 4``. A failed heartbeat is tried again next time."""
    every = lease.total_seconds() / 4
    while True:
        await asyncio.sleep(every)
        try:
            if not await dispatcher.extend(job_id, lease):
                log.warning(
                    "The claim on job %s is no longer held: it lapsed, or a restart closed it.",
                    job_id,
                )
                return
        except Exception:
            log.exception("The heartbeat for job %s failed; trying again.", job_id)
