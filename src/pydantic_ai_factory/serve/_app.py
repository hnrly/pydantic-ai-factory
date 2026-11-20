"""The board's web process: a FastAPI app that reads runs and jobs, and queues work.

This is the only module that imports FastAPI or Starlette. It never runs the factory:
it builds the factory on a test model only to read its shape and digest and to call
its renderers. Everything else comes from the store and the dispatcher.
"""

from __future__ import annotations

import html
import json
import logging
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator
from pydantic_ai import DeferredToolResults, ToolApproved, ToolDenied
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel

from ..factory import (
    AlreadyRestarted,
    Answer,
    ApprovalRefused,
    CannotAbandon,
    Dispatcher,
    Factory,
    FactoryShape,
    Job,
    JobNotFound,
    JobState,
    Mission,
    Reply,
    Run,
    RunConflict,
    RunNotFound,
    Store,
)
from ..stations import Station
from . import BoardUnsupported
from ._sqlite import utc_now

STATIC = Path(__file__).resolve().parent / "_static"
"""Where the built board interface lives, once ``npm run build`` in ``web/board`` has run."""

BUILD_COMMAND = "npm --prefix web/board ci && npm --prefix web/board run build"

NAME_HEADER = "X-Factory-Name"
NEEDS_NAME = "Type your name first"

PAGE = 50
"""How many cards ``GET /api/runs`` returns at a time."""

SHOWN = 20
"""How many cards Done and Stopped show on a full load of the board."""

OVERLAP = timedelta(seconds=10)
"""How far before a poll's start the next poll looks, so no save committed during it is lost."""

_JOBS_MAX = 1000

_IDS_MAX = 500
"""How many run ids one ``list_jobs`` call names."""

log = logging.getLogger("pydantic_ai_factory.serve")

CardState = Literal[
    "passed", "earlier_version", "waiting", "failed", "did_not_run", "stalled", "queued", "running"
]
Column = Literal["station", "done", "stopped"]
Action = Literal["approve", "changes", "reject", "reply", "start_again"]
Who = Callable[[Request], str | None]


def typed_name(request: Request) -> str | None:
    """Read the name a person typed, from the ``X-Factory-Name`` header.

    The board sends it percent-encoded, because browsers refuse header values outside
    Latin-1, so a name such as "Łukasz" survives. It is a name, not a sign-in: anyone
    can type any name. A missing or blank name is ``None``.
    """
    raw = request.headers.get(NAME_HEADER)
    if raw is None:
        return None
    name = unquote(raw).strip()
    return name or None


# What the API sends and takes.


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
    replies: dict[str, str] | None = None
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

    ``column`` is ``"station"``, with the station in ``station``, or ``"done"`` or
    ``"stopped"``. ``queued`` is true while an answer job is queued, or claimed with a
    claim that has not lapsed. ``needs_you`` is true when the card waits for a person
    and no answer is queued.
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


class ToolCallView(BaseModel):
    """One pending tool call: a question an agent asked, or a call that needs sign-off."""

    id: str
    tool: str
    args: dict[str, Any]
    question: str | None = None


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


class RunDetail(Card):
    """One card in full: what it waits for, its output, its steps, answers and last job.

    ``output`` is the work a waiting run offers, or a finished run's output, rendered by
    its station as Markdown, or as formatted JSON when ``output_format`` says so.
    """

    wait: WaitingView | None = None
    output: str | None = None
    output_format: Literal["markdown", "json"] | None = None
    steps: list[StepView] = []
    answers: list[AnswerView] = []
    job: JobView | None = None


class Conflict(BaseModel):
    """Why a request was refused, and the run as it is now."""

    detail: str
    run: RunDetail


# Reading what a run waits for.


def _calls(parts: list[ToolCallPart], metadata: dict[str, dict[str, Any]]) -> list[ToolCallView]:
    views: list[ToolCallView] = []
    for part in parts:
        args = part.args_as_dict()
        question = metadata.get(part.tool_call_id, {}).get("question", args.get("question"))
        views.append(
            ToolCallView(
                id=part.tool_call_id,
                tool=part.tool_name,
                args=args,
                question=None if question is None else str(question),
            )
        )
    return views


