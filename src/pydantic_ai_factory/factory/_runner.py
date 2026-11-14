"""The runner: moves one piece of work through a factory's stations and records every attempt."""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any, Literal, Self, cast

from pydantic import model_validator

from ..artifacts import Artifact, ArtifactRef, ArtifactStore
from ..contracts import Digest, MethodologyRef, Name, Record, check_name
from ..stations import Feedback, StationInputInvalid, StationResult
from ..stations._station import coerce, recorded_as

if TYPE_CHECKING:
    from ._factory import Factory

RunOutcome = Literal["running", "passed", "failed", "did_not_run", "needs_approval"]

OnStep = Callable[["Run"], Awaitable[None]]


class Run(Record):
    """The record of one piece of work moving through a factory.

    ``finished`` says whether the work reached the last station. ``outcome`` says how it
    went. They are separate answers: work can reach the end with a station whose last
    attempt failed, if that station's rework rule says to carry on.

    ``output`` is the last station's output, once the run has finished. ``input_ref`` and
    ``output_ref`` name the artifacts that hold the input and the output.

    ``steps`` holds one result per attempt, in the order they happened. Together with
    ``factory_digest`` and the artifact store, that is everything needed to see where a run
    stopped and to carry it on.
    """

    run_id: Name
    factory: Name
    factory_version: Name
    factory_digest: Digest
    outcome: RunOutcome
    finished: bool
    stopped_at: Name | None = None
    input_ref: ArtifactRef
    output_ref: ArtifactRef | None = None
    output: Any = None
    steps: tuple[StationResult, ...] = ()
    mission_id: Name | None = None
    methodologies: tuple[MethodologyRef, ...] = ()

    @model_validator(mode="after")
    def _is_consistent(self) -> Self:
        if self.finished:
            if self.outcome not in ("passed", "failed"):
                raise ValueError("a finished run either passed or failed")
            if self.stopped_at is not None or self.output_ref is None:
                raise ValueError("a finished run has an output and did not stop early")
        else:
            if self.outcome == "passed":
                raise ValueError("a run that passed must have finished")
            if self.output_ref is not None:
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


async def run_factory(
    factory: Factory,
    input: Any,
    *,
    store: ArtifactStore,
    deps: Any = None,
    run_id: str | None = None,
    mission_id: str | None = None,
    methodologies: Sequence[MethodologyRef] = (),
    on_step: OnStep | None = None,
) -> Run:
    """Do the work of :meth:`Factory.run`."""
    stamps = tuple(methodologies)
    first = factory.stations[0]
    given: object = input
    payload: object = cast("Artifact[Any]", given).payload if isinstance(given, Artifact) else given
    try:
        payload = coerce(first.input_type, payload)
    except ValueError as error:
        raise StationInputInvalid(f"factory {factory.name!r} {error}") from error
    identifier = uuid.uuid4().hex if run_id is None else run_id
    for label, value in (("run_id", identifier), ("mission_id", mission_id)):
        if value is not None and (problem := check_name(value)):
            raise StationInputInvalid(f"factory {factory.name!r}: {label} {problem}")

    incoming: Artifact[Any]
    if isinstance(given, Artifact):
        incoming = cast("Artifact[Any]", given)
    else:
        incoming = Artifact.create(
            payload,
            payload_type=recorded_as(first.input_type, payload),
            mission_id=mission_id,
            methodologies=stamps,
        )
    await store.put(incoming)

    steps: list[StationResult] = []
    inputs: dict[int, Artifact[Any]] = {0: incoming}
    attempts: Counter[str] = Counter()
    reworked: Counter[str] = Counter()

    def record(
        outcome: RunOutcome,
        *,
        finished: bool = False,
        stopped_at: str | None = None,
        output: Artifact[Any] | None = None,
    ) -> Run:
        return Run(
            run_id=identifier,
            factory=factory.name,
            factory_version=factory.version,
            factory_digest=factory.digest,
            outcome=outcome,
            finished=finished,
            stopped_at=stopped_at,
            input_ref=incoming.ref,
            output_ref=output.ref if output else None,
            output=output.payload if output else None,
            steps=tuple(steps),
            mission_id=mission_id,
            methodologies=stamps,
        )

    index = 0
    feedback: Feedback | None = None
    # The latest conversations of each station's agents, carried on at a later attempt.
    conversations: dict[str, dict[str, ArtifactRef]] = {}
    while index < len(factory.stations):
        station = factory.stations[index]
        attempts[station.name] += 1
        result = await station.run(
            inputs[index],
            store=store,
            deps=deps,
            run_id=identifier,
            attempt=attempts[station.name],
            mission_id=mission_id,
            methodologies=stamps,
            feedback=feedback,
            conversations=conversations.get(station.name),
        )
        feedback = None
        conversations[station.name] = {
            **conversations.get(station.name, {}),
            **result.conversations,
        }
        steps.append(result)
        if on_step is not None:
            await on_step(record("running"))

        if result.outcome in ("did_not_run", "needs_approval"):
            return record(result.outcome, stopped_at=station.name)

        if result.outcome == "failed":
            rule = factory.rework_for(station.name)
            if rule is not None and reworked[station.name] < rule.limit:
                reworked[station.name] += 1
                # The target is the very next station to run, so it alone gets the reasons.
                if result.decision is not None:
                    feedback = Feedback(reasons=result.decision.reasons)
                index = factory.index_of(rule.target.name)
                continue
            if rule is None or rule.on_limit != "continue":
                return record("failed", stopped_at=station.name)

        if result.output_ref is None:  # cannot happen: a station that ran has an output
            return record("did_not_run", stopped_at=station.name)
        inputs[index + 1] = await store.get(result.output_ref, station.output_type)
        index += 1

    # The run failed if any station's last attempt failed, even though the work carried on.
    last = {step.station: step.outcome for step in steps}
    return record(
        "failed" if "failed" in last.values() else "passed",
        finished=True,
        output=inputs[len(factory.stations)],
    )
