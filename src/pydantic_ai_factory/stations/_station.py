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
from pydantic_ai import Conversation, DeferredToolRequests, DeferredToolResults
from pydantic_ai._agent_graph import GraphAgentState
from pydantic_ai.agent import AbstractAgent, AgentRunResult
from pydantic_ai.conversation import ConversationTypeAdapter
from typing_extensions import TypeVar as TypeVarWithDefault

from .. import _tracing as tracing
from .._methodology import (
    EvidenceCollector,
    Methodology,
    MethodologyDefinitionError,
    judge,
    methodologies_of,
    unique,
)
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

StationOutcome = Literal["passed", "failed", "waiting", "not_judged", "did_not_run"]
WaitingKind = Literal["approval", "escalation", "tool-calls"]

# Arguments of Agent.run that ctx.run_agent fills in itself.
_RESERVED = ("conversation", "message_history", "deferred_tool_results")


class StationDefinitionError(DefinitionError):
    """A station was declared in a way that cannot work."""


class StationInputInvalid(FactoryError):
    """A station was given an input that is not of its input type, or a bad argument."""


class StationClosed(FactoryError):
    """Evidence was recorded, or an agent run, after the station had already returned."""


_NO_OUTPUT: Any = object()


class Escalate(Exception):
    """Raise inside a station to hand a decision to a person.

    The run waits with ``run.waiting.kind`` of ``"escalation"``. Pass ``output=`` to offer
    the work as it stands: a person who approves then accepts it as the station's output.
    """

    def __init__(self, reason: str, *, output: Any = _NO_OUTPUT) -> None:
        self.reason = reason
        self.output = output
        super().__init__(reason)


class Feedback(Record):
    """Why work came back to a station for another attempt.

    ``reasons`` are the failing gate's reasons, or a person's note. ``by`` names the
    person who asked for changes, if one did. On a later attempt, ``ctx.run_agent`` sends
    :meth:`message` to the agent in place of its prompt.
    """

    reasons: tuple[str, ...] = Field(min_length=1)
    by: Name | None = None

    def message(self) -> str:
        """The text an agent is sent when its work comes back."""
        if self.by is not None:
            return "A person asked for changes to your last output.\n" + "\n".join(
                f"{self.by}: {reason}" for reason in self.reasons
            )
        return "Your last output did not pass its checks.\n" + "\n".join(
            f"- {reason}" for reason in self.reasons
        )


class AgentCall(Record):
    """The record of one finished ``ctx.run_agent`` call, so a resumed station can replay it.

    ``evidence`` is what the agent's methodologies recorded during the call.
    """

    agent: Name
    conversation: ArtifactRef
    output: ArtifactRef
    evidence: tuple[ArtifactRef, ...] = ()


class Question(Record):
    """A question an agent is waiting for a person to answer.

    It prints as its text. When an agent asks more than one, answer each by using it
    as a key: ``run.reply({first: "Yes.", second: "Grey."}, by="ada")``.
    """

    call_id: str
    tool: str
    text: str

    def __str__(self) -> str:
        return self.text

    def __hash__(self) -> int:
        return hash(self.call_id)


class Action(Record):
    """A tool call an agent is waiting for a person to allow or deny.

    It prints as the call, such as ``delete_files(path='old/')``. When an agent waits
    on more than one, answer each by using it as a key, with ``True`` to allow it and
    ``False`` to deny it.
    """

    call_id: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict[str, Any])

    def __str__(self) -> str:
        args = ", ".join(f"{name}={value!r}" for name, value in self.args.items())
        return f"{self.tool}({args})"

    def __hash__(self) -> int:
        return hash(self.call_id)


class Waiting(Record):
    """What a person is being asked, when a station or a run is waiting.

    ``kind`` says why: ``"approval"`` when the station has ``approval=True`` and its gate
    passed, ``"escalation"`` when only a person should settle it, and ``"tool-calls"``
    when an agent asked a person something. ``artifact`` and ``output`` are the work
    being decided on, when there is any. ``requests`` and ``agent`` are set for tool calls;
    ``questions`` and ``actions`` say what those calls ask, in the order they were made.
    """

    kind: WaitingKind
    station: Name
    artifact: ArtifactRef | None = None
    output: Any = None
    reasons: tuple[str, ...] = ()
    requests: DeferredToolRequests | None = None
    agent: Name | None = None
    conversation: ArtifactRef | None = None

    @property
    def questions(self) -> tuple[Question, ...]:
        """Every tool call that waits for a person's answer, such as an ``AskPerson`` question."""
        if self.requests is None:
            return ()
        return tuple(
            Question(
                call_id=call.tool_call_id,
                tool=call.tool_name,
                text=_question_text(
                    self.requests.metadata.get(call.tool_call_id, {}), call.args_as_dict()
                )
                or str(
                    Action(call_id=call.tool_call_id, tool=call.tool_name, args=call.args_as_dict())
                ),
            )
            for call in self.requests.calls
        )

    @property
    def actions(self) -> tuple[Action, ...]:
        """Every tool call that waits for a person to allow or deny it."""
        if self.requests is None:
            return ()
        return tuple(
            Action(call_id=call.tool_call_id, tool=call.tool_name, args=call.args_as_dict())
            for call in self.requests.approvals
        )


