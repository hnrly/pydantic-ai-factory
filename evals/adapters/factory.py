"""The factory adapter: the Design station at full strength, run through the ports.

This is the only module in ``evals/`` that imports the factory. It builds both factory
arms from the software package's public names and assembles nothing itself:

* ``factory`` is the shipped ``SoftwareFactory(model, workspace=sandbox)``, unchanged, so
  the benchmark measures what users install;
* ``factory-simple`` is the same factory with one station swapped:
  ``design=design_station(model, method=ElephantGoldfish(goldfish=()), workspace=sandbox)``.
  It keeps the designer's instructions, skills, design shape, questions in rounds, gate
  and repairs, and leaves out only the review.

The mission is the request, unchanged. The adapter then answers what the run waits on,
up to :data:`MAX_ANSWERS` times:

* a round of questions the designer asked with ``ask_person`` goes to the stakeholder
  port as one call, and the replies, in order, are the tool's result;
* an approval of the design is given at once, and logged as ``adapter_approved``;
* an escalation after rework ran out is not answered: the last draft is delivered as
  blocked;
* any other escalation is approved at once, and logged as ``adapter_approved`` with its
  reasons, so the run passes with the designer's draft.

There is no approval port, so these approvals are not port calls. They stand in for a
person who accepts what the station shows them. When the run passes, the adapter renders
the typed ``Design`` as markdown with the factory's own ``render_markdown`` and delivers
it. Nothing in the benchmark reads the document's shape.

Each run's workspace is the case's snapshot, mounted read-only in its own Docker
container. The agents run in this process; only their file tools reach into the
container, which is removed when the run ends.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import anyio
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults
from pydantic_ai.workspaces import WorkspaceBackend, WorkspaceRef
from pydantic_ai_harness.docker_sandbox import DockerSandbox, DockerSandboxBackend

from evals.contract import ApprovalLog, Brief, Ports, ReportEnd, RunReport
from evals.sandbox import WORKSPACE, container_args
from evals.version import SANDBOX_IMAGE
from pydantic_ai_factory import MemoryStore, Run
from pydantic_ai_factory.methodologies import ElephantGoldfish
from pydantic_ai_factory_software import Design, SoftwareFactory, design_station, render_markdown

MAX_ANSWERS = 10
"""How many times one run may wait before the adapter gives up on it."""

APPROVER = "factory-evals"
"""Who the factory records as having made each decision the adapter answers."""

REWORK_RAN_OUT = "rework ran out"
"""The first reason of the escalation a factory raises when its repairs ran out."""

ASK_PERSON = "ask_person"
"""The designer's tool for asking a person a round of questions."""

NOBODY_ANSWERS = "Nobody here can answer this call. Go on without it."
"""The result of a deferred tool call that is not a question, should one ever wait."""

FACTORY_ARM = "factory"
SIMPLE_ARM = "factory-simple"

Sandbox = Callable[[Path], AbstractAsyncContextManager[AbstractCapability[Any]]]
"""Gives the workspace capability for a snapshot, for the length of one run."""


