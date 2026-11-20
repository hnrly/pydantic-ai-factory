"""The runner: moves one piece of work through a factory's stations and records every attempt."""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, Self, TypeVar, cast

from pydantic import AwareDatetime, Field, model_validator
from pydantic_ai import DeferredToolResults

from .. import _tracing as tracing
from ..artifacts import Artifact, ArtifactRef
from ..contracts import Digest, FactoryError, Name, Record, check_name
from ..stations import Feedback, StationInputInvalid, StationResult, Waiting
from ..stations._station import coerce, recorded_as
from ._answer import Answer, Approve, Changes, Reject, Reply
from ._mission import Mission, Outcome, Signal, signal_artifact
from ._store import RunConflict, Store

if TYPE_CHECKING:
    from ._factory import Factory

RunOutcome = Literal["running", "passed", "failed", "did_not_run", "waiting"]

OnStep = Callable[["Run"], Awaitable[None]]

_A = TypeVar("_A", Approve, Changes, Reject, Reply)


class ApprovalRefused(FactoryError):
    """An answer could not be applied to a run, so nothing changed.

    The run is not waiting, the answer is of the wrong kind, names another wait or
    other work, or the factory's shape has changed since the run stopped.
    ``Factory.check`` says the same thing without raising.
    """


class CannotAbandon(FactoryError):
    """A run could not be abandoned, so nothing changed.

    Only a run that is still ``"running"`` can be abandoned, or a waiting run that a
    factory of the same name but another shape made, since nobody can answer it here.
    """


class Abandoned(Record):
    """Why a run was ended before it could finish, who ended it, and when.

    ``Factory.abandon`` records it on a run whose worker stopped part-way through.
    """

    reason: str
    by: Name
    at: AwareDatetime

    @model_validator(mode="after")
    def _says_why(self) -> Self:
        if not self.reason.strip():
            raise ValueError("an abandoned run needs a reason saying why it was ended")
        return self


