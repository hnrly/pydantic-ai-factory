"""The runner: moves one piece of work along a line and records every attempt."""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal, Self, cast

from pydantic import BaseModel, model_validator

from ..artifacts import Artifact, ArtifactRef, ArtifactStore
from ..contracts import Digest, MethodologyRef, Name, Record, check_name
from ..stations import StationInputInvalid, StationResult, run_station
from ._line import Line

LineOutcome = Literal["running", "passed", "failed", "did_not_run", "needs_approval"]

OnStep = Callable[["LineRun"], Awaitable[None]]


class LineRun(Record):
    """The record of one piece of work moving along a line.

    ``finished`` says whether the work reached the end of the line. ``outcome`` says how
    it went. They are separate answers: a line can reach its end with a station whose
    last attempt failed, if that station's rework rule says to carry on.

    ``steps`` holds one result per attempt, in the order they happened. Together with
    ``line_digest`` and the artifact store, that is everything needed to see where a run
    stopped and to carry it on.
    """

    run_id: Name
    line: Name
    line_version: Name
    line_digest: Digest
    outcome: LineOutcome
    finished: bool
    stopped_at: Name | None = None
    input: ArtifactRef
    output: ArtifactRef | None = None
    steps: tuple[StationResult, ...] = ()
    mission_id: Name | None = None
    methodologies: tuple[MethodologyRef, ...] = ()

    @model_validator(mode="after")
    def _is_consistent(self) -> Self:
        if self.finished:
            if self.outcome not in ("passed", "failed"):
                raise ValueError("a finished run either passed or failed")
            if self.stopped_at is not None or self.output is None:
                raise ValueError("a finished run has an output and did not stop early")
        else:
            if self.outcome == "passed":
                raise ValueError("a run that passed must have finished")
            if self.output is not None:
                raise ValueError("a run that has not finished has no output yet")
            if (self.stopped_at is None) != (self.outcome == "running"):
                raise ValueError("a run names where it stopped unless it is still running")
        return self

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
    on_step: OnStep | None = None,
) -> LineRun:
    """Run ``input`` along ``line`` until it finishes or has to stop.

    The line stops early when a station did not run, when a station fails with no rework
    left, or when a gate asks for a person. Rework happens only after a failure. A
    station that did not run is never retried by the line.

    ``on_step`` is awaited after every attempt with the record so far, whose outcome is
    ``"running"``. Use it to save progress: if the store raises, or the run is cancelled,
    no record is returned.
    """
    stamps = tuple(methodologies)
    first = line.stations[0]
    given: object = input
    payload: object = cast("Artifact[Any]", given).payload if isinstance(given, Artifact) else given
    if not isinstance(payload, first.input_type):
        raise StationInputInvalid(
            f"line {line.name!r} takes {first.input_type.__name__}, not {type(payload).__name__}"
        )
    identifier = uuid.uuid4().hex if run_id is None else run_id
    for label, value in (("run_id", identifier), ("mission_id", mission_id)):
        if value is not None and (problem := check_name(value)):
            raise StationInputInvalid(f"line {line.name!r}: {label} {problem}")

    incoming: Artifact[Any]
    if isinstance(given, Artifact):
        incoming = cast("Artifact[Any]", given)
    else:
        incoming = Artifact.create(payload, mission_id=mission_id, methodologies=stamps)
    await store.put(incoming)

    steps: list[StationResult] = []
    inputs: dict[int, Artifact[Any]] = {0: incoming}
    attempts: Counter[str] = Counter()
    reworked: Counter[str] = Counter()

    def record(
        outcome: LineOutcome,
        *,
        finished: bool = False,
        stopped_at: str | None = None,
        output: ArtifactRef | None = None,
    ) -> LineRun:
        return LineRun(
            run_id=identifier,
            line=line.name,
            line_version=line.version,
            line_digest=line.digest,
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
    feedback = None
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
            feedback=feedback,
        )
        feedback = None
        steps.append(result)
        if on_step is not None:
            await on_step(record("running"))

        if result.outcome in ("did_not_run", "needs_approval"):
            return record(result.outcome, stopped_at=station.name)

        if result.outcome == "failed":
            rule = line.rework_for(station.name)
            if rule is not None and reworked[station.name] < rule.limit:
                reworked[station.name] += 1
                # The target is the very next station to run, so it alone gets the reasons.
                feedback = result.decision
                index = line.index_of(rule.target)
                continue
            if rule is None or rule.on_limit != "continue":
                return record("failed", stopped_at=station.name)

        if result.output is None:  # cannot happen: a station that ran has an output
            return record("did_not_run", stopped_at=station.name)
        inputs[index + 1] = await store.get(result.output, station.output_type)
        index += 1

    # The run failed if any station's last attempt failed, even though the work carried on.
    last = {step.station: step.outcome for step in steps}
    return record(
        "failed" if "failed" in last.values() else "passed",
        finished=True,
        output=inputs[len(line.stations)].ref,
    )
