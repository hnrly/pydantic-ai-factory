"""The runner: moves one piece of work through a factory's stations and records every attempt."""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, Self, cast

from pydantic import AwareDatetime, Field, model_validator
from pydantic_ai import DeferredToolResults

from .. import _tracing as tracing
from ..artifacts import Artifact, ArtifactRef
from ..contracts import Digest, FactoryError, Name, Record, check_name
from ..stations import Feedback, StationInputInvalid, StationResult, Waiting
from ..stations._station import coerce, recorded_as
from ._mission import Mission, Outcome, Signal, signal_artifact
from ._store import RunConflict, Store

if TYPE_CHECKING:
    from ._factory import Factory

RunOutcome = Literal["running", "passed", "failed", "did_not_run", "waiting"]
DecisionKind = Literal["approved", "changes_requested", "rejected"]

OnStep = Callable[["Run"], Awaitable[None]]


class ApprovalRefused(FactoryError):
    """An answer could not be applied to a run, so nothing changed.

    The run is not waiting, the answer is of the wrong kind, names another artifact, or
    the factory's shape has changed since the run stopped.
    """


class Decision(Record):
    """A person's answer to an approval or an escalation.

    It names the exact artifact it was made on, by digest, so it cannot be carried over
    to revised work. ``overrode`` is true when a person approved work that a gate, or a
    station, had escalated.
    """

    decision: DecisionKind
    station: Name
    waiting: Literal["approval", "escalation"]
    artifact: ArtifactRef | None
    by: Name
    note: str | None = None
    at: AwareDatetime
    overrode: bool = False

    @model_validator(mode="after")
    def _says_why_when_it_must(self) -> Self:
        if self.decision != "approved" and not (self.note and self.note.strip()):
            raise ValueError(f"a decision of {self.decision!r} needs a note saying why")
        return self


class Run(Record):
    """The record of one piece of work moving through a factory.

    ``outcome`` says how the run went: ``"passed"``, ``"failed"``, ``"did_not_run"``, or
    ``"waiting"`` for a person. ``finished`` says whether the work reached the last
    station. ``output`` is the last station's output, once the run has finished;
    ``input_ref`` and ``output_ref`` name the artifacts that hold the input and output.

    ``waiting`` says what a person is asked. Answer with :meth:`approve`,
    :meth:`request_changes` or :meth:`reject`, or, for tool calls, with Pydantic AI's
    ``DeferredToolResults``, and pass the answer to ``Factory.resume``.

    ``steps`` holds one result per attempt, in order, and ``approvals`` every decision a
    person made. ``revision`` counts the saves, for compare-and-swap in the store. A run
    read back from a store holds its payloads as plain JSON data.

    ``mission_id`` is the id of the run's mission, and ``mission_ref`` names the artifact
    that records it. When a run of a mission made by intake ends, ``outcome_signal``
    names the outcome signal it emitted.
    """

    run_id: Name
    factory: Name
    factory_version: Name
    factory_digest: Digest
    revision: int = 0
    outcome: RunOutcome
    finished: bool
    stopped_at: Name | None = None
    input_ref: ArtifactRef
    output_ref: ArtifactRef | None = None
    output: Any = None
    waiting: Waiting | None = None
    steps: tuple[StationResult, ...] = ()
    approvals: tuple[Decision, ...] = ()
    reworked: dict[Name, int] = Field(default_factory=lambda: {})
    mission_id: Name | None = None
    mission_ref: ArtifactRef | None = None
    outcome_signal: ArtifactRef | None = None

    @model_validator(mode="after")
    def _is_consistent(self) -> Self:
        if (self.outcome == "waiting") != (self.waiting is not None):
            raise ValueError("a run is waiting exactly when it says what it waits for")
        if (self.mission_id is None) != (self.mission_ref is None):
            raise ValueError("a run names its mission's id and the mission's artifact together")
        if self.outcome_signal is not None and (
            self.mission_id is None or self.outcome in ("running", "waiting")
        ):
            raise ValueError("only a run of a mission that has ended emits an outcome signal")
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
        return len({step.attempt for step in self.steps if step.station == station})

    def approve(self, *, by: str, note: str | None = None) -> Decision:
        """Approve what this run is waiting on. Pass the result to ``Factory.resume``."""
        return self._decide("approved", by=by, note=note)

    def request_changes(self, *, by: str, note: str) -> Decision:
        """Send the waiting work back to its station once, with ``note`` as the feedback."""
        return self._decide("changes_requested", by=by, note=note)

    def reject(self, *, by: str, note: str) -> Decision:
        """Reject the waiting work. The run ends as failed."""
        return self._decide("rejected", by=by, note=note)

    def _decide(self, decision: DecisionKind, *, by: str, note: str | None) -> Decision:
        waiting = self.waiting
        if waiting is None or waiting.kind == "tool-calls":
            raise ApprovalRefused(
                f"run {self.run_id!r} is not waiting for a decision"
                + ("; answer its tool calls with DeferredToolResults" if waiting else "")
            )
        try:
            return Decision(
                decision=decision,
                station=waiting.station,
                waiting=waiting.kind,
                artifact=waiting.artifact,
                by=by,
                note=note,
                at=datetime.now(UTC),
            )
        except ValueError as error:
            raise ApprovalRefused(f"run {self.run_id!r}: {error}") from error