class Run(Record):
    """The record of one piece of work moving through a factory.

    ``outcome`` says how the run went: ``"passed"``, ``"failed"``, ``"did_not_run"``, or
    ``"waiting"`` for a person. ``finished`` says whether the work reached the last
    station. ``output`` is the last station's output, once the run has finished;
    ``input_ref`` and ``output_ref`` name the artifacts that hold the input and output.

    ``waiting`` says what a person is asked. Answer with :meth:`approve`,
    :meth:`request_changes` or :meth:`reject`, or, for tool calls, with :meth:`reply`,
    and pass the answer to ``Factory.resume``.

    ``steps`` holds one result per attempt, in order, and ``answers`` every answer a
    person gave, tool replies included. ``revision`` counts the saves, for
    compare-and-swap in the store. A run read back from a store holds its payloads as
    plain JSON data.

    ``station`` is the station the work is at while the run is ``"running"`` or
    ``"waiting"``, and ``None`` once it has ended. The runner saves it before each
    station attempt starts, so a store always says where a running run is. On rework it
    names the station the work went back to. ``stopped_at`` names where a run that did
    not finish stopped, a waiting run included.

    ``abandoned`` is set when ``Factory.abandon`` ended the run, which is then
    ``"did_not_run"``.

    ``mission_id`` is the id of the run's mission, and ``mission_ref`` names the artifact
    that records it. When a run of a mission made by intake ends, ``outcome_signal``
    names the outcome signal it emitted.

    ``created_at`` is when the run was first saved, in UTC. It never changes.

    ``updated_at`` is when the run was last saved, in UTC. A waiting run's
    ``updated_at`` is when it started waiting.
    """

    run_id: Name
    factory: Name
    factory_version: Name
    factory_digest: Digest
    revision: int = 0
    outcome: RunOutcome
    finished: bool
    station: Name | None
    stopped_at: Name | None = None
    input_ref: ArtifactRef
    output_ref: ArtifactRef | None = None
    output: Any = None
    waiting: Waiting | None = None
    steps: tuple[StationResult, ...] = ()
    answers: tuple[Answer, ...] = ()
    reworked: dict[Name, int] = Field(default_factory=lambda: {})
    mission_id: Name | None = None
    mission_ref: ArtifactRef | None = None
    outcome_signal: ArtifactRef | None = None
    abandoned: Abandoned | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def _is_consistent(self) -> Self:
        if (self.outcome == "waiting") != (self.waiting is not None):
            raise ValueError("a run is waiting exactly when it says what it waits for")
        if (self.station is None) != (self.outcome not in ("running", "waiting")):
            raise ValueError("a run names the station it is at exactly while it has not ended")
        if self.waiting is not None and not (
            self.station == self.stopped_at == self.waiting.station
        ):
            raise ValueError("a waiting run is at the station where it stopped and waits")
        if self.abandoned is not None and self.outcome != "did_not_run":
            raise ValueError("an abandoned run did not run")
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

    # Each answer copies the run's id and its wait, so a caller supplies only who
    # answered, what they said and, from a web page, the revision they saw. Leaving
    # ``revision`` out uses the run's own, which is right for code that has just read it.

    def approve(self, *, by: str, note: str | None = None, revision: int | None = None) -> Approve:
        """Approve what this run is waiting on. Pass the answer to ``Factory.resume``."""
        return self._answer(Approve, by=by, revision=revision, note=note)

    def request_changes(self, *, by: str, note: str, revision: int | None = None) -> Changes:
        """Send the waiting work back to its station once, with ``note`` as the feedback."""
        return self._answer(Changes, by=by, revision=revision, note=note)

    def reject(self, *, by: str, note: str, revision: int | None = None) -> Reject:
        """Reject the waiting work. The run ends as failed."""
        return self._answer(Reject, by=by, revision=revision, note=note)

    def reply(self, *, by: str, results: DeferredToolResults, revision: int | None = None) -> Reply:
        """Answer the tool calls this run waits on. Pass the answer to ``Factory.resume``.

        Make ``results`` with ``run.waiting.requests.build_results(...)``. Any call that
        does not already say who answered it is marked as answered ``by`` this person.
        """
        answered = {
            call: by
            for call in (*results.approvals, *results.calls)
            if call not in results.answered_by
        }
        filled = DeferredToolResults(
            calls=dict(results.calls),
            approvals=dict(results.approvals),
            metadata=dict(results.metadata),
            answered_by={**results.answered_by, **answered},
        )
        return self._answer(Reply, by=by, revision=revision, results=filled)

    def _answer(self, kind: type[_A], *, by: str, revision: int | None, **said: Any) -> _A:
        if revision is not None and revision < self.revision:
            # A person answering a page they loaded earlier: say so, even when the run has
            # since stopped waiting, so a web page can tell them someone answered first.
            raise RunConflict(stale_reason(self.run_id, revision, self.revision))
        if revision is not None and revision > self.revision:
            raise ApprovalRefused(ahead_reason(self.run_id, revision, self.revision))
        waiting = self.waiting
        if waiting is None:
            raise ApprovalRefused(
                f"run {self.run_id!r} is not waiting; its outcome is {self.outcome!r}"
            )
        try:
            return kind(
                run_id=self.run_id,
                revision=self.revision if revision is None else revision,
                station=waiting.station,
                waiting=waiting.kind,
                work=waiting.artifact,
                by=by,
                at=datetime.now(UTC),
                **said,
            )
        except ValueError as error:
            raise ApprovalRefused(f"run {self.run_id!r}: {error}") from error


def stale_reason(run_id: str, revision: int, now: int) -> str:
    """Why an answer made at an earlier revision of a run no longer fits it."""
    return (
        f"run {run_id!r} has moved on since revision {revision}: it is "
        f"at revision {now}; load it again with get_run"
    )


def ahead_reason(run_id: str, revision: int, now: int) -> str:
    """Why an answer that names a revision the run has not reached does not fit it."""
    return (
        f"run {run_id!r}: the answer names revision {revision}, but the run is only at "
        f"revision {now}"
    )