class RecordingModel(WrapperModel):
    """A model that keeps every response the wrapped model returns, once, as it returns it.

    The designer and, because they use the designer's model, the goldfish all run on it,
    so the run's cost and tokens are added up from :attr:`responses`. A resumed
    conversation is not counted twice: a response is kept when it is returned, never when
    it is read back from a stored conversation.
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
    starts a container on first use and gives the same one to the designer and every
    goldfish, across waits, so a run has one container. It is made for one run only, and
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


@asynccontextmanager
async def docker_sandbox(snapshot: Path) -> AsyncGenerator[AbstractCapability[Any]]:
    """The run's workspace: ``snapshot`` mounted read-only at ``/workspace``, with no network.

    The container is removed when the run ends, however it ends.
    """
    sandbox = RunSandbox(
        image=SANDBOX_IMAGE,
        working_dir=WORKSPACE,
        # The sandbox's own option gives the container no network; Docker refuses two.
        docker_args=container_args(snapshot, network=None),
        allow_internet_access=False,
    )
    try:
        yield sandbox
    finally:
        # Shielded, so a run stopped by its time limit still removes its container.
        with anyio.CancelScope(shield=True):
            await sandbox.remove()


def factory_arm(
    model: Model | str, workspace: AbstractCapability[Any], *, review: bool
) -> SoftwareFactory:
    """The factory one arm runs, built from the software package's public names only.

    With ``review``, the shipped ``SoftwareFactory`` unchanged. Without it, the same
    factory whose Design station's method is Elephant Goldfish with no goldfish: the same
    designer, offered the same skills, with no review.
    """
    if review:
        return SoftwareFactory(model, workspace=workspace)
    simple = design_station(model, method=ElephantGoldfish(goldfish=()), workspace=workspace)
    return SoftwareFactory(model, workspace=workspace, design=simple)


class FactoryAdapter:
    """The Design station at full strength, as the arm ``factory`` or ``factory-simple``.

    ``model`` runs the designer and, in the arm with review, the goldfish. ``sandbox``
    gives each run its workspace; by default :func:`docker_sandbox`.
    """

    def __init__(
        self, model: Model | str, *, review: bool, sandbox: Sandbox = docker_sandbox
    ) -> None:
        self._model = model
        self._review = review
        self._sandbox = sandbox

    @property
    def name(self) -> str:
        """``factory`` with review, ``factory-simple`` without."""
        return FACTORY_ARM if self._review else SIMPLE_ARM

    async def run(self, brief: Brief, ports: Ports) -> RunReport:
        """Run the factory on the request, answer what it waits on, and deliver."""
        if not isinstance(ports, ApprovalLog):
            raise TypeError("the factory adapter needs ports that can log its own approvals")
        recording = RecordingModel(self._model)
        async with self._sandbox(brief.snapshot) as workspace:
            software = factory_arm(recording, workspace, review=self._review)
            store = MemoryStore()
            run = await software.run(brief.text, store=store)
            for _ in range(MAX_ANSWERS):
                waiting = run.waiting
                if waiting is None:
                    break
                answer: Any
                if waiting.kind == "tool-calls":
                    answer = await _answer_calls(waiting.requests, ports)
                elif waiting.kind == "escalation" and waiting.reasons[:1] == (REWORK_RAN_OUT,):
                    await ports.deliver(_rendered(waiting.output), "blocked")
                    return recording.report("finished")
                else:
                    what: Literal["design", "escalation"] = (
                        "design" if waiting.kind == "approval" else "escalation"
                    )
                    ports.adapter_approved(what, list(waiting.reasons))
                    answer = run.approve(by=APPROVER)
                run = await software.resume(run, answer, store=store)
        if run.outcome == "did_not_run":
            return recording.report("infrastructure", _why_not(run))
        if run.outcome == "passed":
            await ports.deliver(_rendered(run.output), "delivered")
        return recording.report("finished")


async def _answer_calls(requests: DeferredToolRequests | None, ports: Ports) -> DeferredToolResults:
    """Send each ``ask_person`` round to the stakeholder port as one call, and approve the rest."""
    if requests is None:
        return DeferredToolResults()
    calls: dict[str, Any] = {}
    for call in requests.calls:
        if call.tool_name != ASK_PERSON:
            calls[call.tool_call_id] = NOBODY_ANSWERS
            continue
        asked = requests.metadata.get(call.tool_call_id, {}).get("questions", [])
        questions = [str(question["text"]) for question in asked]
        replies = await ports.ask_stakeholder(questions)
        calls[call.tool_call_id] = [reply.text for reply in replies]
    approvals = {call.tool_call_id: True for call in requests.approvals}
    return requests.build_results(calls=calls, approvals=approvals)


def _rendered(output: Any) -> str:
    """The design as markdown, through the factory's own template."""
    design = output if isinstance(output, Design) else Design.model_validate(output)
    return render_markdown(design)


def _why_not(run: Run) -> str:
    """Why a run did not run, from the step it stopped at."""
    step = run.latest(run.stopped_at) if run.stopped_at is not None else None
    if step is None:
        return "the factory did not run"
    parts = [part for part in (step.error_type, step.reason) if part]
    return ": ".join(parts) or "the factory did not run"
