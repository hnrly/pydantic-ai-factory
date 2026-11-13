"""The runner: moves one piece of work along a line and records every attempt."""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Sequence
from typing import Any, Literal, cast

from pydantic import BaseModel

from ..artifacts import Artifact, ArtifactRef, ArtifactStore
from ..contracts import MethodologyRef, Name, Record
from ..gates import GateDecision
from ..stations import StationInputInvalid, StationResult, run_station
from ._line import Line

LineOutcome = Literal["passed", "failed", "did_not_run", "needs_approval"]


class LineRun(Record):
    """The record of one piece of work moving along a line.

    ``finished`` says whether the work reached the end of the line. ``outcome`` says how
    it went. They are separate answers: a line can reach its end with a station that
    failed, if that station's rework rule says to carry on.

    ``steps`` holds one result per attempt, in the order they happened.
    """

    run_id: Name
    line: Name
    line_version: Name
    outcome: LineOutcome
    finished: bool
    stopped_at: Name | None = None
    input: ArtifactRef
    output: ArtifactRef | None = None
    steps: tuple[StationResult, ...] = ()
    mission_id: Name | None = None
    methodologies: tuple[MethodologyRef, ...] = ()

    def latest(self, station: str) -> StationResult | None:
        """Return the most recent result for ``station``, or ``None`` if it never ran."""
        return next((step for step in reversed(self.steps) if step.station == station), None)

    def attempts(self, station: str) -> int:
        """Return how many times ``station`` was attempted."""
        return sum(1 for step in self.steps if step.station == station)


async def run_line(
    line: Line,
    input: BaseModel | Artifact[Any],
    *,
    store: ArtifactStore,
    deps: Any = None,
    run_id: str | None = None,
    mission_id: str | None = None,
    methodologies: Sequence[MethodologyRef] = (),
) -> LineRun:
    """Run ``input`` along ``line`` until it finishes or has to stop.

    The line stops early when a station did not run, when a station fails with no rework
    left, or when a gate asks for a person. Rework happens only after a failure. A
    station that did not run is never retried by the line.
    """
    stamps = tuple(methodologies)
    first = line.stations[0]
    incoming: Artifact[Any]
    if isinstance(input, Artifact):
        incoming = cast("Artifact[Any]", input)
    else:
        incoming = Artifact.create(input, mission_id=mission_id, methodologies=stamps)
    if not isinstance(incoming.payload, first.input_type):
        raise StationInputInvalid(
            f"line {line.name!r} takes {first.input_type.__name__}, "
            f"not {type(incoming.payload).__name__}"
        )
    await store.put(incoming)

    identifier = run_id or uuid.uuid4().hex
    steps: list[StationResult] = []
    inputs: dict[int, Artifact[Any]] = {0: incoming}
    attempts: Counter[str] = Counter()
    reworked: Counter[str] = Counter()
    feedback: dict[str, GateDecision] = {}
    any_failed = False

    def record(
        outcome: LineOutcome,
        *,
        finished: bool,
        stopped_at: str | None = None,
        output: ArtifactRef | None = None,
    ) -> LineRun:
        return LineRun(
            run_id=identifier,
            line=line.name,
            line_version=line.version,
            outcome=outcome,
            finished=finished,
            stopped_at=stopped_at,
            input=incoming.ref,
            output=output,
            steps=tuple(steps),
            mission_id=mission_id,
            methodologies=stamps,
        )

    index = 0
    while index < len(line.stations):
        station = line.stations[index]
        attempts[station.name] += 1
        result = await run_station(
            station,
            inputs[index],
            store=store,
            deps=deps,
            run_id=identifier,
            attempt=attempts[station.name],
            mission_id=mission_id,
            methodologies=stamps,
            feedback=feedback.pop(station.name, None),
        )
        steps.append(result)

        if result.outcome in ("did_not_run", "needs_approval"):
            return record(result.outcome, finished=False, stopped_at=station.name)

        if result.outcome == "failed":
            rule = line.rework_for(station.name)
            if rule is not None and reworked[station.name] < rule.limit:
                reworked[station.name] += 1
                if result.decision is not None:
                    feedback[rule.target] = result.decision
                index = line.index_of(rule.target)
                continue
            if rule is None or rule.on_limit == "halt":
                return record("failed", finished=False, stopped_at=station.name)
            any_failed = True

        if result.output is None:  # cannot happen: a station that ran has an output
            return record("did_not_run", finished=False, stopped_at=station.name)
        inputs[index + 1] = await store.get(result.output, station.output_type)
        index += 1

    return record(
        "failed" if any_failed else "passed", finished=True, output=inputs[len(line.stations)].ref
    )
