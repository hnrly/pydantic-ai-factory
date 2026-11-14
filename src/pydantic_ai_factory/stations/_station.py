"""Stations: one bounded job with a typed input, a typed output and a gate."""

from __future__ import annotations

import inspect
import logging
import traceback
import types
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Generic, Literal, Self, TypeVar, cast, overload

from pydantic import Field, ValidationError, model_validator
from pydantic_ai import Conversation
from pydantic_ai.agent import AbstractAgent, AgentRunResult
from typing_extensions import TypeVar as TypeVarWithDefault

from ..artifacts import Artifact, ArtifactInvalid, ArtifactRef, ArtifactStore, Producer
from ..artifacts._artifact import adapter_for
from ..contracts import DefinitionError, FactoryError, MethodologyRef, Name, Record, check_name
from ..contracts import run_sync as _run_sync
from ..gates import Evidence, Gate, GateDecision, NotEvaluated

logger = logging.getLogger("pydantic_ai_factory")

InT = TypeVar("InT")
OutT = TypeVar("OutT")
RecordedT = TypeVar("RecordedT")
DepsT = TypeVarWithDefault("DepsT", default=None)

StationOutcome = Literal["passed", "failed", "needs_approval", "not_judged", "did_not_run"]

# Arguments of Agent.run that ctx.run_agent fills in itself.
_RESERVED = ("conversation", "message_history")


class StationDefinitionError(DefinitionError):
    """A station was declared in a way that cannot work."""


class StationInputInvalid(FactoryError):
    """A station was given an input that is not of its input type, or a bad argument."""


class StationClosed(FactoryError):
    """Evidence was recorded, or an agent run, after the station had already returned."""


class Feedback(Record):
    """Why work came back to a station for another attempt.

    ``reasons`` are the failing gate's reasons. On a later attempt, ``ctx.run_agent``
    sends :meth:`message` to the agent in place of its prompt.
    """

    reasons: tuple[str, ...] = Field(min_length=1)

    def message(self) -> str:
        """The text an agent is sent when its work comes back."""
        return "Your last output did not pass its checks.\n" + "\n".join(
            f"- {reason}" for reason in self.reasons
        )


@dataclass
class _Session:
    """What one attempt at a station knows and has recorded. Only the context uses it."""

    station: str
    agents: tuple[AbstractAgent[Any, Any], ...]
    producer: Producer
    input: Artifact[Any]
    store: ArtifactStore
    mission_id: str | None
    methodologies: tuple[MethodologyRef, ...]
    feedback: Feedback | None
    previous: Mapping[str, ArtifactRef]
    artifacts: list[Artifact[Any]] = field(default_factory=lambda: [])
    conversations: dict[str, ArtifactRef] = field(default_factory=lambda: {})
    closed: bool = False

    def check_open(self, doing: str) -> None:
        if self.closed:
            raise StationClosed(f"station {self.station!r} has returned; {doing} before returning")

    def artifact(self, payload: Any, **more: Any) -> Artifact[Any]:
        return Artifact.create(
            payload,
            produced_by=self.producer,
            mission_id=self.mission_id,
            methodologies=self.methodologies,
            **more,
        )