def _wait_view(run: Run) -> WaitingView | None:
    waiting = run.waiting
    if waiting is None:
        return None
    requests = waiting.requests
    return WaitingView(
        kind=waiting.kind,
        station=waiting.station,
        agent=waiting.agent,
        reasons=list(waiting.reasons),
        offers_work=waiting.artifact is not None,
        questions=_calls(requests.calls, requests.metadata) if requests else [],
        sign_offs=_calls(requests.approvals, requests.metadata) if requests else [],
    )


def _answer_actions(run: Run) -> list[Action]:
    waiting = run.waiting
    if waiting is None:
        return []
    if waiting.kind == "tool-calls":
        return ["reply"]
    if waiting.kind == "escalation" and waiting.artifact is None:
        return ["changes", "reject"]
    return ["approve", "changes", "reject"]


def _first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def _key(card: Card) -> tuple[datetime, str]:
    return (card.updated_at, card.run_id)


def _cursor(card: Card) -> str:
    return f"{card.updated_at.isoformat()},{card.run_id}"


def _parse_cursor(before: str | None) -> tuple[datetime, str] | None:
    if before is None:
        return None
    moment, comma, run_id = before.partition(",")
    try:
        when = datetime.fromisoformat(moment)
    except ValueError:
        when = None
    if not comma or not run_id or when is None or when.utcoffset() is None:
        raise HTTPException(
            422, "before must be an updated_at with a time zone, a comma, and a run id"
        )
    return (when, run_id)


def _render(station: Station[Any, Any] | None, value: Any) -> tuple[str, str]:
    """Render ``value`` with the station's ``render``, or as formatted JSON."""
    if station is not None and station.render is not None:
        try:
            loaded = TypeAdapter(station.output_type).validate_python(value)
            return station.render(loaded), "markdown"
        except Exception:
            # A renderer that cannot read this output falls back to the plain data.
            log.warning("Station %r could not render an output", station.name, exc_info=True)
    return json.dumps(value, indent=2, ensure_ascii=False, default=str), "json"


def _missing_page() -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><title>Factory board</title>"
        "<h1>The board's interface has not been built</h1>"
        "<p>The API is running under <code>/api</code>. To build the interface, run "
        f"<code>{html.escape(BUILD_COMMAND)}</code> in the repository, then restart "
        "this process.</p>"
    )


