"""A run leaves a trace: a span for the run, each attempt, agent run, gate and answer."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from asking import ask
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from pydantic_ai import Agent
from pydantic_ai.capabilities import Instrumentation
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from queued import queued_reply

from pydantic_ai_factory import (
    ApprovalRefused,
    Artifact,
    AskPerson,
    Evidence,
    Factory,
    MemoryStore,
    Mission,
    Rework,
    StationContext,
    Verdict,
    failed,
    gate,
    passed,
    station,
)
from pydantic_ai_factory.methodologies import ElephantGoldfish, Goldfish

pytestmark = pytest.mark.anyio

MISSION = "A to-do list web page where I can add tasks and tick them off."
DESIGN = "One page. Type a task and press Enter to add it; tick its box to mark it done."
FIXED = DESIGN + " Pressing Enter on a blank task adds nothing."

EXPORTER = InMemorySpanExporter()
ATTACHED: list[InMemorySpanExporter] = []


@pytest.fixture(autouse=True)
def spans() -> Iterator[InMemorySpanExporter]:
    # OpenTelemetry lets a process set its tracer provider once, so the tests share one.
    # Another test module may have set it first, so the exporter is attached to whichever
    # provider is in place, once.
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    if not ATTACHED:
        provider.add_span_processor(SimpleSpanProcessor(EXPORTER))
        ATTACHED.append(EXPORTER)
    EXPORTER.clear()
    yield EXPORTER
    EXPORTER.clear()


def replies(*texts: str | ModelResponse) -> FunctionModel:
    queue = list(texts)

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        return reply if isinstance(reply, ModelResponse) else ModelResponse(parts=[TextPart(reply)])

    return FunctionModel(respond)


def named(title: str) -> list[ReadableSpan]:
    return [span for span in EXPORTER.get_finished_spans() if span.name == title]


def one(title: str) -> ReadableSpan:
    [found] = named(title)
    return found


def attributes(span: ReadableSpan) -> dict[str, Any]:
    return dict(span.attributes or {})


def ident(span: ReadableSpan) -> int:
    context = span.context
    assert context is not None
    return context.span_id


def parent_of(span: ReadableSpan) -> ReadableSpan | None:
    if span.parent is None:
        return None
    return next(s for s in EXPORTER.get_finished_spans() if ident(s) == span.parent.span_id)


def ancestors(span: ReadableSpan) -> list[str]:
    chain: list[str] = []
    current = parent_of(span)
    while current is not None:
        chain.append(current.name)
        current = parent_of(current)
    return chain


def every_value() -> str:
    """Every attribute value on every span, as one text, to check what was left out."""
    return "\n".join(
        str(value) for span in EXPORTER.get_finished_spans() for value in attributes(span).values()
    )


@gate(version="1")
def blank_task_rule(output: Artifact[str], evidence: Evidence) -> Verdict:
    if "blank task" in output.payload:
        return passed("it says what a blank task does")
    return failed("it does not say what a blank task does")


async def test_a_run_is_one_trace_with_a_span_for_the_run_the_station_its_agent_and_its_gate() -> (
    None
):
    designer = Agent(replies(FIXED), name="designer")
    software = Factory("software", [station(designer, gate=blank_task_rule)], version="2")

    run = await software.run(MISSION, store=MemoryStore(), run_id="run-1")

    factory, attempt = one("factory software"), one("station designer")
    agent, judged = one("agent designer"), one("gate blank_task_rule")
    assert ancestors(agent) == ["station designer", "factory software"]
    assert ancestors(judged) == ["station designer", "factory software"]
    assert len({span.context.trace_id for span in (factory, attempt, agent, judged)}) == 1  # pyright: ignore[reportOptionalMemberAccess]
    assert factory.instrumentation_scope is not None
    assert factory.instrumentation_scope.name == "pydantic_ai_factory"
    assert run.mission_id is not None and run.output_ref is not None
    assert attributes(factory) == {
        "factory.name": "software",
        "factory.version": "2",
        "factory.run.id": "run-1",
        "factory.mission.id": run.mission_id,
        "factory.mission.objective": MISSION,
        "factory.outcome": "passed",
        "factory.finished": True,
        "factory.ended": True,
        "factory.attempts": 1,
        "factory.output": run.output_ref.digest,
    }
    step = run.steps[0]
    assert step.output_ref is not None
    assert attributes(attempt) == {
        "factory.station.name": "designer",
        "factory.station.version": "0",
        "factory.station.attempt": 1,
        "factory.run.id": "run-1",
        "factory.station.outcome": "passed",
        "factory.station.ran": True,
        "factory.station.passed": True,
        "factory.station.input": step.input_ref.digest,
        "factory.station.output": step.output_ref.digest,
    }
    assert attributes(agent) == {
        "factory.agent.name": "designer",
        "factory.agent.attempt": 1,
        "factory.run.id": "run-1",
        "factory.agent.conversation": step.conversations["designer"].digest,
    }
    assert attributes(judged) == {
        "factory.gate.name": "blank_task_rule",
        "factory.gate.version": "1",
        "factory.gate.judged": (step.output_ref.digest,),
        "factory.run.id": "run-1",
        "factory.gate.judgement": "passed",
        "factory.gate.reasons": ("it says what a blank task does",),
    }
    assert all(span.status.status_code is StatusCode.UNSET for span in (factory, attempt))


async def test_artifacts_are_recorded_as_digests_and_never_as_content() -> None:
    designer = Agent(replies(FIXED), name="designer")
    software = Factory("software", [station(designer)])

    await software.run(MISSION, store=MemoryStore())

    assert FIXED not in every_value()


async def test_an_attribute_with_no_value_is_left_off() -> None:
    designer = Agent(replies(DESIGN), name="designer")
    software = Factory("software", [station(designer)])

    await software.run(DESIGN, store=MemoryStore())

    attempt, factory = attributes(one("station designer")), attributes(one("factory software"))
    assert "factory.station.passed" not in attempt
    assert "factory.station.evidence" not in attempt
    assert "factory.methodologies" not in attempt
    assert "factory.stopped_at" not in factory
    assert "factory.waiting.kind" not in factory
    assert "factory.resumed" not in factory


async def test_a_gate_that_fails_the_work_is_not_an_error() -> None:
    designer = Agent(replies(DESIGN, FIXED), name="designer")
    design = station(designer, gate=blank_task_rule)
    software = Factory("software", [design], rework=[Rework(design, limit=1)])

    run = await software.run(MISSION, store=MemoryStore())

    assert run.outcome == "passed"
    first, second = named("gate blank_task_rule")
    assert attributes(first)["factory.gate.judgement"] == "failed"
    assert attributes(first)["factory.gate.reasons"] == ("it does not say what a blank task does",)
    attempts = named("station designer")
    assert [attributes(span)["factory.station.attempt"] for span in attempts] == [1, 2]
    assert attributes(attempts[0])["factory.station.passed"] is False
    assert attributes(one("factory software"))["factory.attempts"] == 2
    spans = [first, second, *attempts, one("factory software")]
    assert all(span.status.status_code is StatusCode.UNSET for span in spans)


async def test_a_station_that_raises_marks_its_attempt_and_the_run_as_errors() -> None:
    @station
    async def design(ctx: StationContext, mission: str) -> str:
        raise RuntimeError("the page template is missing")

    software = Factory("software", [design])

    run = await software.run(MISSION, store=MemoryStore())

    assert run.outcome == "did_not_run"
    attempt, factory = one("station design"), one("factory software")
    assert attempt.status.status_code is StatusCode.ERROR
    assert attributes(attempt)["factory.station.ran"] is False
    assert attributes(attempt)["factory.station.error_type"] == "RuntimeError"
    assert factory.status.status_code is StatusCode.ERROR
    assert attributes(factory)["factory.stopped_at"] == "design"
    assert attributes(factory)["factory.ended"] is True


async def test_a_gate_that_could_not_be_evaluated_is_an_error_naming_what_was_missing() -> None:
    class Checklist(str):
        pass

    @gate(version="1", requires=[Checklist])
    def checked(output: Artifact[str], evidence: Evidence) -> Verdict:
        return passed("checked")

    designer = Agent(replies(DESIGN), name="designer")
    software = Factory("software", [station(designer, gate=checked)])

    await software.run(MISSION, store=MemoryStore())

    judged = one("gate checked")
    assert judged.status.status_code is StatusCode.ERROR
    assert attributes(judged)["factory.gate.not_evaluated"] == "missing evidence: Checklist"
    assert attributes(judged)["factory.gate.missing"] == ("Checklist",)
    assert "factory.gate.judgement" not in attributes(judged)
    assert one("station designer").status.status_code is StatusCode.ERROR


async def test_an_exception_that_escapes_a_run_marks_it_as_an_error() -> None:
    designer = Agent(replies(DESIGN), name="designer")
    software = Factory("software", [station(designer, approval=True)])
    store = MemoryStore()
    run = await software.run(MISSION, store=store)
    EXPORTER.clear()

    with pytest.raises(ApprovalRefused):
        await software.resume(queued_reply(run), store=store)

    factory = one("factory software")
    assert factory.status.status_code is StatusCode.ERROR
    assert factory.events[0].name == "exception"


async def test_running_and_resuming_are_two_traces_that_share_the_run_id() -> None:
    designer = Agent(replies(FIXED), name="designer")
    software = Factory("software", [station(designer, approval=True)])
    store = MemoryStore()

    run = await software.run(Mission(MISSION), store=store)
    waiting = attributes(one("factory software"))
    EXPORTER.clear()
    done = await software.resume(run.approve(by="ada", note="Ship it."), store=store)

    assert done.outcome == "passed"
    assert run.waiting is not None and run.waiting.artifact is not None
    assert waiting["factory.outcome"] == "waiting"
    assert waiting["factory.ended"] is False
    assert waiting["factory.waiting.kind"] == "approval"
    assert waiting["factory.waiting.artifact"] == run.waiting.artifact.digest
    resumed = attributes(one("factory software"))
    assert resumed["factory.run.id"] == waiting["factory.run.id"] == run.run_id
    assert resumed["factory.resumed"] is True
    assert resumed["factory.mission.objective"] == MISSION
    assert resumed["factory.ended"] is True
    answer = one("answer approval approved")
    assert ancestors(answer) == ["factory software"]
    assert attributes(answer) == {
        "factory.answer.kind": "approval",
        "factory.answer.decision": "approved",
        "factory.answer.by": "ada",
        "factory.answer.artifact": run.waiting.artifact.digest,
        "factory.answer.overrode": False,
        "factory.station.name": "designer",
        "factory.run.id": run.run_id,
    }
    assert answer.status.status_code is StatusCode.UNSET
    assert "Ship it." not in every_value()


async def test_a_request_for_changes_records_who_asked_but_not_their_note() -> None:
    designer = Agent(replies(DESIGN, FIXED), name="designer")
    software = Factory("software", [station(designer, approval=True)])
    store = MemoryStore()
    run = await software.run(MISSION, store=store)

    note = "Say what happens to a blank task."
    await software.resume(run.request_changes(by="ada", note=note), store=store)

    answer = one("answer approval changes_requested")
    assert attributes(answer)["factory.answer.by"] == "ada"
    assert note not in every_value()


async def test_answered_tool_calls_are_an_answer_span_without_the_reply_text() -> None:
    question = ask("Keep ticked tasks?", call_id="q1")
    designer = Agent(replies(question, FIXED), name="designer", capabilities=[AskPerson()])
    software = Factory("software", [station(designer)])
    store = MemoryStore()
    run = await software.run(MISSION, store=store)
    assert run.waiting is not None and run.waiting.requests is not None
    waiting_agent = one("agent designer")
    EXPORTER.clear()

    reply = "Yes, after a reload too."
    done = await software.resume(run.reply(reply, by="ada"), store=store)

    assert done.outcome == "passed"
    assert waiting_agent.status.status_code is StatusCode.UNSET
    assert run.waiting.conversation is not None
    answered = one("answer tool-calls answered")
    assert attributes(answered) == {
        "factory.answer.kind": "tool-calls",
        "factory.answer.decision": "answered",
        "factory.answer.by": "ada",
        "factory.answer.artifact": run.waiting.conversation.digest,
        "factory.station.name": "designer",
        "factory.run.id": run.run_id,
    }
    assert reply not in every_value()


async def test_an_agent_run_that_raises_marks_its_span_as_an_error() -> None:
    def broken(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ConnectionError("the model is unreachable")

    designer = Agent(FunctionModel(broken), name="designer")
    software = Factory("software", [station(designer)])

    await software.run(MISSION, store=MemoryStore())

    agent = one("agent designer")
    assert agent.status.status_code is StatusCode.ERROR
    assert "ConnectionError" in (agent.status.description or "")


async def test_pydantic_ai_spans_sit_inside_the_agent_span() -> None:
    designer = Agent(replies(FIXED), name="designer", capabilities=[Instrumentation()])
    software = Factory("software", [station(designer)])

    await software.run(MISSION, store=MemoryStore())

    inside = [
        span
        for span in EXPORTER.get_finished_spans()
        if span.instrumentation_scope is not None
        and span.instrumentation_scope.name != "pydantic_ai_factory"
    ]
    assert inside
    assert all("agent designer" in ancestors(span) for span in inside)


async def test_goldfish_reviewing_a_draft_trace_inside_the_station_that_ran_the_author() -> None:
    def pond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        reply: dict[str, Any] = {"findings": []}
        if "comprehension" in (info.instructions or "").lower():
            reply["paraphrase"] = "A page that keeps a list of tasks you can tick off."
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, reply)])

    method = ElephantGoldfish(
        goldfish=(Goldfish("newcomer", stage="comprehension", job="Explain it back."),),
        model=FunctionModel(pond),
    )
    designer = Agent(replies(FIXED), name="designer", capabilities=[method])
    software = Factory("software", [station(designer)])
    Agent.instrument_all(True)
    try:
        run = await software.run(MISSION, store=MemoryStore())
    finally:
        Agent.instrument_all(False)

    assert run.outcome == "passed"
    goldfish = [s for s in EXPORTER.get_finished_spans() if "goldfish-newcomer" in s.name]
    assert goldfish
    assert all("station designer" in ancestors(span) for span in goldfish)
    assert attributes(one("agent designer"))["factory.methodologies"] == ("elephant-goldfish@1",)
    assert attributes(one("station designer"))["factory.methodologies"] == ("elephant-goldfish@1",)
    evidence = attributes(one("station designer"))["factory.station.evidence"]
    assert evidence == tuple(ref.digest for ref in run.steps[0].evidence)
