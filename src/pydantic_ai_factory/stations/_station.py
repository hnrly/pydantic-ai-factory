"""Stations: one bounded job with a typed input, a typed output and a gate."""

from __future__ import annotations

import inspect
import logging
import traceback
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Generic, Literal, Self, TypeVar, cast, overload

from pydantic import BaseModel, Field, model_validator
from typing_extensions import TypeVar as TypeVarWithDefault

from ..artifacts import Artifact, ArtifactInvalid, ArtifactRef, ArtifactStore, Producer
from ..contracts import FactoryError, MethodologyRef, Name, Record, check_name
from ..gates import Evidence, Gate, GateDecision, NotEvaluated, evaluate_gate

logger = logging.getLogger("pydantic_ai_factory")

InT = TypeVar("InT", bound=BaseModel)
OutT = TypeVar("OutT", bound=BaseModel)
RecordedT = TypeVar("RecordedT", bound=BaseModel)
DepsT = TypeVarWithDefault("DepsT", default=None)

Outcome = Literal["passed", "failed", "needs_approval", "not_judged", "did_not_run"]


class StationDefinitionError(FactoryError):
    """A station was declared in a way that cannot work."""


class StationInputInvalid(FactoryError):
    """A station was given an input that is not of its input type, or a bad argument."""


class StationClosed(FactoryError):
    """Evidence was recorded after the station had already returned."""


@dataclass
class _Recording:
    """What one station run has recorded. Only the context writes to it."""

    artifacts: list[Artifact[Any]] = field(default_factory=lambda: [])
    closed: bool = False


@dataclass(frozen=True)
class StationContext(Generic[DepsT]):
    """What a station's code is given when it runs. It cannot be changed.

    ``deps`` is whatever the caller passed in: clients, settings, an agent. ``feedback``
    is the gate decision that failed the previous attempt, when this run is rework.
    """

    deps: DepsT
    producer: Producer
    input: Artifact[Any]
    store: ArtifactStore
    mission_id: str | None = None
    methodologies: tuple[MethodologyRef, ...] = ()
    feedback: GateDecision | None = None
    _recording: _Recording = field(default_factory=_Recording, init=False, repr=False)

    @property
    def station(self) -> str:
        """The station's name."""
        return self.producer.station

    @property
    def run_id(self) -> str:
        """The run this attempt belongs to."""
        return self.producer.run_id

    @property
    def attempt(self) -> int:
        """Which attempt this is, starting at 1."""
        return self.producer.attempt

    async def record(
        self,
        payload: RecordedT,
        *,
        type: str | None = None,
        inputs: Sequence[ArtifactRef] = (),
    ) -> Artifact[RecordedT]:
        """Record ``payload`` as evidence beside this station's output.

        The artifact is stored at once, with this run as its producer and the station's
        input as its lineage. The station's gate can then read it. Evidence must be
        recorded before the station returns.
        """
        if self._recording.closed:
            raise StationClosed(
                f"station {self.station!r} has returned; record evidence before returning"
            )
        artifact = Artifact.create(
            payload,
            type=type,
            produced_by=self.producer,
            inputs=(self.input.ref, *inputs),
            mission_id=self.mission_id,
            methodologies=self.methodologies,
        )
        await self.store.put(artifact)
        self._recording.artifacts.append(artifact)
        return artifact

    @property
    def recorded(self) -> tuple[Artifact[Any], ...]:
        """Every artifact recorded so far in this run."""
        return tuple(self._recording.artifacts)


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
        for label, value in (("name", self.name), ("version", self.version)):
            problem = check_name(value)
            if problem:
                problems.append(f"the {label} {problem}")
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


def _annotations(run: Callable[..., Any]) -> dict[str, Any]:
    """Read a function's annotations without failing on a name that cannot be found."""
    try:
        import annotationlib  # Python 3.14 and later  # pyright: ignore[reportMissingImports]
    except ImportError:
        return dict(getattr(run, "__annotations__", {}))
    return dict(
        annotationlib.get_annotations(run, format=annotationlib.Format.FORWARDREF)  # pyright: ignore
    )