@dataclass
class _Progress:
    """Everything the runner needs to carry a run on. Rebuilt from a ``Run`` on resume."""

    factory: Factory
    store: Store
    deps: Any
    run_id: str
    incoming: Artifact[Any]
    mission: Mission | None
    mission_ref: ArtifactRef | None
    on_step: OnStep | None
    saved: Run | None = None
    steps: list[StationResult] = field(default_factory=lambda: [])
    approvals: list[Decision] = field(default_factory=lambda: [])
    reworked: Counter[str] = field(default_factory=lambda: Counter[str]())

    def attempts(self, station: str) -> int:
        return len({step.attempt for step in self.steps if step.station == station})

    def conversations(self, station: str) -> dict[str, ArtifactRef]:
        kept: dict[str, ArtifactRef] = {}
        for step in self.steps:
            if step.station == station:
                kept.update(step.conversations)
        return kept

    @property
    def mission_id(self) -> str | None:
        return None if self.mission is None else self.mission.id

    def record(
        self,
        outcome: RunOutcome,
        *,
        finished: bool = False,
        stopped_at: str | None = None,
        output: Artifact[Any] | None = None,
        waiting: Waiting | None = None,
    ) -> Run:
        return Run(
            run_id=self.run_id,
            factory=self.factory.name,
            factory_version=self.factory.version,
            factory_digest=self.factory.digest,
            revision=0 if self.saved is None else self.saved.revision + 1,
            outcome=outcome,
            finished=finished,
            stopped_at=stopped_at,
            input_ref=self.incoming.ref,
            output_ref=output.ref if output else None,
            output=output.payload if output else None,
            waiting=waiting,
            steps=tuple(self.steps),
            approvals=tuple(self.approvals),
            reworked=dict(self.reworked),
            mission_id=self.mission_id,
            mission_ref=self.mission_ref,
        )

    async def save(self, run: Run) -> Run:
        run = await self._emit_outcome(run)
        await self.store.put_run(run, replaces=self.saved)
        self.saved = run
        if self.on_step is not None:
            await self.on_step(run)
        return run

    async def _emit_outcome(self, run: Run) -> Run:
        """Store the outcome signal of a run that has ended, if intake made its mission."""
        mission, mission_ref = self.mission, self.mission_ref
        if (
            mission is None
            or mission_ref is None
            or not mission.signals
            or run.outcome in ("running", "waiting")
        ):
            return run
        outcome = Outcome(
            factory=self.factory.name,
            run_id=run.run_id,
            mission_id=mission.id,
            objective=mission.objective,
            mission_ref=mission_ref,
            outcome=run.outcome,
            finished=run.finished,
            output_ref=run.output_ref,
        )
        emitted = await self.store.put(signal_artifact(Signal(outcome, self.factory.name)))
        return run.model_copy(update={"outcome_signal": emitted})


def _trace_factory(factory: Factory, resumed: bool = False) -> AbstractContextManager[tracing.Span]:
    """Open the span for one call to ``Factory.run`` or ``Factory.resume``."""
    return tracing.span(
        f"factory {factory.name}",
        {
            "factory.name": factory.name,
            "factory.version": factory.version,
            "factory.resumed": True if resumed else None,
        },
    )


