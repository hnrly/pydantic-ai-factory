"""The Dispatcher seam: how a process that never runs a factory queues work for one that does.

A web process queues a mission to start, or a person's answer to apply, as a ``Job``.
Something else takes the job and does it. The protocol only queues work and reports on
it. It has no claim: how work is taken from the queue is each dispatcher's own business,
and a durable engine has its own way.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Literal, Protocol, Self, runtime_checkable

from pydantic import AwareDatetime, model_validator

from ..contracts import FactoryError, Name, Record
from ._answer import Answer
from ._mission import Mission

JobState = Literal["queued", "claimed", "done", "failed"]
"""Where a job is: waiting to be taken, taken by a worker, or finished one way or the other."""

JobKind = Literal["start", "answer"]


class JobNotFound(FactoryError):
    """No job is queued under the id that was asked for."""


class AlreadyRestarted(FactoryError):
    """The run was started again already, so it cannot be started again a second time.

    At most one job may name a given ``restart_of``. That job is the link from the old
    run to its new one.
    """


class Job(Record):
    """One piece of queued work: start a run of a mission, or apply an answer to a run.

    ``run_id`` is the run the job makes or answers. For a start job it is chosen when the
    job is queued, so the run can be shown before a worker has saved it. ``mission`` is
    set on a start job and only then; ``answer`` is set on an answer job and only then.

    ``restart_of`` names the old run, when a person started a run again. ``refused`` is
    true when the job failed at the check, with ``RunConflict`` or ``ApprovalRefused``,
    and so never touched the run; a job that saved the run before it failed is never
    refused. ``error`` says why a failed job failed. ``updated_at``
    moves on every change: queue, claim, heartbeat and finish. ``claimed_until`` is when
    a worker's claim lapses unless it sends a heartbeat first.
    """

    job_id: Name
    run_id: Name
    kind: JobKind
    state: JobState
    by: Name
    mission: Mission | None = None
    answer: Answer | None = None
    restart_of: Name | None = None
    refused: bool = False
    error: str | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    claimed_until: AwareDatetime | None = None

    @model_validator(mode="after")
    def _is_consistent(self) -> Self:
        if (self.kind == "start") != (self.mission is not None):
            raise ValueError("a start job holds a mission, and only a start job does")
        if (self.kind == "answer") != (self.answer is not None):
            raise ValueError("an answer job holds an answer, and only an answer job does")
        if self.answer is not None and self.answer.run_id != self.run_id:
            raise ValueError(
                f"the answer is for run {self.answer.run_id!r}, not the job's run {self.run_id!r}"
            )
        if self.restart_of is not None and self.kind != "start":
            raise ValueError("only a start job can start a run again")
        if (self.state == "failed") != (self.error is not None):
            raise ValueError("a failed job says why in error, and only a failed job does")
        if self.refused and self.state != "failed":
            raise ValueError("a refused job has failed")
        if self.state == "queued" and self.claimed_until is not None:
            raise ValueError("a queued job has no claim")
        if self.state == "claimed" and self.claimed_until is None:
            raise ValueError("a claimed job says when its claim lapses")
        return self

    def stalled(self, now: datetime) -> bool:
        """Say whether a worker claimed this job and its claim lapsed before ``now``.

        A stalled job is never handed to another worker. Only finishing it, or starting
        its run again, closes it.
        """
        return (
            self.state == "claimed" and self.claimed_until is not None and self.claimed_until <= now
        )


@runtime_checkable
class Dispatcher(Protocol):
    """Queues a factory's work, and reports on it, for a process that never runs a factory.

    ``list_jobs`` lists jobs newest change first. With ``latest_per_run``, each run's
    newest job by creation that was not refused alone is considered, and the other
    filters then apply to it. That job is the one driving the run: a refused job never
    touched the run, so it never hides the job that did.
    """

    async def start(self, mission: Mission, *, by: str, restart_of: str | None = None) -> Job:
        """Queue a new run of ``mission``, and choose its run id now.

        With ``restart_of``, the job starts that old run again: in the same step, any
        open job of the old run fails with "Started again". Raises
        :class:`AlreadyRestarted` if the old run was started again already.
        """
        ...

    async def answer(self, answer: Answer) -> Job:
        """Queue ``answer``, to be applied to the run it names."""
        ...

    async def job(self, job_id: str) -> Job:
        """Read one job. Raises :class:`JobNotFound` if there is none with that id."""
        ...

    async def list_jobs(
        self,
        *,
        kind: JobKind | None = None,
        state: JobState | Sequence[JobState] | None = None,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
        latest_per_run: bool = False,
        restart_of: str | Sequence[str] | None = None,
        limit: int = 100,
    ) -> list[Job]:
        """List jobs, newest change first, filtered by what is given."""
        ...