@dataclass(frozen=True)
class StationContext(Generic[DepsT]):
    """What a station's code is given when it runs. It cannot be changed.

    ``deps`` is whatever the caller passed as ``deps=``. ``attempt`` counts from 1.
    ``feedback`` says why the work came back, on a later attempt. ``run_id`` names the run.
    """

    deps: DepsT
    attempt: int
    run_id: str
    feedback: Feedback | None
    _session: _Session = field(repr=False)

    async def record(self, payload: RecordedT, *, type: str | None = None) -> Artifact[RecordedT]:
        """Record ``payload`` as evidence beside this station's output.

        The artifact is stored at once, with this run as its producer and the station's
        input as its lineage. The station's gate can then read it. Evidence must be
        recorded before the station returns.
        """
        session = self._session
        session.check_open("record evidence")
        artifact = session.artifact(payload, type=type, inputs=(session.input.ref,))
        await session.store.put(artifact)
        session.artifacts.append(artifact)
        return artifact

    async def run_agent(
        self, agent: AbstractAgent[Any, Any], prompt: str | None = None, /, **kwargs: Any
    ) -> AgentRunResult[Any]:
        """Run ``agent`` on ``prompt`` and keep its conversation.

        The conversation is stored as an artifact, keyed by the agent's name. A second
        call to the same agent in this attempt continues it. On a later attempt, the
        first call continues the conversation from the attempt before, and sends the
        agent the feedback instead of ``prompt``. Any other keyword argument is passed to
        the agent's run.

        Raises :class:`StationDefinitionError` if the station does not name ``agent``.
        """
        session = self._session
        session.check_open("run agents")
        if not any(agent is known for known in session.agents):
            raise StationDefinitionError(
                f"station {session.station!r}",
                [
                    f"it ran agent {agent.name!r}, which it does not name; "
                    "add the agent to @station(agents=[...])"
                ],
            )
        clashes = [name for name in _RESERVED if name in kwargs]
        if clashes:
            raise StationInputInvalid(
                f"station {session.station!r}: ctx.run_agent sets {', '.join(clashes)} itself"
            )
        name = cast(str, agent.name)
        conversation: Conversation | None = None
        if name in session.conversations:
            stored = await session.store.get(session.conversations[name], Conversation)
            conversation = stored.payload
        elif name in session.previous:
            stored = await session.store.get(session.previous[name], Conversation)
            conversation = stored.payload
            if session.feedback is not None:
                prompt = session.feedback.message()
        result = await agent.run(prompt, conversation=conversation, **kwargs)
        kept = session.artifact(
            result.conversation,
            payload_type=Conversation,
            type=f"Conversation:{name}",
            inputs=(session.input.ref,),
        )
        await session.store.put(kept)
        session.conversations[name] = kept.ref
        return result


@dataclass(frozen=True)
class Station(Generic[InT, OutT]):
    """One bounded job: typed input in, typed output out, judged by an optional gate.

    ``function`` is the station's own code. It may run agents, through
    ``ctx.run_agent``, or none. ``agents`` lists every agent it may run. Declare a
    station with :func:`station`, which reads the types from the function's signature
    and finds the agents itself. :meth:`run` runs it once and records what happened.
    """

    name: str
    function: Callable[[StationContext[Any], InT], Awaitable[OutT]]
    input_type: Any
    output_type: Any
    gate: Gate[OutT] | None = None
    version: str = "0"
    agents: tuple[AbstractAgent[Any, Any], ...] = ()

    def __post_init__(self) -> None:
        problems: list[str] = []
        for label, value in (("name", self.name), ("version", self.version)):
            problem = check_name(value)
            if problem:
                problems.append(f"the {label} {problem}")
        for label, kind in (("input", self.input_type), ("output", self.output_type)):
            try:
                adapter_for(kind)
            except ArtifactInvalid:
                problems.append(
                    f"the {label} type must be one Pydantic can serialise, not {kind!r}"
                )
        if not inspect.iscoroutinefunction(self.function):
            problems.append("the function must be async")
        names: list[str | None] = []
        for agent in self.agents:
            if not isinstance(agent, AbstractAgent):  # pyright: ignore[reportUnnecessaryIsInstance]
                problems.append(f"agents must be Pydantic AI agents, not {agent!r}")
            elif not agent.name:
                problems.append("every agent needs a name; pass name= to Agent(...)")
            else:
                names.append(agent.name)
        for name in sorted({n for n in names if n and names.count(n) > 1}):
            problems.append(f"two agents are named {name!r}; give each agent its own name")
        if problems:
            raise StationDefinitionError(f"station {self.name!r}", problems)

    async def run(
        self,
        input: InT | Artifact[InT],
        *,
        store: ArtifactStore,
        deps: Any = None,
        run_id: str | None = None,
        attempt: int = 1,
        mission_id: str | None = None,
        methodologies: Sequence[MethodologyRef] = (),
        feedback: Feedback | None = None,
        conversations: Mapping[str, ArtifactRef] | None = None,
    ) -> StationResult:
        """Run this station once and record what happened.

        The input may be a payload or an artifact that holds one. The output and anything
        the station recorded are stored as artifacts, then the station's gate judges them.
        ``conversations`` are the agents' conversations from an earlier attempt, which
        ``ctx.run_agent`` carries on.

        A station that raises, or returns something that is not its output type, did not
        run: that is reported in the result, with the traceback, and is never a failure.
        Only a gate can fail a station. Cancellation is not caught.

        Raises :class:`StationInputInvalid` before anything runs if the input or an
        argument is wrong. An error from the store while saving the input or the output
        is raised as it is.
        """
        return await _attempt(
            self,
            input,
            store=store,
            deps=deps,
            run_id=run_id,
            attempt=attempt,
            mission_id=mission_id,
            methodologies=methodologies,
            feedback=feedback,
            previous=conversations or {},
        )

    def run_sync(
        self,
        input: InT | Artifact[InT],
        *,
        store: ArtifactStore,
        deps: Any = None,
        run_id: str | None = None,
    ) -> StationResult:
        """Run this station once, from code that is not async. See :meth:`run`."""
        return _run_sync(self.run(input, store=store, deps=deps, run_id=run_id))


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
            f"station {station_name!r}", ["annotate the input and the return type"]
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
            f"station {station_name!r}",
            [
                f"cannot resolve the annotation {text!r}; define the type at module "
                "level, or build the Station directly"
            ],
        ) from error