def board_app(
    build_factory: Callable[[Model | str], Factory],
    store: Store,
    dispatcher: Dispatcher,
    *,
    who: Who | None = None,
) -> FastAPI:
    """Serve the factory ``build_factory`` makes as a board, over ``store`` and ``dispatcher``.

    See :func:`pydantic_ai_factory.serve.board_app`.
    """
    factory = build_factory(TestModel())
    first = factory.stations[0]
    if first.input_type is not Mission and first.input_type is not str:
        raise BoardUnsupported(
            f"the board starts runs from a mission, but factory {factory.name!r} starts at "
            f"station {first.name!r}, which takes neither a Mission nor a str"
        )
    shape = factory.shape()
    stations = {station.name: station for station in factory.stations}
    name_of: Who = typed_name if who is None else who
    app = FastAPI(title=f"{factory.name} board", summary="Serve a factory as a board.")

    # Reading the store and the dispatcher.

    async def runs(**filters: Any) -> list[Run]:
        """Every run of this factory that matches, newest change first."""
        found: list[Run] = []
        cursor: dict[str, Any] = {}
        while True:
            page = await store.list_runs(factory=factory.name, limit=1000, **filters, **cursor)
            found.extend(page)
            if len(page) < 1000:
                return found
            last = page[-1]
            cursor = {"updated_before": last.updated_at, "before_run_id": last.run_id}

    async def scan_jobs(
        before: tuple[datetime, str] | None = None, **filters: Any
    ) -> AsyncIterator[Job]:
        """Jobs that match, newest change first, page by page, each once."""
        seen: set[str] = set()
        bound = None if before is None else before[0] + timedelta(microseconds=1)
        while True:
            page = await dispatcher.list_jobs(limit=_JOBS_MAX, updated_before=bound, **filters)
            fresh = [job for job in page if job.job_id not in seen]
            for job in fresh:
                seen.add(job.job_id)
                yield job
            if len(page) < _JOBS_MAX or not fresh:
                return
            bound = page[-1].updated_at + timedelta(microseconds=1)

    async def open_jobs() -> list[Job]:
        """Every queued or claimed job, however many there are."""
        return [job async for job in scan_jobs(state=("queued", "claimed"))]

    async def saved(run_id: str) -> Run | None:
        try:
            run = await store.get_run(run_id)
        except RunNotFound:
            return None
        return run if run.factory == factory.name else None

    async def mission_of(run: Run) -> Mission | None:
        if run.mission_ref is None:
            return None
        payload: Any = (await store.get(run.mission_ref, Mission)).payload
        return payload if isinstance(payload, Mission) else Mission.model_validate(payload)

    async def restarts(run_ids: Iterable[str]) -> dict[str, str]:
        """The new run each of ``run_ids`` was started again as, where it was."""
        ids = sorted(set(run_ids))
        links: dict[str, str] = {}
        for at in range(0, len(ids), _IDS_MAX):
            chunk = ids[at : at + _IDS_MAX]
            for job in await dispatcher.list_jobs(restart_of=chunk, limit=_JOBS_MAX):
                if job.restart_of is not None:
                    links[job.restart_of] = job.run_id
        return links

    async def jobs_for(
        live: Sequence[Run], opened: Sequence[Job] | None = None
    ) -> dict[str, list[Job]]:
        """Each run's jobs that could be driving it, or queue an answer to it.

        Open jobs are always read, every page of them, unless ``opened`` already holds
        them. Each live run's newest job is read too, by its run id, so no number of
        newer jobs for other runs can hide it.
        """
        found: list[Job] = list(await open_jobs() if opened is None else opened)
        ids = sorted({run.run_id for run in live})
        for at in range(0, len(ids), _IDS_MAX):
            found += await dispatcher.list_jobs(
                latest_per_run=True, run_id=ids[at : at + _IDS_MAX], limit=_JOBS_MAX
            )
        by_run: dict[str, dict[str, Job]] = {}
        for job in found:
            if not job.refused:
                by_run.setdefault(job.run_id, {})[job.job_id] = job
        return {run_id: list(jobs.values()) for run_id, jobs in by_run.items()}

    # The card rules.

    def rework(run: Run, at: str) -> ReworkCount | None:
        rules = [rule for rule in factory.rework if rule.target.name == at] or [
            rule for rule in factory.rework if rule.station.name == at
        ]
        for rule in rules:
            count = run.reworked.get(rule.station.name, 0)
            if count:
                return ReworkCount(count=count, limit=rule.limit)
        return None

    def run_card(run: Run, mission: str, jobs: list[Job], now: datetime) -> Card:
        driving = max(jobs, key=lambda job: job.created_at, default=None)
        open_answer = any(
            job.kind == "answer" and job.state in ("queued", "claimed") and not job.stalled(now)
            for job in jobs
        )
        base: dict[str, Any] = {
            "run_id": run.run_id,
            "mission": mission,
            "revision": run.revision,
            "updated_at": run.updated_at,
        }
        # 1. The run passed.
        if run.outcome == "passed":
            return Card(**base, column="done", state="passed")
        # 2. A factory of another shape made the run.
        if run.factory_digest != factory.digest:
            live = run.outcome in ("running", "waiting")
            return Card(
                **base,
                column="stopped",
                state="earlier_version",
                actions=["start_again"] if live else [],
            )
        # 3. The run waits for a person.
        if run.waiting is not None:
            return Card(
                **base,
                column="station",
                station=run.station,
                state="waiting",
                queued=open_answer,
                needs_you=not open_answer,
                waiting=WaitingSummary(
                    kind=run.waiting.kind, station=run.waiting.station, since=run.updated_at
                ),
                rework=rework(run, run.waiting.station),
                actions=_answer_actions(run),
            )
        # 4. The run failed or did not run.
        if run.outcome in ("failed", "did_not_run"):
            reason = (
                run.abandoned.reason
                if run.abandoned
                else (driving.error if driving and driving.error else None)
                or next((step.reason for step in reversed(run.steps) if step.reason), None)
            )
            return Card(
                **base,
                column="stopped",
                state=run.outcome,
                reason=reason,
                actions=["start_again"],
            )
        # 7. The run is running, but the job driving it ended or lapsed.
        if driving is not None and (driving.state in ("done", "failed") or driving.stalled(now)):
            return Card(
                **base,
                column="stopped",
                state="stalled",
                reason=driving.error,
                actions=["start_again"],
            )
        # 8. The run is running.
        return Card(
            **base,
            column="station",
            station=run.station,
            state="running",
            queued=open_answer,
            rework=None if run.station is None else rework(run, run.station),
        )

    def start_card(job: Job, now: datetime) -> Card:
        mission = _first_line(job.mission.objective) if job.mission else ""
        base: dict[str, Any] = {"run_id": job.run_id, "mission": mission}
        # 5. A start job with no saved run ended or lapsed.
        if job.state in ("done", "failed") or job.stalled(now):
            return Card(
                **base,
                column="stopped",
                state="stalled",
                updated_at=job.updated_at,
                reason=job.error,
                actions=["start_again"],
            )
        # 6. A start job has no saved run yet.
        return Card(
            **base,
            column="station",
            station=first.name,
            state="queued",
            queued=True,
            updated_at=job.updated_at,
        )

    async def cards(
        found: Iterable[Run],
        starts: Iterable[Job],
        now: datetime,
        jobs: dict[str, list[Job]] | None = None,
    ) -> list[Card]:
        """Card every run and every start job with no saved run, each run once.

        ``jobs`` is what :func:`jobs_for` returned for the live runs among ``found``, when
        the caller read it already.
        """
        unique: dict[str, Run] = {}
        for run in found:
            if run.factory == factory.name:
                unique.setdefault(run.run_id, run)
        unsaved = {job.run_id: job for job in starts if job.run_id not in unique}
        if jobs is None:
            live = [run for run in unique.values() if run.outcome in ("running", "waiting")]
            jobs = await jobs_for(live)
        made: list[Card] = []
        missions: dict[str, str] = {}
        for run in unique.values():
            key = run.mission_id or ""
            if key not in missions:
                mission = await mission_of(run)
                missions[key] = _first_line(mission.objective) if mission else ""
            made.append(run_card(run, missions[key], jobs.get(run.run_id, []), now))
        made += [start_card(job, now) for job in unsaved.values()]
        links = await restarts(card.run_id for card in made)
        for card in made:
            if card.run_id in links:
                card.restarted_as = links[card.run_id]
                card.actions = [action for action in card.actions if action != "start_again"]
        return made

    async def saved_ids(starts: Sequence[Job], known: set[str]) -> set[str]:
        """The run ids of ``starts`` that have a saved run of this factory.

        Ids in ``known`` are saved. The rest are read together, as the runs saved since
        the oldest of those jobs was queued, never one read per job.
        """
        found = {job.run_id for job in starts if job.run_id in known}
        unknown = [job for job in starts if job.run_id not in known]
        if not unknown:
            return found
        # A start job's run is saved after the job is queued; a second allows for clocks.
        floor = min(job.created_at for job in unknown) - timedelta(seconds=1)
        wanted = {job.run_id for job in unknown}
        return found | {run.run_id for run in await runs(updated_after=floor)} & wanted

    async def unsaved_starts(
        states: Sequence[JobState],
        before: tuple[datetime, str] | None,
        want: int,
        now: datetime,
        *,
        known: set[str],
        oldest: datetime | None = None,
    ) -> list[Job]:
        """Up to ``want`` start jobs in ``states`` whose run was never saved, newest first.

        Only those carded in Stopped when ``"queued"`` is not among ``states``. A start
        job that is done saved its run, so ``states`` never needs ``"done"``. The scan
        stops at jobs last changed before ``oldest``, the oldest card the page can show.
        ``known`` holds run ids already read as saved.
        """
        found: list[Job] = []
        batch: list[Job] = []

        async def keep_unsaved() -> None:
            saved_now = await saved_ids(batch, known)
            found.extend(job for job in batch if job.run_id not in saved_now)
            batch.clear()

        async for job in scan_jobs(before, kind="start", state=tuple(states)):
            if oldest is not None and job.updated_at < oldest:
                break
            if before is not None and (job.updated_at, job.run_id) >= before:
                continue
            if "queued" not in states and job.state == "claimed" and not job.stalled(now):
                continue
            batch.append(job)
            if len(found) + len(batch) >= want:
                await keep_unsaved()
                if len(found) >= want:
                    break
        await keep_unsaved()
        return found[:want]

    def oldest_shown(found: Sequence[Run], size: int) -> datetime | None:
        """When the oldest card a page can show last changed, once ``found`` fills it.

        ``found`` is newest first, and every run in it is carded on the page.
        """
        return found[size - 1].updated_at if len(found) >= size else None

    def cut(found: list[Card], before: tuple[datetime, str] | None, size: int) -> CardPage:
        unique = {card.run_id: card for card in found}
        ordered = sorted(
            (card for card in unique.values() if before is None or _key(card) < before),
            key=_key,
            reverse=True,
        )
        shown = ordered[:size]
        more = len(ordered) > size
        return CardPage(cards=shown, before=_cursor(shown[-1]) if more and shown else None)

    def cursor_filters(before: tuple[datetime, str] | None) -> dict[str, Any]:
        if before is None:
            return {}
        return {"updated_before": before[0], "before_run_id": before[1]}

    async def done_page(before: tuple[datetime, str] | None, size: int, now: datetime) -> CardPage:
        found = await store.list_runs(
            outcome="passed", factory=factory.name, limit=size + 1, **cursor_filters(before)
        )
        # A passed run is carded by rules 1 and 2, which never look at its jobs.
        return cut(await cards(found, (), now, {}), before, size)

    async def stopped_page(
        before: tuple[datetime, str] | None,
        size: int,
        now: datetime,
        live: Sequence[Run] | None = None,
        jobs: dict[str, list[Job]] | None = None,
    ) -> CardPage:
        """A page of Stopped. ``live`` and ``jobs`` are the live runs and their jobs, when
        the caller read them already."""
        # Three sources, each newest change first; the card rules give each one state.
        ended = await store.list_runs(
            outcome=("failed", "did_not_run"),
            factory=factory.name,
            limit=size + 1,
            **cursor_filters(before),
        )
        if live is None:
            live = await runs(outcome=("running", "waiting"))
        starts = await unsaved_starts(
            ("failed", "claimed"),
            before,
            size + 1,
            now,
            known={run.run_id for run in [*ended, *live]},
            oldest=oldest_shown(ended, size),
        )
        found = await cards([*ended, *live], starts, now, jobs)
        return cut([card for card in found if card.column == "stopped"], before, size)

    async def all_page(before: tuple[datetime, str] | None, size: int, now: datetime) -> CardPage:
        found = await store.list_runs(
            factory=factory.name, limit=size + 1, **cursor_filters(before)
        )
        starts = await unsaved_starts(
            ("queued", "claimed", "failed"),
            before,
            size + 1,
            now,
            known={run.run_id for run in found},
            oldest=oldest_shown(found, size),
        )
        return cut(await cards(found, starts, now), before, size)

    async def start_job_of(run_id: str) -> Job | None:
        found = await dispatcher.list_jobs(kind="start", run_id=run_id, limit=1)
        return found[0] if found else None

    async def card_of(run_id: str, now: datetime) -> tuple[Card, Run | None, Job | None]:
        run = await saved(run_id)
        if run is not None:
            return (await cards([run], (), now))[0], run, None
        job = await start_job_of(run_id)
        if job is not None:
            return (await cards((), [job], now))[0], None, job
        raise HTTPException(404, f"There is no run {run_id!r}.")

    async def detail(run_id: str) -> RunDetail:
        now = utc_now()
        card, run, start = await card_of(run_id, now)
        if run is None:
            job = None if start is None else _job_view(start, now)
            return RunDetail(**dict(card), job=job)
        latest = (await jobs_for([run])).get(run.run_id, [])
        newest = max(latest, key=lambda job: job.created_at, default=None)
        output: str | None = None
        output_format: Literal["markdown", "json"] | None = None
        if run.waiting is not None and run.waiting.output is not None:
            text, kind = _render(stations.get(run.waiting.station), run.waiting.output)
            output, output_format = text, "markdown" if kind == "markdown" else "json"
        elif run.finished and run.output is not None:
            text, kind = _render(factory.stations[-1], run.output)
            output, output_format = text, "markdown" if kind == "markdown" else "json"
        return RunDetail(
            **dict(card),
            wait=_wait_view(run),
            output=output,
            output_format=output_format,
            steps=[
                StepView(
                    station=step.station,
                    attempt=step.attempt,
                    outcome=step.outcome,
                    reason=step.reason,
                )
                for step in run.steps
            ],
            answers=[
                AnswerView(
                    kind=answer.kind,
                    by=answer.by,
                    station=answer.station,
                    note=None if isinstance(answer, Reply) else answer.note,
                    at=answer.at,
                )
                for answer in run.answers
            ],
            job=None if newest is None else _job_view(newest, now),
        )

    async def conflict(message: str, run_id: str) -> JSONResponse:
        body = Conflict(detail=message, run=await detail(run_id))
        return JSONResponse(status_code=409, content=body.model_dump(mode="json"))

    def person(request: Request) -> str:
        name = name_of(request)
        if name is None or not name.strip():
            raise HTTPException(400, NEEDS_NAME)
        return name.strip()

    # The routes.

    @app.get("/api/board")
    async def board(since: datetime | None = None) -> Board:
        """The factory's shape, and every card that changed since ``since``."""
        started = utc_now()
        if since is not None and since.utcoffset() is None:
            raise HTTPException(422, "since must carry a time zone")
        live = await runs(outcome=("running", "waiting"))
        opened = await open_jobs()
        if since is None:
            # A full load: every live run, every open start, and a page of Done and Stopped.
            starts = [job for job in opened if job.kind == "start"]
            jobs = await jobs_for(live, opened)
            found = await cards(live, starts, started, jobs)
            done = await done_page(None, SHOWN, started)
            stopped = await stopped_page(None, SHOWN, started, live, jobs)
            merged = {card.run_id: card for card in [*found, *done.cards, *stopped.cards]}
            return Board(
                shape=shape,
                cards=list(merged.values()),
                since=started - OVERLAP,
                more={"done": done.before is not None, "stopped": stopped.before is not None},
            )
        changed = await runs(updated_after=since)
        # Every job that changed, not just each run's newest, so that a restart, which
        # closes the old run's open job, re-cards the old run.
        moved = [job async for job in scan_jobs(updated_after=since)]
        known = {run.run_id for run in [*live, *changed]}
        start_of = {job.run_id: job for job in [*moved, *opened] if job.kind == "start"}
        # A run started again since gets its card back, so it loses "Start again"
        # wherever the board is open, even when nothing about the old run changed.
        old = [job.restart_of for job in moved if job.restart_of is not None]
        extra: list[Run] = []
        starts: list[Job] = []
        for run_id in dict.fromkeys([*(job.run_id for job in [*moved, *opened]), *old]):
            if run_id in known:
                continue
            known.add(run_id)
            run = await saved(run_id)
            start = start_of.get(run_id) or (
                await start_job_of(run_id) if run is None and run_id in old else None
            )
            if run is not None:
                extra.append(run)
            elif start is not None:
                starts.append(start)
        every = [*live, *changed, *extra]
        current = [run for run in every if run.outcome in ("running", "waiting")]
        found = await cards(every, starts, started, await jobs_for(current, opened))
        return Board(shape=shape, cards=found, since=started - OVERLAP)

    @app.get("/api/runs")
    async def list_cards(
        column: Literal["done", "stopped", "all"] = "all",
        before: str | None = Query(None, description="updated_at,run_id of the last card"),
    ) -> CardPage:
        """The cards of ``column``, newest change first, 50 at a time."""
        cursor = _parse_cursor(before)
        now = utc_now()
        if column == "done":
            return await done_page(cursor, PAGE, now)
        if column == "stopped":
            return await stopped_page(cursor, PAGE, now)
        return await all_page(cursor, PAGE, now)

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str) -> RunDetail:
        """One card in full, with its output rendered by its station."""
        return await detail(run_id)

    @app.post("/api/runs", status_code=202)
    async def start_run(new: NewRun, request: Request) -> Queued:
        """Queue a new run of a mission. Its run id is known at once."""
        by = person(request)
        job = await dispatcher.start(Mission(new.mission), by=by)
        return Queued(run_id=job.run_id, job_id=job.job_id)

    @app.post(
        "/api/runs/{run_id}/answer",
        status_code=202,
        response_model=Queued,
        responses={409: {"model": Conflict}, 422: {"description": "The answer does not fit"}},
    )
    async def answer_run(run_id: str, body: AnswerBody, request: Request) -> Queued | JSONResponse:
        """Make the answer from the run as it is, check it, and queue it for the worker."""
        by = person(request)
        run = await saved(run_id)
        if run is None:
            raise HTTPException(404, f"There is no saved run {run_id!r} to answer.")
        try:
            answer = make_answer(run, body, by)
        except RunConflict:
            return await conflict("Someone answered first. This is the run as it is now.", run_id)
        except (ApprovalRefused, ValueError) as error:
            raise HTTPException(422, str(error)) from error
        problem = factory.check(run, answer)
        if problem is not None and problem.stale:
            return await conflict("Someone answered first. This is the run as it is now.", run_id)
        if problem is not None:
            raise HTTPException(422, problem.reason)
        job = await dispatcher.answer(answer)
        return Queued(run_id=run_id, job_id=job.job_id)

    @app.post(
        "/api/runs/{run_id}/restart",
        status_code=202,
        response_model=Queued,
        responses={409: {"model": Conflict}},
    )
    async def restart_run(run_id: str, request: Request) -> Queued | JSONResponse:
        """Start a stopped card's mission again, abandoning its run first if it is stuck."""
        by = person(request)
        now = utc_now()
        card, run, start = await card_of(run_id, now)
        if card.restarted_as is not None:
            return await conflict(
                f"This run was started again already, as run {card.restarted_as}.", run_id
            )
        if "start_again" not in card.actions:
            return await conflict("This card cannot be started again.", run_id)
        mission = start.mission if start is not None else None
        if run is not None:
            mission = await mission_of(run)
        if mission is None:
            raise HTTPException(422, "This run has no mission to start again.")
        if run is not None and run.outcome in ("running", "waiting"):
            try:
                await factory.abandon(run, reason=f"Started again by {by}", by=by, store=store)
            except (RunConflict, CannotAbandon):
                return await conflict("The run has moved on. This is the run as it is now.", run_id)
        try:
            job = await dispatcher.start(mission, by=by, restart_of=run_id)
        except AlreadyRestarted as error:
            return await conflict(str(error), run_id)
        return Queued(run_id=job.run_id, job_id=job.job_id)

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str) -> JobView:
        """One job's state, why it failed if it did, and whether its claim has lapsed."""
        try:
            job = await dispatcher.job(job_id)
        except JobNotFound as error:
            raise HTTPException(404, str(error)) from error
        return _job_view(job, utc_now())

    if (STATIC / "index.html").is_file():
        app.mount("/", StaticFiles(directory=STATIC, html=True), name="board")
    else:
        log.warning("The board's interface has not been built. To build it, run: %s", BUILD_COMMAND)

        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        async def missing() -> HTMLResponse:
            return _missing_page()

    return app