def _question_text(metadata: Mapping[str, Any], args: Mapping[str, Any]) -> str | None:
    """The question a call asks: from its metadata, as ``AskPerson`` sends it, or its arguments."""
    for source in (metadata, args):
        text = source.get("question")
        if isinstance(text, str) and text.strip():
            return text
    return None


class _ToolCallsWait(BaseException):
    """An agent's run ended with deferred tool calls. Raised through station code on purpose.

    It derives from ``BaseException`` so that a station's own ``except Exception`` cannot
    swallow it.
    """

    def __init__(self, agent: str, requests: DeferredToolRequests, conversation: ArtifactRef):
        self.agent = agent
        self.requests = requests
        self.conversation = conversation
        super().__init__(agent)


@dataclass
class _Session:
    """What one attempt at a station knows and has recorded. Only the context uses it."""

    station: str
    agents: tuple[AbstractAgent[Any, Any], ...]
    producer: Producer
    input: Artifact[Any]
    store: ArtifactStore
    mission_id: str | None
    feedback: Feedback | None
    previous: Mapping[str, ArtifactRef]
    replay: tuple[AgentCall, ...] = ()
    answer: DeferredToolResults | None = None
    waiting: Waiting | None = None
    artifacts: list[Artifact[Any]] = field(default_factory=lambda: [])
    conversations: dict[str, ArtifactRef] = field(default_factory=lambda: {})
    calls: list[AgentCall] = field(default_factory=lambda: [])
    in_force: list[Methodology] = field(default_factory=lambda: [])
    closed: bool = False

    @property
    def methodologies(self) -> tuple[Methodology, ...]:
        """The methodologies of every agent run so far in this attempt."""
        return unique(self.in_force)

    @property
    def stamps(self) -> tuple[MethodologyRef, ...]:
        return tuple(methodology.ref for methodology in self.methodologies)

    def check_open(self, doing: str) -> None:
        if self.closed:
            raise StationClosed(f"station {self.station!r} has returned; {doing} before returning")

    def artifact(self, payload: Any, **more: Any) -> Artifact[Any]:
        return Artifact.create(
            payload,
            produced_by=self.producer,
            mission_id=self.mission_id,
            methodologies=self.stamps,
            **more,
        )


def _with_deferred(agent: AbstractAgent[Any, Any], kwargs: dict[str, Any]) -> list[Any] | None:
    """The run's output type with ``DeferredToolRequests`` added, or None to keep the agent's.

    A run can wait on tool calls only if its output type includes ``DeferredToolRequests``.
    An agent that already declares it is left alone. So is an agent with output
    validators, because Pydantic AI refuses a run-level output type for one: to let it
    wait, declare ``DeferredToolRequests`` in its own ``output_type``.
    """
    given = "output_type" in kwargs
    declared: Any = kwargs.pop("output_type") if given else agent.output_type
    kinds: list[Any] = (
        list(cast("list[Any]", declared)) if isinstance(declared, list) else [declared]
    )
    if DeferredToolRequests in kinds:
        return kinds if given else None
    # Pydantic AI has no public way to ask whether an agent has output validators.
    if not given and getattr(agent, "_output_validators", None):
        return None
    return [*kinds, DeferredToolRequests]


def _output_type_of(agent: AbstractAgent[Any, Any]) -> Any:
    """The agent's output type when it is one type Pydantic can read back, else ``Any``."""
    kind: Any = agent.output_type
    if inspect.isclass(kind) or isinstance(kind, types.GenericAlias):
        return kind
    return Any