def _referenced_names(code: types.CodeType) -> list[str]:
    """Every global and free variable name ``code`` and its nested functions refer to."""
    names = [*code.co_names, *code.co_freevars]
    for constant in code.co_consts:
        if isinstance(constant, types.CodeType):
            names.extend(_referenced_names(constant))
    return names


def agents_used_by(function: Callable[..., Any]) -> tuple[AbstractAgent[Any, Any], ...]:
    """Find the Pydantic AI agents ``function`` refers to by name.

    It reads the global and closure names in the function's code, and in any function
    defined inside it, and keeps each one bound to an agent. An agent reached only
    through a value, such as a dictionary lookup, is not found.
    """
    code = getattr(function, "__code__", None)
    if not isinstance(code, types.CodeType):
        return ()
    scope: dict[str, Any] = dict(getattr(function, "__globals__", {}))
    closure = getattr(function, "__closure__", None) or ()
    for name, cell in zip(code.co_freevars, closure, strict=False):
        try:
            scope[name] = cell.cell_contents
        except ValueError:  # an empty cell: the variable is not bound yet
            continue
    found: list[AbstractAgent[Any, Any]] = []
    for name in _referenced_names(code):
        value = scope.get(name)
        if isinstance(value, AbstractAgent) and not any(value is seen for seen in found):
            found.append(cast("AbstractAgent[Any, Any]", value))
    return tuple(found)


def _agent_station(
    agent: AbstractAgent[Any, OutT],
    *,
    name: str | None,
    version: str,
    gate: Gate[OutT] | None,
) -> Station[str, OutT]:
    """Turn one agent into a station: the input is the prompt, the output is the agent's."""
    station_name = agent.name if name is None else name
    if not station_name:
        raise StationDefinitionError(
            "a station made from an agent", ["the agent needs a name; pass name= to Agent(...)"]
        )
    output_type: Any = agent.output_type
    if not (inspect.isclass(output_type) or isinstance(output_type, types.GenericAlias)):
        raise StationDefinitionError(
            f"station {station_name!r}",
            [
                f"the agent's output type {output_type!r} is not one type; "
                "write the station as a function with @station"
            ],
        )

    async def run(ctx: StationContext[Any], prompt: str) -> Any:
        result = await ctx.run_agent(agent, prompt)
        return result.output

    return Station(
        name=station_name,
        function=run,
        input_type=str,
        output_type=output_type,
        gate=gate,
        version=version,
        agents=(agent,),
    )


@overload
def station(fn: StationFn[InT, OutT], /) -> Station[InT, OutT]: ...


@overload
def station(
    agent: AbstractAgent[Any, OutT],
    /,
    *,
    name: str | None = None,
    version: str = "0",
    gate: Gate[OutT] | None = None,
) -> Station[str, OutT]: ...


@overload
def station(
    *,
    name: str | None = None,
    version: str = "0",
    gate: Gate[Any] | None = None,
    agents: Sequence[AbstractAgent[Any, Any]] | None = None,
) -> Callable[[StationFn[InT, OutT]], Station[InT, OutT]]: ...


