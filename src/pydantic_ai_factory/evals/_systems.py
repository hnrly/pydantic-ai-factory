"""Factories and stations as systems under test, run in this process through the ports.

:class:`FactorySystem` runs a factory on the request and answers what it waits on, as a
person at the ports would:

* a round of questions from ``ask_user_question`` goes to the stakeholder port, up to
  :data:`MAX_QUESTIONS_PER_CALL` questions to a call. A question from any other tool is
  told nobody here can answer it, and actions are allowed;
* the factory's approval of its output, and any escalation, is approved, and noted in the
  run's :class:`ApprovalLog` with the reasons the factory gave;
* an escalation because rework ran out ends the run: the last output is delivered as
  ``blocked``.

When the run passes, its output is shown as text, by default with the last station's own
:meth:`~pydantic_ai_factory.Station.show`, and delivered. :class:`StationSystem` runs one
station the same way, inside a factory of that one station.

With a workspace in the brief, each run's workspace is the snapshot, mounted read-only in
its own Docker container. The agents run in this process; only their file tools reach into
the container, which is removed when the run ends.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.workspaces import WorkspaceBackend, WorkspaceRef
from pydantic_ai_harness.ask_user import TOOL_NAME as ASK_TOOL
from pydantic_ai_harness.docker_sandbox import DockerSandbox, DockerSandboxBackend
from pydantic_core import to_jsonable_python

from ..factory import Factory, Reply, Rework, Run
from ..stations import Question, Station
from ..stores import MemoryStore
from ._contract import (
    MAX_QUESTIONS_PER_CALL,
    ApprovalLog,
    Approved,
    Brief,
    Ports,
    ReportEnd,
    RunReport,
)
from ._sandbox import WORKSPACE, container_args

MAX_ANSWERS = 10
"""How many times one run may wait before the adapter gives up on it."""

APPROVER = "factory-evals"
"""Who the factory records as having made each decision the adapter answers."""

REWORK_RAN_OUT = "rework ran out"
"""The first reason of the escalation a factory raises when its repairs ran out."""

ASK_PERSON = ASK_TOOL
"""The tool an agent asks a person a round of questions with."""

NOBODY_ANSWERS = "Nobody here can answer this call. Go on without it."
"""The result of a deferred tool call that is not a question, should one ever wait."""

Sandbox = Callable[[Path], AbstractAsyncContextManager[AbstractCapability[Any]]]
"""Gives the workspace capability for a snapshot, for the length of one run."""

BuildFactory = Callable[[Model, AbstractCapability[Any] | None], Factory]
"""Builds the factory one run uses, on the run's model and with its workspace, if any."""

BuildStation = Callable[[Model, AbstractCapability[Any] | None], Station[Any, Any]]
"""Builds the station one run uses, on the run's model and with its workspace, if any."""


class RecordingModel(WrapperModel):
    """A model that keeps every response the wrapped model returns, once, as it returns it.

    Every agent of a run uses it, so the run's cost and tokens are added up from
    :attr:`responses`. A resumed conversation is not counted twice: a response is kept
    when it is returned, never when it is read back from a stored conversation.
    """

    def __init__(self, wrapped: Model | str) -> None:
        super().__init__(wrapped)
        self.responses: list[ModelResponse] = []

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        response = await super().request(messages, model_settings, model_request_parameters)
        self.responses.append(response)
        return response

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncGenerator[StreamedResponse]:
        async with super().request_stream(
            messages, model_settings, model_request_parameters, run_context
        ) as stream:
            yield stream
        self.responses.append(stream.get())

    def report(self, end: ReportEnd, detail: str = "") -> RunReport:
        """The run's cost and tokens, from every kept response.

        The cost is ``None`` when any response cannot be priced, as with a scripted model.
        The tokens are counted either way.
        """
        cost: float | None = 0.0
        for response in self.responses:
            try:
                price = float(response.cost().total_price)
            except (LookupError, ValueError, AssertionError):
                cost = None
                break
            cost += price
        return RunReport(
            cost_usd=cost,
            input_tokens=sum(r.usage.input_tokens for r in self.responses),
            output_tokens=sum(r.usage.output_tokens for r in self.responses),
            end=end,
            detail=detail,
        )


@dataclass(kw_only=True)
class RunSandbox(DockerSandbox[Any]):
    """The harness's Docker sandbox, with one container for every agent of one run.

    The plain ``DockerSandbox`` starts a new container for each agent run. This one
    starts a container on first use and gives the same one to every agent of the run,
    across waits, so a run has one container. It is made for one run only, and
    :meth:`remove` removes its container when the run ends.
    """

    _shared: WorkspaceBackend | None = field(default=None, init=False, repr=False, compare=False)

    def get_workspace(
        self, ctx: RunContext[Any], *, ref: WorkspaceRef | None
    ) -> WorkspaceBackend | None:
        if ref is not None:
            return super().get_workspace(ctx, ref=ref)
        if self._shared is None:
            self._shared = super().get_workspace(ctx, ref=None)
        return self._shared

    async def remove(self) -> None:
        """Remove the run's container, if one was started."""
        shared = self._shared
        if isinstance(shared, DockerSandboxBackend) and shared.ref is not None:
            await self.destroy(shared.ref)