def _widen_output_retries(agent: AbstractAgent[Any, Any], given: Any, extra: int) -> Any:
    """The ``retries`` argument that gives the agent ``extra`` more output retries.

    The agent's own budget is kept for ordinary validation retries, and the methodologies'
    rounds are added on top. ``given`` is any ``retries`` the station passed itself.
    """
    if isinstance(given, int):
        return {"tools": given, "output": given + extra}
    # Pydantic AI keeps the agent's own budget privately; 1 is its default.
    own: int = getattr(agent, "_max_output_retries", 1)
    if isinstance(given, Mapping):
        budgets = dict(cast("Mapping[str, int]", given))
        return {**budgets, "output": budgets.get("output", own) + extra}
    return {"output": own + extra}


def _as_json_reads_back(conversation: Conversation) -> Conversation:
    """The conversation as it reads back from JSON, which is how it is stored.

    Message metadata is typed ``Any``, and some capabilities put message parts in it:
    Pydantic AI Harness's ``CodeMode`` keeps each tool call it made inside ``run_code``.
    JSON turns those into dictionaries, so a conversation stored as it was would not read
    back the same. Pydantic AI persists a conversation as JSON, so the factory keeps that form.
    """
    return ConversationTypeAdapter.validate_json(ConversationTypeAdapter.dump_json(conversation))


def replayed_result(output: Any, conversation: Conversation) -> AgentRunResult[Any]:
    """Rebuild the result of a finished agent run from its output and conversation.

    Pydantic AI has no public constructor for a finished result, so this fills in the
    state that ``output``, ``all_messages()``, ``usage`` and ``conversation`` read.
    ``workspace`` is a placeholder on a replayed result.
    """
    state = GraphAgentState(
        message_history=list(conversation.messages),
        usage=conversation.usage,
        conversation_id=conversation.conversation_id,
    )
    return AgentRunResult(output, _state=state, _new_message_index=len(conversation.messages))


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

        When the agent ends with deferred tool calls, such as a question for a person,
        the station stops and the run waits. When the person answers, the station runs
        again from the top: calls that already finished return their stored result,
        matched by agent and order, and the waiting call carries on with the answer.

        The evidence each :class:`Methodology` on the agent records is stored and added
        to the station's evidence, and its gate is joined to the station's. The agent's
        output retry budget is raised by each methodology's ``output_retries``.

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
        methodologies = methodologies_of(agent)
        with tracing.span(
            f"agent {name}",
            {
                "factory.agent.name": name,
                "factory.agent.attempt": self.attempt,
                "factory.run.id": self.run_id,
                "factory.methodologies": [str(m.ref) for m in methodologies],
            },
        ) as current:
            return await self._run_agent(agent, name, methodologies, prompt, kwargs, current)

    async def _run_agent(
        self,
        agent: AbstractAgent[Any, Any],
        name: str,
        methodologies: tuple[Methodology, ...],
        prompt: str | None,
        kwargs: dict[str, Any],
        span: tracing.Span,
    ) -> AgentRunResult[Any]:
        """Do the work of :meth:`run_agent`, inside its span."""
        session = self._session
        index = len(session.calls)
        session.in_force.extend(methodologies)

        if index < len(session.replay):
            done = session.replay[index]
            if done.agent != name:
                raise StationDefinitionError(
                    f"station {session.station!r}",
                    [
                        f"on resume, call {index + 1} ran agent {name!r} where it ran "
                        f"{done.agent!r} before; keep the code between agent runs deterministic"
                    ],
                )
            replayed = await session.store.get(done.conversation, Conversation)
            output = await session.store.get(done.output, _output_type_of(agent))
            kinds = {kind.__name__: kind for m in methodologies for kind in m.evidence}
            for ref in done.evidence:
                if ref.type not in kinds:
                    raise StationDefinitionError(
                        f"station {session.station!r}",
                        [
                            f"on resume, agent {name!r} has no methodology that records "
                            f"{ref.type}; resume on the agents that started the run"
                        ],
                    )
                session.artifacts.append(await session.store.get(ref, kinds[ref.type]))
            session.conversations[name] = done.conversation
            session.calls.append(done)
            tracing.annotate(span, {"factory.agent.conversation": done.conversation.digest})
            return replayed_result(output.payload, replayed.payload)

        conversation: Conversation | None = None
        deferred_results: DeferredToolResults | None = None
        waiting = session.waiting
        if session.answer is not None and waiting is not None and waiting.agent == name:
            # This is the call that was waiting: carry it on with the person's answer.
            stored = await session.store.get(cast(ArtifactRef, waiting.conversation), Conversation)
            conversation, prompt = stored.payload, None
            deferred_results, session.answer = session.answer, None
        elif name in session.conversations:
            stored = await session.store.get(session.conversations[name], Conversation)
            conversation = stored.payload
        elif name in session.previous:
            stored = await session.store.get(session.previous[name], Conversation)
            conversation = stored.payload
            if session.feedback is not None:
                prompt = session.feedback.message()

        output_type = _with_deferred(agent, kwargs)
        if output_type is not None:
            kwargs["output_type"] = output_type
        collector = EvidenceCollector()
        kwargs["capabilities"] = [*(kwargs.get("capabilities") or ()), collector]
        extra = sum(methodology.output_retries for methodology in methodologies)
        if extra:
            kwargs["retries"] = _widen_output_retries(agent, kwargs.get("retries"), extra)
        result: AgentRunResult[Any] = await agent.run(
            prompt,
            conversation=conversation,
            deferred_tool_results=deferred_results,
            **kwargs,
        )
        evidence: list[ArtifactRef] = []
        for event in collector.recorded:
            recorded = session.artifact(event.payload, inputs=(session.input.ref,))
            await session.store.put(recorded)
            session.artifacts.append(recorded)
            evidence.append(recorded.ref)
        kept = session.artifact(
            _as_json_reads_back(result.conversation),
            payload_type=Conversation,
            type=f"Conversation:{name}",
            inputs=(session.input.ref,),
        )
        await session.store.put(kept)
        session.conversations[name] = kept.ref
        tracing.annotate(span, {"factory.agent.conversation": kept.ref.digest})
        produced: Any = result.output
        if isinstance(produced, DeferredToolRequests):
            raise _ToolCallsWait(name, produced, kept.ref)
        output = session.artifact(
            result.output, type=f"AgentOutput:{name}", inputs=(session.input.ref,)
        )
        await session.store.put(output)
        session.calls.append(
            AgentCall(
                agent=name, conversation=kept.ref, output=output.ref, evidence=tuple(evidence)
            )
        )
        return result