def _trace_mission(span: tracing.Span, run_id: str, mission: Mission | None) -> None:
    """Put the run's id and its mission on the factory's span, as soon as they are known."""
    tracing.annotate(
        span,
        {
            "factory.run.id": run_id,
            "factory.mission.id": mission.id if mission else None,
            "factory.mission.objective": mission.objective if mission else None,
        },
    )


def _trace_run(span: tracing.Span, run: Run) -> Run:
    """Put how the call left the run on the factory's span, and hand the run back."""
    waiting = run.waiting
    tracing.annotate(
        span,
        {
            "factory.outcome": run.outcome,
            "factory.finished": run.finished,
            # The run has ended when nothing more can happen to it: it is not still
            # running and not waiting for a person. A waiting run ends this call, and
            # this trace, but not the run.
            "factory.ended": run.outcome not in ("running", "waiting"),
            "factory.stopped_at": run.stopped_at,
            "factory.attempts": len({(step.station, step.attempt) for step in run.steps}),
            "factory.output": run.output_ref.digest if run.output_ref else None,
            "factory.waiting.kind": waiting.kind if waiting else None,
            "factory.waiting.artifact": (
                waiting.artifact.digest if waiting and waiting.artifact else None
            ),
        },
    )
    if run.outcome == "did_not_run":
        tracing.mark_error(span, f"stopped at {run.stopped_at!r}: a station did not run")
    return run


def _trace_answer(run: Run, waiting: Waiting, answer: Decision | DeferredToolResults) -> None:
    """Put the answer a person gave on the trace, as a span of its own.

    It records who answered, but never their note or the text of their reply.
    """
    if isinstance(answer, Decision):
        decision: str = answer.decision
        attributes: dict[str, object] = {
            "factory.answer.by": answer.by,
            "factory.answer.artifact": answer.artifact.digest if answer.artifact else None,
            "factory.answer.overrode": answer.overrode,
        }
    else:
        # Tool calls are answered as a whole; the answer is about the waiting conversation.
        decision = "answered"
        attributes = {
            "factory.answer.artifact": (
                waiting.conversation.digest if waiting.conversation else None
            ),
        }
    with tracing.span(
        f"answer {waiting.kind} {decision}",
        {
            "factory.answer.kind": waiting.kind,
            "factory.answer.decision": decision,
            **attributes,
            "factory.station.name": waiting.station,
            "factory.run.id": run.run_id,
        },
    ):
        pass  # an answer takes no time; the span puts it on the trace's timeline


async def run_factory(
    factory: Factory,
    input: Any,
    *,
    store: Store,
    deps: Any = None,
    run_id: str | None = None,
    on_step: OnStep | None = None,
) -> Run:
    """Do the work of :meth:`Factory.run`, traced as one span."""
    with _trace_factory(factory) as span:
        return _trace_run(
            span,
            await _run_factory(
                factory, input, store=store, deps=deps, run_id=run_id, on_step=on_step, span=span
            ),
        )


async def _run_factory(
    factory: Factory,
    input: Any,
    *,
    store: Store,
    deps: Any,
    run_id: str | None,
    on_step: OnStep | None,
    span: tracing.Span,
) -> Run:
    """Start a run: record its input and mission, then drive it."""
    first = factory.stations[0]
    given: object = input
    payload: object = cast("Artifact[Any]", given).payload if isinstance(given, Artifact) else given
    takes_text = first.input_type is str
    mission: Mission | None = None
    try:
        if isinstance(payload, Mission):
            mission = payload
        elif (
            isinstance(payload, str)
            and not isinstance(given, Artifact)
            and (first.input_type is Mission or takes_text)
        ):
            mission = Mission(payload)
        if mission is not None and not isinstance(given, Artifact):
            # A station that takes text is given the mission as its prompt.
            payload = mission.as_prompt() if takes_text else mission
        payload = coerce(first.input_type, payload)
    except ValueError as error:
        raise StationInputInvalid(f"factory {factory.name!r} {error}") from error
    identifier = uuid.uuid4().hex if run_id is None else run_id
    if problem := check_name(identifier):
        raise StationInputInvalid(f"factory {factory.name!r}: run_id {problem}")
    _trace_mission(span, identifier, mission)

    incoming: Artifact[Any]
    recorded: Artifact[Mission] | None = None
    if mission is not None:
        recorded = (
            cast("Artifact[Mission]", given)
            if isinstance(given, Artifact)
            else Artifact.create(mission, inputs=mission.signals, mission_id=mission.id)
        )
        await store.put(recorded)
    if isinstance(given, Artifact):
        incoming = cast("Artifact[Any]", given)
    elif recorded is not None and takes_text:
        incoming = Artifact.create(
            payload,
            payload_type=str,
            inputs=[recorded.ref],
            mission_id=recorded.payload.id,
        )
    elif recorded is not None:
        incoming = recorded
    else:
        incoming = Artifact.create(payload, payload_type=recorded_as(first.input_type, payload))
    await store.put(incoming)

    progress = _Progress(
        factory=factory,
        store=store,
        deps=deps,
        run_id=identifier,
        incoming=incoming,
        mission=mission,
        mission_ref=None if recorded is None else recorded.ref,
        on_step=on_step,
    )
    await progress.save(progress.record("running"))
    return await _drive(progress, index=0, input=incoming)