def _now() -> datetime:
    """Read the clock, in UTC. Every time on a run comes from here, so tests can patch it."""
    return datetime.now(UTC)


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
    answers: list[Answer] = field(default_factory=lambda: [])
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
        station: str | None = None,
        stopped_at: str | None = None,
        output: Artifact[Any] | None = None,
        waiting: Waiting | None = None,
    ) -> Run:
        """Make the run's next record. A waiting run is at the station where it stopped."""
        now = _now()
        return Run(
            run_id=self.run_id,
            factory=self.factory.name,
            factory_version=self.factory.version,
            factory_digest=self.factory.digest,
            revision=0 if self.saved is None else self.saved.revision + 1,
            outcome=outcome,
            finished=finished,
            station=stopped_at if outcome == "waiting" else station,
            stopped_at=stopped_at,
            input_ref=self.incoming.ref,
            output_ref=output.ref if output else None,
            output=output.payload if output else None,
            waiting=waiting,
            steps=tuple(self.steps),
            answers=tuple(self.answers),
            reworked=dict(self.reworked),
            mission_id=self.mission_id,
            mission_ref=self.mission_ref,
            created_at=now if self.saved is None else self.saved.created_at,
            updated_at=now,
        )

    async def save(self, run: Run) -> Run:
        run = await _emit_outcome(self.store, self.factory.name, self.mission, run)
        await self.store.put_run(run, replaces=self.saved)
        self.saved = run
        if self.on_step is not None:
            await self.on_step(run)
        return run


async def _emit_outcome(store: Store, factory: str, mission: Mission | None, run: Run) -> Run:
    """Store the outcome signal of a run that has ended, if intake made its mission."""
    mission_ref = run.mission_ref
    if (
        mission is None
        or mission_ref is None
        or not mission.signals
        or run.outcome in ("running", "waiting")
    ):
        return run
    outcome = Outcome(
        factory=factory,
        run_id=run.run_id,
        mission_id=mission.id,
        objective=mission.objective,
        mission_ref=mission_ref,
        outcome=run.outcome,
        finished=run.finished,
        output_ref=run.output_ref,
    )
    emitted = await store.put(signal_artifact(Signal(outcome, factory)))
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


# The names an answer has on a trace. They are the words of the span, not stored data.
_TRACED: dict[str, str] = {
    "approve": "approved",
    "changes": "changes_requested",
    "reject": "rejected",
    "reply": "answered",
}


def _trace_answer(run: Run, waiting: Waiting, answer: Answer) -> None:
    """Put the answer a person gave on the trace, as a span of its own.

    It records who answered, but never their note or the text of their reply.
    """
    decision = _TRACED[answer.kind]
    attributes: dict[str, object] = {"factory.answer.by": answer.by}
    if isinstance(answer, Reply):
        # Tool calls are answered as a whole; the answer is about the waiting conversation.
        attributes["factory.answer.artifact"] = (
            waiting.conversation.digest if waiting.conversation else None
        )
    else:
        attributes["factory.answer.artifact"] = answer.work.digest if answer.work else None
        attributes["factory.answer.overrode"] = isinstance(answer, Approve) and answer.overrode
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
    await progress.save(progress.record("running", station=first.name))
    return await _drive(progress, index=0, input=incoming)


async def resume_factory(
    factory: Factory,
    answer: Answer,
    *,
    store: Store,
    deps: Any = None,
    on_step: OnStep | None = None,
) -> Run:
    """Do the work of :meth:`Factory.resume`, traced as one span of its own."""
    with _trace_factory(factory, resumed=True) as span:
        # The mission's objective is added once the mission is read from the store.
        tracing.annotate(span, {"factory.run.id": answer.run_id})
        return _trace_run(
            span,
            await _resume_factory(
                factory, answer, store=store, deps=deps, on_step=on_step, span=span
            ),
        )