@dataclass(frozen=True)
class Station(Generic[InT, OutT]):
    """One bounded job: typed input in, typed output out, judged by an optional gate.

    ``function`` is the station's own code. It may run agents, through
    ``ctx.run_agent``, or none. ``agents`` lists every agent it may run, and
    :attr:`methodologies` the methodologies attached to them. Declare a station with
    :func:`station`, which reads the types from the function's signature and finds the
    agents itself. :meth:`run` runs it once and records what happened.
    """

    name: str
    function: Callable[[StationContext[Any], InT], Awaitable[OutT]]
    input_type: Any
    output_type: Any
    gate: Gate[OutT] | None = None
    version: str = "0"
    agents: tuple[AbstractAgent[Any, Any], ...] = ()
    approval: bool = False

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
        if not problems:
            try:
                _ = self.methodologies
            except MethodologyDefinitionError as error:
                problems.extend(error.findings)
        if problems:
            raise StationDefinitionError(f"station {self.name!r}", problems)

    @property
    def methodologies(self) -> tuple[Methodology, ...]:
        """Every methodology attached to the station's agents, one of each name."""
        return unique([m for agent in self.agents for m in methodologies_of(agent)])

    async def run(
        self,
        input: InT | Artifact[InT],
        *,
        store: ArtifactStore,
        deps: Any = None,
        run_id: str | None = None,
        attempt: int = 1,
        mission_id: str | None = None,
        feedback: Feedback | None = None,
        conversations: Mapping[str, ArtifactRef] | None = None,
        resume: StationResult | None = None,
        answer: DeferredToolResults | None = None,
    ) -> StationResult:
        """Run this station once and record what happened.

        The input may be a payload or an artifact that holds one. The output and anything
        the station recorded are stored as artifacts, then the station's gate judges them.
        ``conversations`` are the agents' conversations from an earlier attempt, which
        ``ctx.run_agent`` carries on. ``resume`` and ``answer`` carry on an attempt that
        was waiting on tool calls: the station runs again, replaying the agent calls that
        finished, and the waiting call receives ``answer``.

        A station that raises, or returns something that is not its output type, did not
        run: that is reported in the result, with the traceback, and is never a failure.
        Only a gate can fail a station. Cancellation is not caught.

        Raises :class:`StationInputInvalid` before anything runs if the input or an
        argument is wrong. An error from the store while saving the input or the output
        is raised as it is.

        The attempt is traced as a span named ``station`` and the station's name, marked
        as an error when the attempt did not run. The spans of its agents and its gates
        sit inside it.
        """
        with tracing.span(
            f"station {self.name}",
            {
                "factory.station.name": self.name,
                "factory.station.version": self.version,
                "factory.station.attempt": attempt,
                "factory.run.id": run_id,
                "factory.methodologies": [str(m.ref) for m in self.methodologies],
            },
        ) as current:
            result = await _attempt(
                self,
                input,
                store=store,
                deps=deps,
                run_id=run_id,
                attempt=attempt,
                mission_id=mission_id,
                feedback=feedback,
                previous=conversations or {},
                resume=resume,
                answer=answer,
            )
            tracing.annotate(
                current,
                {
                    "factory.run.id": result.run_id,
                    "factory.station.outcome": result.outcome,
                    "factory.station.ran": result.ran,
                    "factory.station.passed": result.passed,
                    "factory.station.input": result.input_ref.digest,
                    "factory.station.output": result.output_ref.digest
                    if result.output_ref
                    else None,
                    "factory.station.evidence": [ref.digest for ref in result.evidence],
                    "factory.station.error_type": result.error_type,
                },
            )
            if result.outcome == "did_not_run":
                tracing.mark_error(current, result.reason or "the attempt did not run")
            return result

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
    approval: bool,
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
        approval=approval,
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
    approval: bool = False,
) -> Station[str, OutT]: ...


