"""Stations: one bounded job with a typed input, a typed output and a gate."""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Generic, Literal, TypeVar, cast, overload

from pydantic import BaseModel, Field
from typing_extensions import TypeVar as TypeVarWithDefault

from ..artifacts import Artifact, ArtifactRef, ArtifactStore, Producer
from ..contracts import FactoryError, MethodologyRef, Name, Record
from ..gates import Evidence, Gate, GateDecision, NotEvaluated, evaluate_gate

InT = TypeVar("InT", bound=BaseModel)
OutT = TypeVar("OutT", bound=BaseModel)
RecordedT = TypeVar("RecordedT", bound=BaseModel)
DepsT = TypeVarWithDefault("DepsT", default=None)

Outcome = Literal["passed", "failed", "needs_approval", "not_judged", "did_not_run"]


class StationDefinitionError(FactoryError):
    """A station was declared in a way that cannot work."""


class StationInputInvalid(FactoryError):
    """A station was given an input that is not of its input type."""


@dataclass
class StationContext(Generic[DepsT]):
    """What a station's code is given when it runs.

    ``deps`` is whatever the caller passed in: clients, settings, an agent. ``feedback``
    is the gate decision that failed the previous attempt, when this run is rework.
    """

    deps: DepsT
    station: str
    station_version: str
    run_id: str
    attempt: int
    input: Artifact[Any]
    store: ArtifactStore
    mission_id: str | None = None
    methodologies: tuple[MethodologyRef, ...] = ()
    feedback: GateDecision | None = None
    _recorded: list[Artifact[Any]] = field(default_factory=lambda: [], repr=False)

    @property
    def producer(self) -> Producer:
        """How artifacts made in this run say where they came from."""
        return Producer(
            station=self.station,
            station_version=self.station_version,
            run_id=self.run_id,
            attempt=self.attempt,
        )

    async def record(
        self,
        payload: RecordedT,
        *,
        type: str | None = None,
        inputs: Sequence[ArtifactRef] = (),
    ) -> Artifact[RecordedT]:
        """Record ``payload`` as evidence beside this station's output.

        The artifact is stored at once, with this run as its producer and the station's
        input as its lineage. The station's gate can then read it.
        """
        artifact = Artifact.create(
            payload,
            type=type,
            produced_by=self.producer,
            inputs=(self.input.ref, *inputs),
            mission_id=self.mission_id,
            methodologies=self.methodologies,
        )
        await self.store.put(artifact)
        self._recorded.append(artifact)
        return artifact

    @property
    def recorded(self) -> tuple[Artifact[Any], ...]:
        """Every artifact recorded so far in this run."""
        return tuple(self._recorded)


@dataclass(frozen=True)
class Station(Generic[InT, OutT]):
    """One bounded job: typed input in, typed output out, judged by an optional gate.

    ``run`` may use an agent, several agents or none. Declare a station with the
    :func:`station` decorator, which reads the types from the function's signature.
    """

    name: str
    run: Callable[[StationContext[Any], InT], Awaitable[OutT]]
    input_type: type[InT]
    output_type: type[OutT]
    gate: Gate[OutT] | None = None
    version: str = "0"

    def __post_init__(self) -> None:
        problems: list[str] = []
        if not self.name.strip() or self.name != self.name.strip():
            problems.append("the name must be non-empty with no surrounding space")
        for label, model in (("input", self.input_type), ("output", self.output_type)):
            # Callers may be untyped, so the types are checked at run time.
            if not (
                inspect.isclass(model) and issubclass(model, BaseModel)  # pyright: ignore[reportUnnecessaryIsInstance]
            ):
                problems.append(f"the {label} type must be a Pydantic model, not {model!r}")
        if not inspect.iscoroutinefunction(self.run):
            problems.append("run must be an async function")
        if problems:
            raise StationDefinitionError(f"station {self.name!r}: " + "; ".join(problems))


StationFn = Callable[[StationContext[Any], InT], Awaitable[OutT]]


def _annotation(run: Callable[..., Any], label: str, station_name: str) -> Any:
    """Resolve one annotation of ``run``.

    Only the input and the return type are resolved, so a context annotated with a type
    that cannot be looked up, such as a class defined inside a function, does no harm.
    """
    annotations: dict[str, Any] = getattr(run, "__annotations__", {})
    if label not in annotations:
        raise StationDefinitionError(
            f"station {station_name!r}: annotate the input and the return type"
        )
    annotation = annotations[label]
    if not isinstance(annotation, str):
        return annotation
    scope = {**getattr(run, "__globals__", {}), **inspect.getclosurevars(run).nonlocals}
    try:
        return eval(annotation, scope)
    except Exception as error:
        raise StationDefinitionError(
            f"station {station_name!r}: cannot resolve the annotation {annotation!r}; "
            "define the type at module level, or build the Station directly"
        ) from error


@overload
def station(fn: StationFn[InT, OutT], /) -> Station[InT, OutT]: ...


@overload
def station(
    *, name: str | None = None, version: str = "0", gate: Gate[OutT] | None = None
) -> Callable[[StationFn[InT, OutT]], Station[InT, OutT]]: ...