async def _resume_factory(
    factory: Factory,
    answer: Answer,
    *,
    store: Store,
    deps: Any,
    on_step: OnStep | None,
    span: tracing.Span,
) -> Run:
    """Load the answer's run, check the answer fits it, then carry the run on."""
    run = await store.get_run(answer.run_id)
    tracing.annotate(span, {"factory.mission.id": run.mission_id})
    if problem := factory.check(run, answer):
        if problem.stale:
            raise RunConflict(problem.reason)
        raise ApprovalRefused(problem.reason)
    waiting = cast(Waiting, run.waiting)
    index = factory.index_of(waiting.station)
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
        saved=run,
        steps=list(run.steps),
        answers=list(run.answers),
        reworked=Counter(run.reworked),
    )
    _trace_mission(span, run.run_id, progress.mission)
    station_input = await store.get(step.input_ref, station.input_type)

    if isinstance(answer, Approve) and waiting.kind == "escalation":
        answer = answer.model_copy(update={"overrode": True})
    progress.answers.append(answer)
    _trace_answer(run, waiting, answer)

    if isinstance(answer, Reply):
        await _claim(progress, station.name)
        return await _drive(
            progress, index=index, input=station_input, resume=step, answer=answer.results
        )
    if isinstance(answer, Reject):
        return await progress.save(progress.record("failed", stopped_at=station.name))
    if isinstance(answer, Changes):
        feedback = Feedback(reasons=(answer.note,), by=answer.by)
        await _claim(progress, station.name)
        return await _drive(progress, index=index, input=station_input, feedback=feedback)
    # check has made sure an approval has work to accept.
    accepted = await store.get(cast(ArtifactRef, answer.work), station.output_type)
    # The work moves on to the next station, or, after the last one, stays to finish there.
    after = factory.stations[index + 1] if index + 1 < len(factory.stations) else station
    await _claim(progress, after.name)
    return await _drive(progress, index=index + 1, input=accepted)


async def abandon_run(factory: Factory, run: Run, *, reason: str, by: str, store: Store) -> Run:
    """Do the work of :meth:`Factory.abandon`."""
    # A waiting run made by this factory under another shape: nobody can answer it here.
    stranded = (
        run.outcome == "waiting"
        and run.factory == factory.name
        and run.factory_digest != factory.digest
    )
    if run.outcome != "running" and not stranded:
        raise CannotAbandon(
            f"run {run.run_id!r} is not running, so it cannot be abandoned; "
            f"its outcome is {run.outcome!r}"
        )
    try:
        abandoned = Abandoned(reason=reason, by=by, at=_now())
        ended = Run.model_validate(
            {
                **dict(run),
                "revision": run.revision + 1,
                "outcome": "did_not_run",
                "station": None,
                "stopped_at": run.station,
                "waiting": None,
                "abandoned": abandoned,
                "updated_at": abandoned.at,
            }
        )
    except ValueError as error:
        raise CannotAbandon(f"run {run.run_id!r}: {error}") from error
    mission = None if run.mission_ref is None else (await store.get(run.mission_ref, Mission))
    ended = await _emit_outcome(
        store, run.factory, None if mission is None else mission.payload, ended
    )
    # A compare-and-swap on the revision that was read, as for every save. A worker still
    # driving the run saves with the old revision afterwards, and gets RunConflict.
    await store.put_run(ended, replaces=run)
    return ended


async def _claim(progress: _Progress, station: str) -> None:
    """Save the answer, and the run as running at ``station``, before any resumed work starts.

    The save is a compare-and-swap on the revision that was waiting, so of two answers
    to the same run only one gets here: the other raises :class:`RunConflict` and runs
    nothing.
    """
    await progress.save(progress.record("running", station=station))


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
        # Saved before the next attempt starts, so the run names the station it goes to.
        await progress.save(
            progress.record("running", station=_next_station(progress, index, result))
        )

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


def _next_station(progress: _Progress, index: int, result: StationResult) -> str:
    """Where the work goes after the station at ``index`` gave ``result``.

    Failed work goes back to its rework target while the limit allows. Failed work that
    halts or escalates stays where it is, and so does work that has passed the last
    station, until the run ends.
    """
    factory = progress.factory
    station = factory.stations[index]
    if result.outcome == "failed":
        rule = factory.rework_for(station.name)
        if rule is not None and progress.reworked[station.name] < rule.limit:
            return rule.target.name
        if rule is None or rule.on_limit != "continue":
            return station.name
    if index + 1 < len(factory.stations):
        return factory.stations[index + 1].name
    return station.name


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
        (answer.station, answer.work) for answer in progress.answers if isinstance(answer, Approve)
    }
    return any(
        step.outcome == "failed" and (name, step.output_ref) not in accepted
        for name, step in last.items()
    )