def station(
    fn: StationFn[InT, OutT] | AbstractAgent[Any, OutT] | None = None,
    /,
    *,
    name: str | None = None,
    version: str = "0",
    gate: Gate[Any] | None = None,
    agents: Sequence[AbstractAgent[Any, Any]] | None = None,
) -> Station[Any, Any] | Callable[[StationFn[InT, OutT]], Station[InT, OutT]]:
    """Declare a station from an async function, or from one agent.

    The function takes a :class:`StationContext` and one input, and returns one output.
    The input and output types are read from its annotations, and may be any type
    Pydantic can serialise. The agents it runs are found from the names its code refers
    to; ``agents=`` lists them instead, when inference cannot see one.

    ``station(agent)`` makes a station that passes its input to the agent as the prompt
    and hands on the agent's output. Its name is the agent's name.
    """
    if isinstance(fn, AbstractAgent):
        if agents is not None:
            raise StationDefinitionError(
                f"station {fn.name!r}", ["a station made from an agent runs only that agent"]
            )
        return _agent_station(
            cast("AbstractAgent[Any, Any]", fn), name=name, version=version, gate=gate
        )

    def wrap(run: StationFn[InT, OutT]) -> Station[InT, OutT]:
        station_name = getattr(run, "__name__", "") if name is None else name
        if not callable(run):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise StationDefinitionError(
                "a station", [f"it is declared from a function or an agent, not {run!r}"]
            )
        positional = (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
        parameters = list(inspect.signature(run).parameters.values())
        if len(parameters) != 2 or any(p.kind not in positional for p in parameters):
            raise StationDefinitionError(
                f"station {station_name!r}",
                ["the function must take exactly two positional arguments: a context, one input"],
            )
        return Station(
            name=station_name,
            function=run,
            input_type=_annotation(run, parameters[1].name, station_name),
            output_type=_annotation(run, "return", station_name),
            gate=gate,
            version=version,
            agents=agents_used_by(run) if agents is None else tuple(agents),
        )

    return wrap if fn is None else wrap(fn)


class StationResult(Record):
    """The record of one attempt at one station.

    ``outcome`` sums up the attempt as a whole. ``ran`` and ``passed`` are read from the
    record, not stored beside it. ``ran`` is about the station's own code: did it complete
    and return its output type? ``passed`` is the gate's answer, and is ``None`` whenever
    the output was not judged. So an attempt can have ``ran`` true and the outcome
    ``did_not_run``: the code ran, but its gate could not be evaluated.

    ``conversations`` holds the last conversation of each agent the station ran, by the
    agent's name.
    """

    station: Name
    station_version: Name
    run_id: Name
    attempt: int = Field(ge=1)
    outcome: StationOutcome
    input_ref: ArtifactRef
    output_ref: ArtifactRef | None = None
    evidence: tuple[ArtifactRef, ...] = ()
    conversations: dict[Name, ArtifactRef] = Field(default_factory=lambda: {})
    decision: GateDecision | None = None
    not_evaluated: NotEvaluated | None = None
    reason: str | None = None
    error_type: str | None = None
    traceback: str | None = None

    @property
    def ran(self) -> bool:
        """Whether the station's own code completed and returned its output type."""
        return self.outcome != "did_not_run" or self.not_evaluated is not None

    @property
    def passed(self) -> bool | None:
        """The gate's answer, or ``None`` when the output was not judged."""
        return {"passed": True, "failed": False}.get(self.outcome)

    @model_validator(mode="after")
    def _is_consistent(self) -> Self:
        judgement = self.decision.judgement if self.decision else None
        if self.outcome == "did_not_run":
            if self.decision is not None:
                raise ValueError("an attempt that did not run was not judged")
            if not self.reason:
                raise ValueError("an attempt that did not run must say why")
            return self
        if self.not_evaluated is not None:
            raise ValueError("a gate that was not evaluated means the attempt did not run")
        if judgement != (None if self.outcome == "not_judged" else self.outcome):
            raise ValueError(f"outcome {self.outcome!r} does not match the gate's decision")
        if self.output_ref is None:
            raise ValueError(f"outcome {self.outcome!r} needs an output")
        return self


def type_name(kind: Any) -> str:
    """A readable name for a station's input or output type."""
    return kind.__name__ if inspect.isclass(kind) else repr(kind)


def recorded_as(kind: Any, value: object) -> Any:
    """The type to record ``value`` as: its own class when ``kind`` is a class.

    So a subclass of a station's output type keeps its extra fields.
    """
    return type(value) if inspect.isclass(kind) else kind


def coerce(kind: Any, value: object) -> object:
    """Check ``value`` is a ``kind`` and return it, or raise ``ValueError``.

    A class is checked with ``isinstance``. Any other type, such as ``list[str]``, is
    checked by validating the value strictly.
    """
    if inspect.isclass(kind):
        if not isinstance(value, kind):
            raise ValueError(f"expected {type_name(kind)}, not {type(value).__name__}")
        return value
    try:
        return adapter_for(kind).validate_python(value, strict=True)
    except ValidationError as error:
        raise ValueError(f"expected {type_name(kind)}: {error}") from error


async def _attempt(
    station: Station[InT, OutT],
    input: InT | Artifact[InT],
    *,
    store: ArtifactStore,
    deps: Any,
    run_id: str | None,
    attempt: int,
    mission_id: str | None,
    methodologies: Sequence[MethodologyRef],
    feedback: Feedback | None,
    previous: Mapping[str, ArtifactRef],
) -> StationResult:
    """Do the work of :meth:`Station.run`."""
    stamps = tuple(methodologies)
    given: object = input
    payload: object = cast("Artifact[Any]", given).payload if isinstance(given, Artifact) else given
    try:
        payload = coerce(station.input_type, payload)
    except ValueError as error:
        raise StationInputInvalid(f"station {station.name!r} {error}") from error
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
        incoming = Artifact.create(
            payload,
            payload_type=recorded_as(station.input_type, payload),
            mission_id=mission_id,
            methodologies=stamps,
        )
    await store.put(incoming)

    session = _Session(
        station=station.name,
        agents=station.agents,
        producer=producer,
        input=incoming,
        store=store,
        mission_id=mission_id,
        methodologies=stamps,
        feedback=feedback,
        previous=previous,
    )
    context: StationContext[Any] = StationContext(
        deps=deps,
        attempt=attempt,
        run_id=producer.run_id,
        feedback=feedback,
        _session=session,
    )

    def result(outcome: StationOutcome, **more: Any) -> StationResult:
        return StationResult(
            station=producer.station,
            station_version=producer.station_version,
            run_id=producer.run_id,
            attempt=producer.attempt,
            outcome=outcome,
            input_ref=incoming.ref,
            evidence=tuple(artifact.ref for artifact in session.artifacts),
            conversations=dict(session.conversations),
            **more,
        )

    def did_not_run(reason: str, error: BaseException | None = None) -> StationResult:
        return result(
            "did_not_run",
            reason=reason,
            error_type=type(error).__name__ if error else None,
            traceback="".join(traceback.format_exception(error)) if error else None,
        )

    try:
        produced: object = await station.function(context, incoming.payload)
    except Exception as error:
        logger.exception("station %r raised on attempt %d", station.name, attempt)
        return did_not_run(f"the station raised {type(error).__name__}: {error}", error)
    finally:
        session.closed = True

    try:
        produced = coerce(station.output_type, produced)
    except ValueError as error:
        return did_not_run(f"the station returned the wrong type: {error}")
    try:
        output = session.artifact(
            produced,
            payload_type=recorded_as(station.output_type, produced),
            inputs=(incoming.ref,),
        )
    except ArtifactInvalid as error:
        return did_not_run(f"the station's output cannot be recorded: {error}", error)
    await store.put(output)

    if station.gate is None:
        return result("not_judged", output_ref=output.ref)

    judged = station.gate.evaluate(output, Evidence(session.artifacts), methodologies=stamps)
    if isinstance(judged, NotEvaluated):
        return result(
            "did_not_run",
            output_ref=output.ref,
            not_evaluated=judged,
            reason=f"gate {judged.gate!r} was not evaluated: {judged.reason}",
        )
    return result(judged.judgement, output_ref=output.ref, decision=judged)
