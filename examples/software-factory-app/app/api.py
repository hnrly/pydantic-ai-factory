"""The web process: a FastAPI app that reads runs and queues jobs. It never needs a model key.

    uvicorn app.api:app

Every route is under ``/api`` and returns JSON. The process never runs the factory: it
reads runs from the store, checks an answer fits the run, and adds a job for the worker.
When the web interface has been built into ``web/dist``, it serves that too.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.models import Model
from pydantic_ai_factory import Factory, Mission, Run, RunNotFound, SQLiteStore
from pydantic_ai_factory.factory import RunOutcome, Store
from pydantic_ai_factory_software import offline_model

from . import factory, jobs, settings
from .jobs import Job, JobNotFound
from .models import Answer, problem_with

WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
"""Where the built web interface lives, once there is one."""

DisplayState = Literal[
    "earlier_version", "waiting", "passed", "failed", "did_not_run", "stalled", "queued", "running"
]
Action = Literal["approve", "changes", "reject", "reply", "start_again"]


class NewRun(BaseModel):
    """A mission to start, and the name of the person starting it."""

    mission: str = Field(min_length=1)
    by: str = Field(min_length=1, max_length=200)

    @field_validator("mission", "by")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class Queued(BaseModel):
    """A job was added. Follow it with ``GET /api/jobs/{job_id}``."""

    run_id: str
    job_id: str


class JobView(Job):
    """A job, and whether it has been claimed for longer than the stalled threshold."""

    stalled: bool


class ToolCallView(BaseModel):
    """One pending tool call: a question an agent asked, or a call that needs sign-off."""

    id: str
    tool: str
    args: dict[str, Any]
    question: str | None = None


class WaitingView(BaseModel):
    """What a waiting run asks of a person."""

    kind: Literal["approval", "escalation", "tool-calls"]
    station: str
    agent: str | None = None
    reasons: list[str]
    offers_work: bool
    questions: list[ToolCallView] = []
    sign_offs: list[ToolCallView] = []


class RunSummary(BaseModel):
    """A run as a list shows it.

    ``state`` is the one display state the interface shows. ``outcome`` is the stored
    run's outcome, or ``None`` when the run exists only as a start job. ``last_change``
    is when anything last happened to it, which is "waiting since" for a waiting run and
    "no change since" for a stalled one. ``actions`` are the answers a person may give.
    """

    run_id: str
    mission: str
    state: DisplayState
    outcome: RunOutcome | None = None
    revision: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_change: datetime | None = None
    waiting: WaitingView | None = None
    actions: list[Action]


class StepView(BaseModel):
    station: str
    attempt: int
    outcome: str
    reason: str | None = None


class DecisionView(BaseModel):
    decision: str
    station: str
    by: str
    note: str | None = None
    at: datetime
    overrode: bool


class RunDetail(RunSummary):
    """One run in full: its steps, decisions, output as Markdown and latest jobs."""

    steps: list[StepView] = []
    approvals: list[DecisionView] = []
    output: str | None = None
    jobs: dict[str, JobView] = {}


class RunList(BaseModel):
    """A page of runs. ``more`` is true when more runs matched than were returned."""

    runs: list[RunSummary]
    more: bool


class Conflict(BaseModel):
    """Why an answer was refused, and the run as it is now."""

    detail: str
    run: RunDetail


def _now() -> datetime:
    return datetime.now(UTC)


def _older_than(moment: datetime | None, limit: timedelta) -> bool:
    return moment is None or _now() - moment > limit


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


def _waiting(run: Run) -> WaitingView | None:
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


def _actions(state: DisplayState, waiting: WaitingView | None) -> list[Action]:
    if state == "stalled":
        return ["start_again"]
    if state != "waiting" or waiting is None:
        return []
    if waiting.kind == "tool-calls":
        return ["reply"]
    if waiting.kind == "escalation" and not waiting.offers_work:
        return ["changes", "reject"]
    return ["approve", "changes", "reject"]


def create_app(
    db: Path,
    *,
    build_factory: Callable[[Model | str], Factory] = factory.build_factory,
    render_output: Callable[[Run, Store], Awaitable[str | None]] = factory.render_output,
    stalled_after: timedelta = settings.STALLED_AFTER,
) -> FastAPI:
    """Build the web app over the SQLite file ``db``.

    ``build_factory`` and ``render_output`` come from ``app/factory.py`` unless given.
    The factory is built once, on the scripted model, only to read its name and digest:
    the digest does not depend on the model, so the web process never needs a key.
    """
    shape = build_factory(offline_model())
    store = SQLiteStore(db)
    app = FastAPI(title="Software factory", summary="Run the software factory as a service.")

    async def mission_of(run: Run) -> str:
        if run.mission_ref is None:
            return ""
        return (await store.get(run.mission_ref, Mission)).payload.objective

    async def summarise(run: Run) -> RunSummary:
        if run.factory_digest != shape.digest:
            state: DisplayState = "earlier_version"
        elif run.outcome != "running":
            state = run.outcome
        elif _older_than(run.updated_at, stalled_after):
            state = "stalled"
        else:
            state = "running"
        waiting = _waiting(run)
        return RunSummary(
            run_id=run.run_id,
            mission=await mission_of(run),
            state=state,
            outcome=run.outcome,
            revision=run.revision,
            created_at=run.created_at,
            updated_at=run.updated_at,
            last_change=run.updated_at,
            waiting=waiting,
            actions=_actions(state, waiting),
        )

    def unsaved(start: Job) -> RunSummary:
        """A run that exists only as its start job, because the worker has not saved it."""
        if start.state == "queued":
            state: DisplayState = "queued"
        elif start.state == "claimed":
            state = "stalled" if _older_than(start.claimed_at, stalled_after) else "running"
        else:
            # The job ended without the run ever being saved: the run could not start.
            state = "failed"
        return RunSummary(
            run_id=start.run_id,
            mission=str(start.body.get("mission", "")),
            state=state,
            last_change=start.claimed_at or start.created_at,
            actions=_actions(state, None),
        )

    def job_view(job: Job) -> JobView:
        stalled = job.state == "claimed" and _older_than(job.claimed_at, stalled_after)
        return JobView(**job.model_dump(), stalled=stalled)

    async def detail(run_id: str) -> RunDetail:
        latest = await asyncio.to_thread(jobs.latest_for_run, db, run_id)
        try:
            run = await store.get_run(run_id)
        except RunNotFound:
            start = latest.get("start")
            if start is None:
                raise HTTPException(404, f"There is no run {run_id!r}.") from None
            return RunDetail(**unsaved(start).model_dump(), jobs={"start": job_view(start)})
        summary = await summarise(run)
        return RunDetail(
            **summary.model_dump(),
            steps=[
                StepView(
                    station=step.station,
                    attempt=step.attempt,
                    outcome=step.outcome,
                    reason=step.reason,
                )
                for step in run.steps
            ],
            approvals=[
                DecisionView(
                    decision=decision.decision,
                    station=decision.station,
                    by=decision.by,
                    note=decision.note,
                    at=decision.at,
                    overrode=decision.overrode,
                )
                for decision in run.approvals
            ],
            output=await render_output(run, store),
            jobs={kind: job_view(job) for kind, job in latest.items()},
        )

    def conflict(message: str, run: RunDetail) -> JSONResponse:
        body = Conflict(detail=message, run=run)
        return JSONResponse(status_code=409, content=body.model_dump(mode="json"))

    @app.post("/api/runs", status_code=202)
    async def start_run(new: NewRun) -> Queued:
        """Queue a new run of a mission. The run's id is known at once."""
        job = await asyncio.to_thread(jobs.add_start, db, new.mission, new.by)
        return Queued(run_id=job.run_id, job_id=job.id)

    @app.get("/api/runs")
    async def list_runs(
        outcome: RunOutcome | None = None,
        order: Literal["newest", "oldest"] = "newest",
        # One less than the store's own limit, so one more can be asked for.
        limit: int = Query(50, ge=1, le=999),
    ) -> RunList:
        """List runs by when they last changed. ``outcome=waiting&order=oldest`` is the inbox."""
        inbox = outcome == "waiting"
        # The inbox leaves out runs of an earlier version, so it reads as many as it may.
        fetched = await store.list_runs(
            outcome=outcome, factory=shape.name, order=order, limit=1000 if inbox else limit + 1
        )
        if inbox:
            fetched = [run for run in fetched if run.factory_digest == shape.digest]
        summaries = [await summarise(run) for run in fetched]
        if outcome is None:
            # Runs the worker has not saved yet exist only as start jobs.
            for start in await asyncio.to_thread(jobs.open_starts, db):
                try:
                    await store.get_run(start.run_id)
                except RunNotFound:
                    summaries.append(unsaved(start))
            # As in the store, a run with no time, saved by 0.1, comes last in either order.
            dated = [run for run in summaries if run.last_change is not None]
            dated.sort(key=lambda run: run.last_change or _now(), reverse=order == "newest")
            summaries = dated + [run for run in summaries if run.last_change is None]
        return RunList(runs=summaries[:limit], more=len(summaries) > limit)

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str) -> RunDetail:
        """One run in full, with its latest job of each kind, failed ones included."""
        return await detail(run_id)

    @app.post(
        "/api/runs/{run_id}/answer",
        status_code=202,
        response_model=Queued,
        responses={409: {"model": Conflict}, 422: {"description": "The answer does not fit"}},
    )
    async def answer_run(run_id: str, answer: Answer) -> Queued | JSONResponse:
        """Check an answer fits the run as the person saw it, then queue it for the worker."""
        current = await detail(run_id)
        if current.state == "earlier_version":
            return conflict(
                "This run was made by an earlier version of the factory, so it cannot be "
                "answered here.",
                current,
            )
        if current.state != "waiting" or current.revision != answer.revision:
            return conflict("Someone answered first. This is the run as it is now.", current)
        pending = current.jobs.get("answer")
        if (
            pending is not None
            and pending.state in ("queued", "claimed")
            and not pending.stalled
            and pending.body.get("revision") == current.revision
        ):
            return conflict("Someone answered first. Their answer is queued.", current)
        run = await store.get_run(run_id)
        if problem := problem_with(answer, run):
            raise HTTPException(422, problem)
        job = await asyncio.to_thread(jobs.add_answer, db, run_id, answer)
        return Queued(run_id=run_id, job_id=job.id)

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str) -> JobView:
        """One job's state, and why it failed if it did."""
        try:
            job = await asyncio.to_thread(jobs.get, db, job_id)
        except JobNotFound as error:
            raise HTTPException(404, str(error)) from error
        return job_view(job)

    if WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    return app


app = create_app(settings.DB)
"""What ``uvicorn app.api:app`` serves."""