async def resume_factory(
    factory: Factory,
    run: Run,
    answer: Decision | DeferredToolResults,
    *,
    store: Store,
    deps: Any = None,
    on_step: OnStep | None = None,
) -> Run:
    """Do the work of :meth:`Factory.resume`, traced as one span of its own."""
    with _trace_factory(factory, resumed=True) as span:
        # The mission's objective is added once the mission is read from the store.
        tracing.annotate(span, {"factory.run.id": run.run_id, "factory.mission.id": run.mission_id})
        return _trace_run(
            span,
            await _resume_factory(
                factory, run, answer, store=store, deps=deps, on_step=on_step, span=span
            ),
        )


async def _resume_factory(
    factory: Factory,
    run: Run,
    answer: Decision | DeferredToolResults,
    *,
    store: Store,
    deps: Any,
    on_step: OnStep | None,
    span: tracing.Span,
) -> Run:
    """Check the answer fits the waiting run, then carry the run on."""
    waiting = run.waiting
    if waiting is None or run.stopped_at is None:
        raise ApprovalRefused(f"run {run.run_id!r} is not waiting; its outcome is {run.outcome!r}")
    if run.factory_digest != factory.digest:
        raise ApprovalRefused(
            f"run {run.run_id!r} was made by a factory of another shape; "
            "resume it on the factory that started it"
        )
    stored = await store.get_run(run.run_id)
    if stored.revision != run.revision:
        raise RunConflict(
            f"run {run.run_id!r} has moved on since it was read; load it again with get_run"
        )
    index = factory.index_of(run.stopped_at)
    station = factory.stations[index]
    step = run.steps[-1]
    first = factory.stations[0]
    incoming = await store.get(run.input_ref, first.input_type)
    progress = _Progress(
        factory=factory,
        store=store,
        deps=deps,
        run_id=run.run_id,
        incoming=incoming,
        mission=(
            None if run.mission_ref is None else (await store.get(run.mission_ref, Mission)).payload
        ),
        mission_ref=run.mission_ref,
        on_step=on_step,
        saved=stored,
        steps=list(run.steps),
        approvals=list(run.approvals),
        reworked=Counter(run.reworked),
    )
    _trace_mission(span, run.run_id, progress.mission)
    station_input = await store.get(step.input_ref, station.input_type)

    if isinstance(answer, DeferredToolResults):
        if waiting.kind != "tool-calls":
            raise ApprovalRefused(
                f"run {run.run_id!r} waits for a decision; use run.approve, "
                "run.request_changes or run.reject"
            )
        _trace_answer(run, waiting, answer)
        await _claim(progress)
        return await _drive(progress, index=index, input=station_input, resume=step, answer=answer)

    if not isinstance(answer, Decision):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise ApprovalRefused(f"cannot resume with {type(answer).__name__}")
    if waiting.kind == "tool-calls":
        raise ApprovalRefused(
            f"run {run.run_id!r} waits on tool calls; answer them with DeferredToolResults"
        )
    if (answer.station, answer.waiting, answer.artifact) != (
        waiting.station,
        waiting.kind,
        waiting.artifact,
    ):
        raise ApprovalRefused(
            f"the decision was made on {answer.artifact} at {answer.station!r}, "
            f"but the run waits on {waiting.artifact} at {waiting.station!r}"
        )
    decided = answer.model_copy(
        update={"overrode": answer.decision == "approved" and waiting.kind == "escalation"}
    )
    progress.approvals.append(decided)
    _trace_answer(run, waiting, decided)

    if decided.decision == "rejected":
        return await progress.save(progress.record("failed", stopped_at=station.name))
    if decided.decision == "changes_requested":
        feedback = Feedback(reasons=(cast(str, decided.note),), by=decided.by)
        await _claim(progress)
        return await _drive(progress, index=index, input=station_input, feedback=feedback)
    if waiting.artifact is None:
        raise ApprovalRefused(
            f"station {station.name!r} offered no work to accept; ask for changes or reject"
        )
    accepted = await store.get(waiting.artifact, station.output_type)
    await _claim(progress)
    return await _drive(progress, index=index + 1, input=accepted)