def make_answer(run: Run, body: AnswerBody, by: str) -> Answer:
    """Build the person's answer from the run, so the station, wait and work are the run's.

    Raises ``RunConflict`` when ``body.revision`` is earlier than the run's, and
    ``ApprovalRefused`` or ``ValueError`` when the answer cannot be made.
    """
    if body.kind != "reply":
        if body.replies is not None or body.approvals is not None:
            raise ApprovalRefused(f"{body.kind!r} takes no replies or approvals")
        if body.kind == "approve":
            return run.approve(by=by, note=body.note, revision=body.revision)
        if body.kind == "changes":
            return run.request_changes(by=by, note=body.note or "", revision=body.revision)
        return run.reject(by=by, note=body.note or "", revision=body.revision)
    if body.note is not None:
        raise ApprovalRefused("'reply' takes replies and approvals, not a note")
    if body.revision < run.revision or run.waiting is None or run.waiting.requests is None:
        # The builder says whether the answer is stale or the run waits for something else.
        return run.reply(by=by, results=DeferredToolResults(), revision=body.revision)
    results = run.waiting.requests.build_results(
        calls=dict(body.replies or {}),
        approvals={
            call_id: ToolApproved() if allowed else ToolDenied()
            for call_id, allowed in (body.approvals or {}).items()
        },
    )
    return run.reply(by=by, results=results, revision=body.revision)


def _job_view(job: Job, now: datetime) -> JobView:
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


__all__ = [
    "BUILD_COMMAND",
    "NAME_HEADER",
    "STATIC",
    "AnswerBody",
    "Board",
    "Card",
    "CardPage",
    "RunDetail",
    "board_app",
    "make_answer",
    "typed_name",
]
