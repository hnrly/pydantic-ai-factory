"""What the board's API sends and takes.

Every model here is plain Pydantic, so the card rules, the history and the pages can
build them without FastAPI.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ...artifacts import OutputFormat
from ...factory import FactoryShape, Job

CardState = Literal[
    "passed",
    "earlier_version",
    "waiting",
    "failed",
    "did_not_run",
    "stalled",
    "queued",
    "starting",
    "running",
]
Column = Literal["missions", "station", "done", "stopped"]
Action = Literal["approve", "changes", "reject", "reply", "start_again"]


class NewRun(BaseModel):
    """A mission to start. Who starts it comes from the request, not the body."""

    model_config = ConfigDict(extra="forbid")

    mission: str

    @field_validator("mission")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("the mission must not be blank")
        return value


class AnswerBody(BaseModel):
    """What a person decided, at the revision they saw.

    The station, the wait and the work come from the run, never from the body, so the
    body refuses any other field.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["approve", "changes", "reject", "reply"]
    revision: int = Field(ge=0)
    note: str | None = None
    replies: dict[str, str | dict[str, str | list[str]]] | None = None
    approvals: dict[str, bool] | None = None


class Queued(BaseModel):
    """A job was queued. Follow it with ``GET /api/jobs/{job_id}``."""

    run_id: str
    job_id: str


class JobView(BaseModel):
    """A job as the page follows it: its state, why it failed, and whether it stalled.

    ``stalled`` is true when a worker claimed the job and the claim has lapsed.
    """

    job_id: str
    run_id: str
    kind: Literal["start", "answer"]
    state: Literal["queued", "claimed", "done", "failed"]
    by: str
    restart_of: str | None = None
    refused: bool = False
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    claimed_until: datetime | None = None
    stalled: bool


class WaitingSummary(BaseModel):
    """What a waiting card waits for, at which station, and since when."""

    kind: Literal["approval", "escalation", "tool-calls"]
    station: str
    since: datetime


class ReworkCount(BaseModel):
    """How many of the reworks a rule allows have been used."""

    count: int
    limit: int


class Card(BaseModel):
    """One run, or one mission queued but not yet started, as the board shows it.

    ``column`` is ``"missions"`` for a mission whose run is not saved yet, ``"station"``,
    with the station in ``station``, or ``"done"`` or ``"stopped"``. ``queued`` is true
    while a job for the card is queued, or claimed with a claim that has not lapsed.
    ``needs_you`` is true when the card waits for a person and no answer is queued.
    """

    run_id: str
    mission: str
    column: Column
    station: str | None = None
    state: CardState
    queued: bool = False
    needs_you: bool = False
    waiting: WaitingSummary | None = None
    rework: ReworkCount | None = None
    revision: int | None = None
    updated_at: datetime
    restarted_as: str | None = None
    reason: str | None = None
    actions: list[Action] = []


class Board(BaseModel):
    """The factory's shape, and the cards that changed since ``since`` was asked for.

    ``since`` is what to send on the next poll. ``more`` says, on a full load, whether
    Done and Stopped hold more cards than were sent.
    """

    shape: FactoryShape
    cards: list[Card]
    since: datetime
    more: dict[str, bool] | None = None


class CardPage(BaseModel):
    """A page of cards, newest change first. Send ``before`` back for the next page."""

    cards: list[Card]
    before: str | None = None


class OptionView(BaseModel):
    """One choice a question offers. The recommended one comes first."""

    label: str
    description: str | None = None


class AskView(BaseModel):
    """One question in an ``AskPerson`` call. Answer it by ``header``."""

    header: str
    question: str
    options: list[OptionView]
    multi_select: bool


class ToolCallView(BaseModel):
    """One pending tool call: questions an agent asked, or a call that needs sign-off.

    An ``AskPerson`` call lists its questions in ``asks``; reply to it with an object that
    maps each header to a label, a list of labels, or your own words. Any other call
    that waits for an answer has its question in ``question``; reply to it with text.
    """

    id: str
    tool: str
    args: dict[str, Any]
    question: str | None = None
    asks: list[AskView] = []


class WaitingView(BaseModel):
    """What a waiting run asks of a person, in full."""

    kind: Literal["approval", "escalation", "tool-calls"]
    station: str
    agent: str | None = None
    reasons: list[str]
    offers_work: bool
    questions: list[ToolCallView] = []
    sign_offs: list[ToolCallView] = []


class StepView(BaseModel):
    station: str
    attempt: int
    outcome: str
    reason: str | None = None


class AnswerView(BaseModel):
    kind: str
    by: str
    station: str
    note: str | None = None
    at: datetime


TrackState = Literal["done", "here", "ahead", "stopped"]


class TrackStop(BaseModel):
    """One station on a run's track: whether the work has passed it, is there, or has not
    reached it, and which attempt it is on or ended with.

    ``"stopped"`` marks the station where a run that ended early, or stalled, stopped.
    """

    station: str
    state: TrackState
    attempt: int | None = None


HistoryKind = Literal[
    "mission",
    "attempt",
    "review",
    "evidence",
    "gate",
    "waiting",
    "answer",
    "stalled",
    "ended",
    "restarted",
    "done",
    "failed",
]


class HistoryEvent(BaseModel):
    """One thing that happened to a run, as its history shows it.

    ``at`` is left out when the records do not say when it happened. ``run_id`` names
    another run the event links to, such as the new run a restart began.
    """

    kind: HistoryKind
    title: str
    detail: str | None = None
    station: str | None = None
    attempt: int | None = None
    by: str | None = None
    at: datetime | None = None
    run_id: str | None = None


class RunDetail(Card):
    """One card in full: what it waits for, its output, its steps, answers and last job.

    ``output`` is the work a waiting run offers, or a finished run's output, rendered by
    in its station's ``output_format``: text, Markdown or formatted JSON.

    ``started_by`` is who added the mission, and ``created_at`` when. ``waiting_since``
    is when a waiting run began to wait. ``track`` holds every station of the factory's
    shape, in order, with where the work is. ``history`` lists what happened to the run,
    newest first.
    """

    wait: WaitingView | None = None
    output: str | None = None
    output_format: OutputFormat | None = None
    steps: list[StepView] = []
    answers: list[AnswerView] = []
    job: JobView | None = None
    started_by: str | None = None
    created_at: datetime | None = None
    waiting_since: datetime | None = None
    factory_version: str | None = None
    track: list[TrackStop] = []
    history: list[HistoryEvent] = []


class Conflict(BaseModel):
    """Why a request was refused, and the run as it is now."""

    detail: str
    run: RunDetail


def job_view(job: Job, now: datetime) -> JobView:
    return JobView(
        job_id=job.job_id,
        run_id=job.run_id,
        kind=job.kind,
        state=job.state,
        by=job.by,
        restart_of=job.restart_of,
        refused=job.refused,
        error=job.error,
        created_at=job.created_at,
        updated_at=job.updated_at,
        claimed_until=job.claimed_until,
        stalled=job.stalled(now),
    )