def _annotation(run: Callable[..., Any], label: str, station_name: str) -> Any:
    """Resolve one annotation of ``run``.

    Only the input and the return type are resolved, so a context annotated with a type
    that cannot be looked up, such as a class defined inside a function, does no harm.
    """
    annotations = _annotations(run)
    if label not in annotations:
        raise StationDefinitionError(
            f"station {station_name!r}: annotate the input and the return type"
        )
    annotation = annotations[label]
    if inspect.isclass(annotation):
        return annotation
    text = (
        annotation if isinstance(annotation, str) else getattr(annotation, "__forward_arg__", None)
    )
    if not isinstance(text, str):
        return annotation
    scope = {**getattr(run, "__globals__", {}), **inspect.getclosurevars(run).nonlocals}
    try:
        return eval(text, scope)
    except Exception as error:
        raise StationDefinitionError(
            f"station {station_name!r}: cannot resolve the annotation {text!r}; "
            "define the type at module level, or build the Station directly"
        ) from error


@overload
def station(fn: StationFn[InT, OutT], /) -> Station[InT, OutT]: ...


@overload
def station(
    *, name: str | None = None, version: str = "0"
) -> Callable[[StationFn[InT, OutT]], Station[InT, OutT]]: ...


@overload
def station(
    *, name: str | None = None, version: str = "0", gate: Gate[OutT]
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
        station_name = getattr(run, "__name__", "") if name is None else name
        if not callable(run):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise StationDefinitionError(f"a station is declared from a function, not {run!r}")
        positional = (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
        parameters = list(inspect.signature(run).parameters.values())
        if len(parameters) != 2 or any(p.kind not in positional for p in parameters):
            raise StationDefinitionError(
                f"station {station_name!r}: run must take exactly two positional "
                "arguments, a context and one input"
            )
        return Station(
            name=station_name,
            run=run,
            input_type=_annotation(run, parameters[1].name, station_name),
            output_type=_annotation(run, "return", station_name),
            gate=gate,
            version=version,
        )

    return wrap if fn is None else wrap(fn)


class StationResult(Record):
    """The record of one attempt at one station.

    ``ran`` is about the station's own code: did it complete and return its output type?
    ``passed`` is the gate's answer, and is ``None`` whenever the output was not judged.
    ``outcome`` sums up the attempt as a whole. So an attempt can have ``ran=True`` and
    the outcome ``did_not_run``: the code ran, but its gate could not be evaluated.
    """

    station: Name
    station_version: Name
    run_id: Name
    attempt: int = Field(ge=1)
    outcome: Outcome
    ran: bool
    passed: bool | None
    input: ArtifactRef
    output: ArtifactRef | None = None
    evidence: tuple[ArtifactRef, ...] = ()
    decision: GateDecision | None = None
    not_evaluated: NotEvaluated | None = None
    reason: str | None = None
    error_type: str | None = None
    traceback: str | None = None

    @model_validator(mode="after")
    def _is_consistent(self) -> Self:
        judgement = self.decision.judgement if self.decision else None
        expected: dict[str, tuple[bool | None, bool | None, str | None]] = {
            "passed": (True, True, "pass"),
            "failed": (True, False, "fail"),
            "needs_approval": (True, None, "needs_approval"),
            "not_judged": (True, None, None),
        }
        if self.outcome in expected:
            if (self.ran, self.passed, judgement) != expected[self.outcome]:
                raise ValueError(
                    f"outcome {self.outcome!r} does not match ran, passed and decision"
                )
            if self.output is None:
                raise ValueError(f"outcome {self.outcome!r} needs an output")
        else:
            if self.passed is not None or self.decision is not None:
                raise ValueError("an attempt that did not run was not judged")
            if not self.reason:
                raise ValueError("an attempt that did not run must say why")
            if self.ran != (self.not_evaluated is not None):
                raise ValueError("ran must be true exactly when the gate was not evaluated")
        return self


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

    A station that raises, or returns something that cannot be recorded as its output
    type, did not run: that is reported in the result, with the traceback, and is never
    a failure. Only a gate can fail a station. Cancellation is not caught.

    Raises :class:`StationInputInvalid` before anything runs if the input or an argument
    is wrong. An error from the store while saving the input or the output is raised
    as it is.
    """
    stamps = tuple(methodologies)
    given: object = input
    payload: object = cast("Artifact[Any]", given).payload if isinstance(given, Artifact) else given
    if not isinstance(payload, station.input_type):
        raise StationInputInvalid(
            f"station {station.name!r} takes {station.input_type.__name__}, "
            f"not {type(payload).__name__}"
        )
    if mission_id is not None and (problem := check_name(mission_id)):
        raise StationInputInvalid(f"station {station.name!r}: mission_id {problem}")
    try:
        producer = Producer(
            station=station.name,
            station_version=station.version,
            run_id=run_id or uuid.uuid4().hex,
            attempt=attempt,
        )
    except ValueError as error:
        raise StationInputInvalid(f"station {station.name!r}: {error}") from error

    incoming: Artifact[Any]
    if isinstance(given, Artifact):
        incoming = cast("Artifact[Any]", given)
    else:
        incoming = Artifact.create(payload, mission_id=mission_id, methodologies=stamps)
    await store.put(incoming)

    context: StationContext[Any] = StationContext(
        deps=deps,
        producer=producer,
        input=incoming,
        store=store,
        mission_id=mission_id,
        methodologies=stamps,
        feedback=feedback,
    )
    recording = context._recording  # pyright: ignore[reportPrivateUsage]

    def result(outcome: Outcome, *, ran: bool, passed: bool | None, **more: Any) -> StationResult:
        return StationResult(
            station=producer.station,
            station_version=producer.station_version,
            run_id=producer.run_id,
            attempt=producer.attempt,
            outcome=outcome,
            ran=ran,
            passed=passed,
            input=incoming.ref,
            evidence=tuple(artifact.ref for artifact in recording.artifacts),
            **more,
        )

    def did_not_run(reason: str, error: BaseException | None = None) -> StationResult:
        return result(
            "did_not_run",
            ran=False,
            passed=None,
            reason=reason,
            error_type=type(error).__name__ if error else None,
            traceback="".join(traceback.format_exception(error)) if error else None,
        )

    try:
        produced: object = await station.run(context, incoming.payload)
    except Exception as error:
        logger.exception("station %r raised on attempt %d", station.name, attempt)
        return did_not_run(f"the station raised {type(error).__name__}: {error}", error)
    finally:
        recording.closed = True

    if not isinstance(produced, station.output_type):
        return did_not_run(
            f"the station returned {type(produced).__name__}, not {station.output_type.__name__}"
        )
    try:
        output = Artifact.create(
            produced,
            produced_by=producer,
            inputs=(incoming.ref,),
            mission_id=mission_id,
            methodologies=stamps,
        )
    except ArtifactInvalid as error:
        return did_not_run(f"the station's output cannot be recorded: {error}", error)
    await store.put(output)

    if station.gate is None:
        return result("not_judged", ran=True, passed=None, output=output.ref)

    judged = evaluate_gate(
        station.gate, output, Evidence(recording.artifacts), methodologies=stamps
    )
    if isinstance(judged, NotEvaluated):
        return result(
            "did_not_run",
            ran=True,
            passed=None,
            output=output.ref,
            not_evaluated=judged,
            reason=f"gate {judged.gate!r} was not evaluated: {judged.reason}",
        )
    outcome: Outcome = {"pass": "passed", "fail": "failed"}.get(judged.judgement, "needs_approval")  # type: ignore[assignment]
    return result(
        outcome,
        ran=True,
        passed={"passed": True, "failed": False}.get(outcome),
        output=output.ref,
        decision=judged,
    )