@overload
def station(
    *,
    name: str | None = None,
    version: str = "0",
    gate: Gate[Any] | None = None,
    agents: Sequence[AbstractAgent[Any, Any]] | None = None,
    approval: bool = False,
) -> Callable[[StationFn[InT, OutT]], Station[InT, OutT]]: ...


def station(
    fn: StationFn[InT, OutT] | AbstractAgent[Any, OutT] | None = None,
    /,
    *,
    name: str | None = None,
    version: str = "0",
    gate: Gate[Any] | None = None,
    agents: Sequence[AbstractAgent[Any, Any]] | None = None,
    approval: bool = False,
) -> Station[Any, Any] | Callable[[StationFn[InT, OutT]], Station[InT, OutT]]:
    """Declare a station from an async function, or from one agent.

    The function takes a :class:`StationContext` and one input, and returns one output.
    The input and output types are read from its annotations, and may be any type
    Pydantic can serialise. The agents it runs are found from the names its code refers
    to; ``agents=`` lists them instead, when inference cannot see one.

    ``station(agent)`` makes a station that passes its input to the agent as the prompt
    and hands on the agent's output. Its name is the agent's name.

    ``approval=True`` makes a person approve the output once every gate has passed.
    """
    if isinstance(fn, AbstractAgent):
        if agents is not None:
            raise StationDefinitionError(
                f"station {fn.name!r}", ["a station made from an agent runs only that agent"]
            )
        return _agent_station(
            cast("AbstractAgent[Any, Any]", fn),
            name=name,
            version=version,
            gate=gate,
            approval=approval,
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
            approval=approval,
        )

    return wrap if fn is None else wrap(fn)


