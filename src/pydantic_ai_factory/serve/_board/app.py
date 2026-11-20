"""The board's FastAPI app: its routes, who is asking, and the built interface.

This is the only module that imports FastAPI or Starlette. It never runs the factory:
it builds the factory on a test model only to read its shape and digest and to call
its renderers. Everything else comes from the store and the dispatcher, through
:class:`~.reading.BoardReader`.
"""

from __future__ import annotations

import html
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel

from ...factory import (
    AlreadyRestarted,
    ApprovalRefused,
    CannotAbandon,
    Dispatcher,
    Factory,
    FactoryShape,
    JobNotFound,
    Mission,
    RunConflict,
    Store,
)
from .. import BoardUnsupported
from .._sqlite import utc_now
from . import pages
from .answers import make_answer
from .detail import run_detail
from .reading import BoardReader
from .views import (
    AnswerBody,
    Board,
    CardPage,
    Conflict,
    JobView,
    NewRun,
    Queued,
    RunDetail,
    job_view,
)

STATIC = Path(__file__).resolve().parent.parent / "_static"
"""Where the built board interface lives, once ``npm run build`` in ``web/board`` has run."""

BUILD_COMMAND = "npm --prefix web/board ci && npm --prefix web/board run build"

NAME_HEADER = "X-Factory-Name"
NEEDS_NAME = "Type your name first"

ANSWERED_FIRST = "Someone answered first. This is the run as it is now."

log = logging.getLogger("pydantic_ai_factory.serve")

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


def _missing_page() -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><title>Factory board</title>"
        "<h1>The board's interface has not been built</h1>"
        "<p>The API is running under <code>/api</code>. To build the interface, run "
        f"<code>{html.escape(BUILD_COMMAND)}</code> in the repository, then restart "
        "this process.</p>"
    )


def _checked(build_factory: Callable[[Model | str], Factory]) -> Factory:
    """Build the factory on a test model, and refuse one that takes no mission."""
    factory = build_factory(TestModel())
    first = factory.stations[0]
    if first.input_type is not Mission and first.input_type is not str:
        raise BoardUnsupported(
            f"the board starts runs from a mission, but factory {factory.name!r} starts at "
            f"station {first.name!r}, which takes neither a Mission nor a str"
        )
    return factory


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
    factory = _checked(build_factory)
    shape = factory.shape()
    reader = BoardReader(factory, store, dispatcher)
    name_of: Who = typed_name if who is None else who
    app = FastAPI(title=f"{factory.name} board", summary="Serve a factory as a board.")
    _add_routes(app, reader, shape, name_of)
    _add_interface(app)
    return app


def _add_routes(app: FastAPI, reader: BoardReader, shape: FactoryShape, name_of: Who) -> None:
    factory, store, dispatcher = reader.factory, reader.store, reader.dispatcher

    async def detail(run_id: str) -> RunDetail:
        found = await run_detail(reader, shape, run_id, utc_now())
        if found is None:
            raise HTTPException(404, f"There is no run {run_id!r}.")
        return found

    async def conflict(message: str, run_id: str) -> JSONResponse:
        body = Conflict(detail=message, run=await detail(run_id))
        return JSONResponse(status_code=409, content=body.model_dump(mode="json"))

    def person(request: Request) -> str:
        name = name_of(request)
        if name is None or not name.strip():
            raise HTTPException(400, NEEDS_NAME)
        return name.strip()

    @app.get("/api/board")
    async def board(since: datetime | None = None) -> Board:
        """The factory's shape, and every card that changed since ``since``."""
        now = utc_now()
        if since is None:
            return await pages.full_board(reader, shape, now)
        if since.utcoffset() is None:
            raise HTTPException(422, "since must carry a time zone")
        return await pages.board_since(reader, shape, since, now)

    @app.get("/api/runs")
    async def list_cards(
        column: Literal["done", "stopped", "all"] = "all",
        before: str | None = Query(None, description="updated_at,run_id of the last card"),
    ) -> CardPage:
        """The cards of ``column``, newest change first, 50 at a time."""
        try:
            cursor = pages.parse_cursor(before)
        except pages.BadCursor as error:
            raise HTTPException(422, str(error)) from error
        now = utc_now()
        if column == "done":
            return await pages.done_page(reader, cursor, pages.PAGE, now)
        if column == "stopped":
            return await pages.stopped_page(reader, cursor, pages.PAGE, now)
        return await pages.all_page(reader, cursor, pages.PAGE, now)

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
        run = await reader.saved(run_id)
        if run is None:
            raise HTTPException(404, f"There is no saved run {run_id!r} to answer.")
        try:
            answer = make_answer(run, body, by)
        except RunConflict:
            return await conflict(ANSWERED_FIRST, run_id)
        except (ApprovalRefused, ValueError) as error:
            raise HTTPException(422, str(error)) from error
        problem = factory.check(run, answer)
        if problem is not None and problem.stale:
            return await conflict(ANSWERED_FIRST, run_id)
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
        found = await reader.card_of(run_id, utc_now())
        if found is None:
            raise HTTPException(404, f"There is no run {run_id!r}.")
        card, run, start = found.card, found.run, found.start
        if card.restarted_as is not None:
            return await conflict(
                f"This run was started again already, as run {card.restarted_as}.", run_id
            )
        if "start_again" not in card.actions:
            return await conflict("This card cannot be started again.", run_id)
        mission = start.mission if start is not None else None
        if run is not None:
            mission = await reader.mission_of(run)
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
        return job_view(job, utc_now())


def _add_interface(app: FastAPI) -> None:
    """Serve the built interface at ``/``, or a page that names the build command."""
    if (STATIC / "index.html").is_file():
        app.mount("/", StaticFiles(directory=STATIC, html=True), name="board")
        return
    log.warning("The board's interface has not been built. To build it, run: %s", BUILD_COMMAND)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def missing() -> HTMLResponse:
        return _missing_page()