def station(
    fn: StationFn[InT, OutT] | None = None,
    /,
    *,
    name: str | None = None,
    version: str = "0",
    gate: Gate[OutT] | None = None,
) -> Station[InT, OutT] | Callable[[StationFn[InT, OutT]], Station[InT, OutT]]:
    """Declare a station from an async function.

    The function takes a :class:`StationContext` and one input, and returns one output.
    The input and output types are read from its annotations and must be Pydantic models.
    """

    def wrap(run: StationFn[InT, OutT]) -> Station[InT, OutT]:
        station_name = name or getattr(run, "__name__", "station")
        parameters = list(inspect.signature(run).parameters)
        if len(parameters) != 2:
            raise StationDefinitionError(
                f"station {station_name!r}: run must take a context and one input"
            )
        hints = {
            label: _annotation(run, label, station_name) for label in (parameters[1], "return")
        }
        return Station(
            name=station_name,
            run=run,
            input_type=hints[parameters[1]],
            output_type=hints["return"],
            gate=gate,
            version=version,
        )

    return wrap if fn is None else wrap(fn)


class StationResult(Record):
    """The record of one attempt at one station.

    ``ran`` and ``passed`` are separate answers. ``passed`` is ``None`` whenever the
    output was not judged: the station did not run, it has no gate, or a person has
    still to decide.
    """

    station: Name
    station_version: Name
    run_id: Name
    attempt: int = Field(ge=1)
    outcome: Outcome
    ran: bool
    passed: bool | None
    output: ArtifactRef | None = None
    evidence: tuple[ArtifactRef, ...] = ()
    decision: GateDecision | None = None
    reason: str | None = None


async def run_station(
    station: Station[InT, OutT],
    input: InT | Artifact[InT],
    *,
    store: ArtifactStore,
    deps: Any = None,
    run_id: str | None = None,
    attempt: int = 1,
    mission_id: str | None = None,
    methodologies: Sequence[MethodologyRef] = (),
    feedback: GateDecision | None = None,
) -> StationResult:
    """Run one station once and record what happened.

    The input may be a payload or an artifact that holds one. The output and anything
    the station recorded are stored as artifacts, then the station's gate judges them.

    A station that raises, or returns the wrong type, did not run: that is reported in
    the result and is never a failure. Only a gate can fail a station.
    """
    stamps = tuple(methodologies)
    incoming: Artifact[Any]
    if isinstance(input, Artifact):
        incoming = cast("Artifact[Any]", input)
    else:
        incoming = Artifact.create(input, mission_id=mission_id, methodologies=stamps)
    if not isinstance(incoming.payload, station.input_type):
        raise StationInputInvalid(
            f"station {station.name!r} takes {station.input_type.__name__}, "
            f"not {type(incoming.payload).__name__}"
        )
    await store.put(incoming)

    context: StationContext[Any] = StationContext(
        deps=deps,
        station=station.name,
        station_version=station.version,
        run_id=run_id or uuid.uuid4().hex,
        attempt=attempt,
        input=incoming,
        store=store,
        mission_id=mission_id,
        methodologies=stamps,
        feedback=feedback,
    )

    def result(
        outcome: Outcome,
        *,
        ran: bool,
        passed: bool | None,
        output: ArtifactRef | None = None,
        decision: GateDecision | None = None,
        reason: str | None = None,
    ) -> StationResult:
        return StationResult(
            station=station.name,
            station_version=station.version,
            run_id=context.run_id,
            attempt=attempt,
            outcome=outcome,
            ran=ran,
            passed=passed,
            output=output,
            evidence=tuple(artifact.ref for artifact in context.recorded),
            decision=decision,
            reason=reason,
        )

    try:
        produced: object = await station.run(context, incoming.payload)
    except Exception as error:
        return result(
            "did_not_run",
            ran=False,
            passed=None,
            reason=f"the station raised {type(error).__name__}: {error}",
        )
    if not isinstance(produced, station.output_type):
        return result(
            "did_not_run",
            ran=False,
            passed=None,
            reason=(
                f"the station returned {type(produced).__name__}, "
                f"not {station.output_type.__name__}"
            ),
        )

    output = Artifact.create(
        produced,
        produced_by=context.producer,
        inputs=(incoming.ref,),
        mission_id=mission_id,
        methodologies=stamps,
    )
    await store.put(output)

    if station.gate is None:
        return result("not_judged", ran=True, passed=None, output=output.ref)

    judged = evaluate_gate(
        station.gate, output, Evidence((output, *context.recorded)), methodologies=stamps
    )
    if isinstance(judged, NotEvaluated):
        return result(
            "did_not_run",
            ran=True,
            passed=None,
            output=output.ref,
            reason=f"gate {judged.gate!r} was not evaluated: {judged.reason}",
        )
    if judged.judgement == "pass":
        return result("passed", ran=True, passed=True, output=output.ref, decision=judged)
    if judged.judgement == "fail":
        return result("failed", ran=True, passed=False, output=output.ref, decision=judged)
    return result("needs_approval", ran=True, passed=None, output=output.ref, decision=judged)