async def _claim(progress: _Progress) -> None:
    """Save the answer, and the run as running, before any resumed work starts.

    The save is a compare-and-swap on the revision that was waiting, so of two answers
    to the same run only one gets here: the other raises :class:`RunConflict` and runs
    nothing.
    """
    await progress.save(progress.record("running"))


async def _drive(
    progress: _Progress,
    *,
    index: int,
    input: Artifact[Any],
    feedback: Feedback | None = None,
    resume: StationResult | None = None,
    answer: DeferredToolResults | None = None,
) -> Run:
    """Run stations from ``index`` until the work finishes or has to stop."""
    factory, store = progress.factory, progress.store
    current = input
    while index < len(factory.stations):
        station = factory.stations[index]
        attempt = resume.attempt if resume is not None else progress.attempts(station.name) + 1
        result = await station.run(
            current,
            store=store,
            deps=progress.deps,
            run_id=progress.run_id,
            attempt=attempt,
            mission_id=progress.mission_id,
            feedback=resume.feedback if resume is not None else feedback,
            conversations=progress.conversations(station.name) if resume is None else None,
            resume=resume,
            answer=answer,
        )
        feedback, resume, answer = None, None, None
        progress.steps.append(result)

        if result.outcome == "did_not_run":
            return await progress.save(progress.record("did_not_run", stopped_at=station.name))
        if result.outcome == "waiting":
            return await progress.save(
                progress.record("waiting", stopped_at=station.name, waiting=result.waiting)
            )
        await progress.save(progress.record("running"))

        if result.outcome == "failed":
            rule = factory.rework_for(station.name)
            if rule is not None and progress.reworked[station.name] < rule.limit:
                progress.reworked[station.name] += 1
                # The target is the very next station to run, so it alone gets the reasons.
                if result.decision is not None:
                    feedback = Feedback(reasons=result.decision.reasons)
                index = factory.index_of(rule.target.name)
                current = await store.get(_input_of(progress, index), rule.target.input_type)
                continue
            if rule is not None and rule.on_limit == "escalate" and result.output_ref:
                failed_output = await store.get(result.output_ref, station.output_type)
                reasons = result.decision.reasons if result.decision else ()
                return await progress.save(
                    progress.record(
                        "waiting",
                        stopped_at=station.name,
                        waiting=Waiting(
                            kind="escalation",
                            station=station.name,
                            artifact=failed_output.ref,
                            output=failed_output.payload,
                            reasons=("rework ran out", *reasons),
                        ),
                    )
                )
            if rule is None or rule.on_limit != "continue":
                return await progress.save(progress.record("failed", stopped_at=station.name))

        if result.output_ref is None:  # cannot happen: a station that ran has an output
            return await progress.save(progress.record("did_not_run", stopped_at=station.name))
        current = await store.get(result.output_ref, station.output_type)
        index += 1

    return await progress.save(
        progress.record(
            "failed" if _any_failed(progress) else "passed", finished=True, output=current
        )
    )


def _input_of(progress: _Progress, index: int) -> ArtifactRef:
    """The input the station at ``index`` last received."""
    name = progress.factory.stations[index].name
    step = next(step for step in reversed(progress.steps) if step.station == name)
    return step.input_ref


def _any_failed(progress: _Progress) -> bool:
    """Whether any station's last attempt failed and no person accepted it anyway.

    The run fails then, even though the work carried on to the end.
    """
    last = {step.station: step for step in progress.steps}
    accepted = {
        (decision.station, decision.artifact)
        for decision in progress.approvals
        if decision.decision == "approved"
    }
    return any(
        step.outcome == "failed" and (name, step.output_ref) not in accepted
        for name, step in last.items()
    )