class StationResult(Record):
    """The record of one attempt at one station.

    ``outcome`` sums up the attempt as a whole. ``ran`` and ``passed`` are read from the
    record, not stored beside it. ``ran`` is about the station's own code: did it complete
    and return its output type? ``passed`` is the gate's answer, and is ``None`` whenever
    the output was not judged. So an attempt can have ``ran`` true and the outcome
    ``did_not_run``: the code ran, but its gate could not be evaluated.

    ``waiting`` says what a person is asked, when the outcome is ``"waiting"``.
    ``conversations`` holds the last conversation of each agent the station ran, by the
    agent's name, and ``calls`` the agent runs that finished, in order.
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
    calls: tuple[AgentCall, ...] = ()
    decision: GateDecision | None = None
    not_evaluated: NotEvaluated | None = None
    waiting: Waiting | None = None
    feedback: Feedback | None = None
    reason: str | None = None
    error_type: str | None = None
    traceback: str | None = None

    @property
    def ran(self) -> bool:
        """Whether the station's own code completed and returned its output type."""
        if self.outcome == "waiting":
            return self.output_ref is not None
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
        if (self.outcome == "waiting") != (self.waiting is not None):
            raise ValueError("an attempt is waiting exactly when it says what it waits for")
        if self.outcome == "waiting":
            if judgement == "failed":
                raise ValueError("work that failed its gate does not wait for a person")
            approval = self.waiting is not None and self.waiting.kind == "approval"
            if approval and (judgement not in (None, "passed") or self.output_ref is None):
                raise ValueError("only output that passed every gate waits for approval")
            return self
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
    feedback: Feedback | None,
    previous: Mapping[str, ArtifactRef],
    resume: StationResult | None,
    answer: DeferredToolResults | None,
) -> StationResult:
    """Do the work of :meth:`Station.run`."""
    given: object = input
    payload: object = cast("Artifact[Any]", given).payload if isinstance(given, Artifact) else given
    try:
        payload = coerce(station.input_type, payload)
    except ValueError as error:
        raise StationInputInvalid(f"station {station.name!r} {error}") from error
    if mission_id is not None and (problem := check_name(mission_id)):
        raise StationInputInvalid(f"station {station.name!r}: mission_id {problem}")
    if (resume is None) != (answer is None):
        raise StationInputInvalid(f"station {station.name!r}: resume and answer go together")
    if resume is not None and (resume.waiting is None or resume.waiting.kind != "tool-calls"):
        raise StationInputInvalid(f"station {station.name!r}: only tool calls are resumed here")
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
        )
    await store.put(incoming)

    session = _Session(
        station=station.name,
        agents=station.agents,
        producer=producer,
        input=incoming,
        store=store,
        mission_id=mission_id,
        feedback=feedback,
        previous=previous,
        replay=resume.calls if resume is not None else (),
        answer=answer,
        waiting=resume.waiting if resume is not None else None,
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
            evidence=tuple(dict.fromkeys(artifact.ref for artifact in session.artifacts)),
            conversations=dict(session.conversations),
            calls=tuple(session.calls),
            feedback=feedback,
            **more,
        )

    def did_not_run(reason: str, error: BaseException | None = None) -> StationResult:
        return result(
            "did_not_run",
            reason=reason,
            error_type=type(error).__name__ if error else None,
            traceback="".join(traceback.format_exception(error)) if error else None,
        )

    async def record_output(produced: object) -> Artifact[Any] | StationResult:
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
        return output

    def escalation(reasons: tuple[str, ...], output: Artifact[Any] | None) -> Waiting:
        return Waiting(
            kind="escalation",
            station=station.name,
            artifact=output.ref if output else None,
            output=output.payload if output else None,
            reasons=reasons,
        )

    try:
        produced: object = await station.function(context, incoming.payload)
    except _ToolCallsWait as wait:
        return result(
            "waiting",
            waiting=Waiting(
                kind="tool-calls",
                station=station.name,
                requests=wait.requests,
                agent=wait.agent,
                conversation=wait.conversation,
            ),
        )
    except Escalate as raised:
        offered: Artifact[Any] | None = None
        if raised.output is not _NO_OUTPUT:
            recorded = await record_output(raised.output)
            if isinstance(recorded, StationResult):
                return recorded
            offered = recorded
        return result(
            "waiting",
            output_ref=offered.ref if offered else None,
            waiting=escalation((raised.reason,), offered),
        )
    except Exception as error:
        logger.exception("station %r raised on attempt %d", station.name, attempt)
        return did_not_run(f"the station raised {type(error).__name__}: {error}", error)
    finally:
        session.closed = True

    output = await record_output(produced)
    if isinstance(output, StationResult):
        return output

    def approval_or(outcome: StationOutcome, **more: Any) -> StationResult:
        if not station.approval:
            return result(outcome, output_ref=output.ref, **more)
        decision: GateDecision | None = more.get("decision")
        return result(
            "waiting",
            output_ref=output.ref,
            waiting=Waiting(
                kind="approval",
                station=station.name,
                artifact=output.ref,
                output=output.payload,
                reasons=decision.reasons if decision else (),
            ),
            **more,
        )

    judged = judge(
        station.name,
        station.gate,
        session.methodologies,
        output,
        Evidence(session.artifacts),
        stamps=session.stamps,
    )
    if judged is None:
        return approval_or("not_judged")
    if isinstance(judged, NotEvaluated):
        return result(
            "did_not_run",
            output_ref=output.ref,
            not_evaluated=judged,
            reason=f"gate {judged.gate!r} was not evaluated: {judged.reason}",
        )
    if judged.judgement == "escalate":
        return result(
            "waiting",
            output_ref=output.ref,
            decision=judged,
            waiting=escalation(judged.reasons, output),
        )
    if judged.judgement == "passed":
        return approval_or("passed", decision=judged)
    return result(judged.judgement, output_ref=output.ref, decision=judged)
