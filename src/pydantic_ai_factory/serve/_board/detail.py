"""One card in full, as the run panel shows it: what it waits for, its work and history."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from pydantic_ai.messages import ToolCallPart

from ...artifacts import OutputFormat
from ...factory import FactoryShape, Reply, Run
from ...stations import Question, Station
from .history import history, track
from .reading import BoardReader
from .views import (
    AnswerView,
    AskView,
    OptionView,
    RunDetail,
    StepView,
    ToolCallView,
    WaitingView,
    job_view,
)

log = logging.getLogger("pydantic_ai_factory.serve")


def _calls(
    parts: list[ToolCallPart], metadata: dict[str, dict[str, Any]], questions: tuple[Question, ...]
) -> list[ToolCallView]:
    views: list[ToolCallView] = []
    for part in parts:
        args = part.args_as_dict()
        asks = [
            AskView(
                header=asked.header,
                question=asked.text,
                options=[
                    OptionView(label=option.label, description=option.description)
                    for option in asked.options
                ],
                multi_select=asked.multi_select,
            )
            for asked in questions
            if asked.call_id == part.tool_call_id and asked.header is not None
        ]
        question = (
            None
            if asks
            else metadata.get(part.tool_call_id, {}).get("question", args.get("question"))
        )
        views.append(
            ToolCallView(
                id=part.tool_call_id,
                tool=part.tool_name,
                args=args,
                question=None if question is None else str(question),
                asks=asks,
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
        questions=_calls(requests.calls, requests.metadata, waiting.questions) if requests else [],
        sign_offs=_calls(requests.approvals, requests.metadata, ()) if requests else [],
    )


def _render(station: Station[Any, Any] | None, value: Any) -> tuple[str, OutputFormat]:
    """Show ``value`` in its station's format, or as formatted JSON if it cannot be read."""
    if station is not None:
        try:
            return station.show(value), station.output_format or OutputFormat.JSON
        except Exception:
            # An output saved by an earlier shape of the station falls back to the data.
            log.warning("Station %r could not show an output", station.name, exc_info=True)
    return json.dumps(value, indent=2, ensure_ascii=False, default=str), OutputFormat.JSON


def _output(reader: BoardReader, run: Run) -> tuple[str | None, OutputFormat | None]:
    """The work a waiting run offers, or a finished run's output, rendered."""
    if run.waiting is not None and run.waiting.output is not None:
        station = next((s for s in reader.factory.stations if s.name == run.waiting.station), None)
        return _render(station, run.waiting.output)
    if run.finished and run.output is not None:
        return _render(reader.factory.stations[-1], run.output)
    return None, None


async def run_detail(
    reader: BoardReader, shape: FactoryShape, run_id: str, now: datetime
) -> RunDetail | None:
    """The card ``run_id`` in full, or ``None`` when there is no such run or mission."""
    found = await reader.card_of(run_id, now)
    if found is None:
        return None
    card, run, start, jobs, restart = found
    names = [station.name for station in reader.factory.stations]
    if run is None:
        return RunDetail(
            **dict(card),
            job=None if start is None else job_view(start, now),
            started_by=None if start is None else start.by,
            created_at=None if start is None else start.created_at,
            factory_version=shape.version,
            track=track(names, None, card.state),
            history=await history(reader.factory, reader.store, card, None, start, start, restart),
        )
    newest = max(jobs, key=lambda job: job.created_at, default=None)
    output, output_format = _output(reader, run)
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
        job=None if newest is None else job_view(newest, now),
        started_by=None if start is None else start.by,
        created_at=run.created_at,
        waiting_since=run.updated_at if run.waiting is not None else None,
        factory_version=run.factory_version,
        track=track(names, run, card.state),
        history=await history(reader.factory, reader.store, card, run, start, newest, restart),
    )