def docker_sandbox(image: str) -> Sandbox:
    """A :data:`Sandbox` that mounts a snapshot read-only at ``/workspace`` in ``image``.

    The container has no network, and is removed when the run ends, however it ends.
    """

    @asynccontextmanager
    async def sandbox(snapshot: Path) -> AsyncGenerator[AbstractCapability[Any]]:
        run_sandbox = RunSandbox(
            image=image,
            working_dir=WORKSPACE,
            # The sandbox's own option gives the container no network; Docker refuses two.
            docker_args=container_args(snapshot, network=None),
            allow_internet_access=False,
        )
        try:
            yield run_sandbox
        finally:
            # Shielded, so a run stopped by its time limit still removes its container.
            with anyio.CancelScope(shield=True):
                await run_sandbox.remove()

    return sandbox


@dataclass(frozen=True, kw_only=True)
class FactorySystem:
    """A factory as a system under test.

    ``build`` builds the factory for each run, on a :class:`RecordingModel` over ``model``
    and with the run's workspace, or ``None`` when the brief has none. ``render`` shows
    the output as text; by default the last station's own ``show``. ``sandbox`` gives each
    run's workspace from the brief's snapshot.
    """

    name: str
    model: Model | str
    build: BuildFactory
    sandbox: Sandbox | None = None
    render: Callable[[Any], str] | None = None
    approver: str = APPROVER

    async def run(self, brief: Brief, ports: Ports) -> RunReport:
        """Run the factory on the request, answer what it waits on, and deliver."""
        if not isinstance(ports, ApprovalLog):
            raise TypeError("a factory system needs ports that can log its own approvals")
        recording = RecordingModel(self.model)
        async with AsyncExitStack() as stack:
            workspace: AbstractCapability[Any] | None = None
            if brief.workspace is not None and self.sandbox is not None:
                workspace = await stack.enter_async_context(self.sandbox(brief.workspace))
            factory = self.build(recording, workspace)
            store = MemoryStore()
            run = await factory.run(brief.text, store=store)
            for _ in range(MAX_ANSWERS):
                waiting = run.waiting
                if waiting is None:
                    break
                answer: Any
                if waiting.kind == "tool-calls":
                    answer = await answer_calls(run, ports, by=self.approver)
                elif waiting.kind == "escalation" and waiting.reasons[:1] == (REWORK_RAN_OUT,):
                    await self._deliver(factory, ports, waiting.output, "blocked")
                    return recording.report("finished")
                else:
                    what: Approved = "approval" if waiting.kind == "approval" else "escalation"
                    ports.adapter_approved(what, list(waiting.reasons))
                    answer = run.approve(by=self.approver)
                run = await factory.resume(answer, store=store)
        if run.outcome == "did_not_run":
            return recording.report("infrastructure", _why_not(run))
        if run.outcome == "passed":
            await self._deliver(factory, ports, run.output, "delivered")
        return recording.report("finished")

    async def _deliver(self, factory: Factory, ports: Ports, output: Any, status: Any) -> None:
        text = self.render(output) if self.render is not None else factory.stations[-1].show(output)
        await ports.deliver(text, status, output=to_jsonable_python(output))


@dataclass(frozen=True, kw_only=True)
class StationSystem:
    """One station as a system under test, inside a factory of that station alone.

    When the station has a gate, the factory sends an output that fails it back up to
    ``repairs`` times, then escalates, so a station is run as a factory would run it. Everything
    else is :class:`FactorySystem`'s.
    """

    name: str
    model: Model | str
    build: BuildStation
    repairs: int = 2
    sandbox: Sandbox | None = None
    render: Callable[[Any], str] | None = None
    approver: str = APPROVER

    def factory_system(self) -> FactorySystem:
        """The :class:`FactorySystem` this station runs as."""

        def build(model: Model, workspace: AbstractCapability[Any] | None) -> Factory:
            station = self.build(model, workspace)
            # A station with no gate can never fail, so it is never sent back.
            rework = (
                [Rework(station, limit=self.repairs, on_limit="escalate")]
                if station.gate is not None
                else []
            )
            return Factory(station.name, [station], rework=rework)

        return FactorySystem(
            name=self.name,
            model=self.model,
            build=build,
            sandbox=self.sandbox,
            render=self.render,
            approver=self.approver,
        )

    async def run(self, brief: Brief, ports: Ports) -> RunReport:
        """Run the station's factory on the request, answer what it waits on, and deliver."""
        return await self.factory_system().run(brief, ports)


async def answer_calls(run: Run, ports: Ports, *, by: str = APPROVER) -> Reply:
    """Send each ``ask_user_question`` round to the stakeholder port, and allow the rest.

    A round of more than :data:`MAX_QUESTIONS_PER_CALL` questions goes as several calls,
    in order. A question from any other tool is told that nobody here can answer it.
    """
    answers: dict[Question | Any, Any] = {}
    rounds: dict[str, list[Question]] = {}
    for question in run.questions:
        if question.tool == ASK_PERSON:
            rounds.setdefault(question.call_id, []).append(question)
        else:
            answers[question] = NOBODY_ANSWERS
    for asked in rounds.values():
        for start in range(0, len(asked), MAX_QUESTIONS_PER_CALL):
            chunk = asked[start : start + MAX_QUESTIONS_PER_CALL]
            replies = await ports.ask_stakeholder([question.text for question in chunk])
            answers.update(
                (question, reply.text) for question, reply in zip(chunk, replies, strict=True)
            )
    answers.update((action, True) for action in run.actions)
    return run.reply(answers, by=by)


def _why_not(run: Run) -> str:
    """Why a run did not run, from the step it stopped at."""
    step = run.latest(run.stopped_at) if run.stopped_at is not None else None
    if step is None:
        return "the factory did not run"
    parts = [part for part in (step.error_type, step.reason) if part]
    return ": ".join(parts) or "the factory did not run"
