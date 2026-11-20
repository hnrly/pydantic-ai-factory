"""A methodology is a capability whose evidence a station stores and whose gate it joins."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.output import OutputContext

from pydantic_ai_factory import (
    Artifact,
    AskPerson,
    Evidence,
    Factory,
    MemoryStore,
    Methodology,
    MethodologyDefinitionError,
    MethodologyRef,
    StationContext,
    StationDefinitionError,
    Untyped,
    Verdict,
    escalate,
    failed,
    gate,
    passed,
    station,
)

pytestmark = pytest.mark.anyio

MISSION = "A to-do list web page where I can add tasks and tick them off."
SCENARIO = "Given an empty list, when I add a blank task, then nothing is added."


class Design(BaseModel):
    summary: str
    acceptance_criteria: list[str]


class ScenarioCheck(BaseModel):
    not_scenarios: list[str]


def is_scenario(criterion: str) -> bool:
    words = criterion.lower()
    return words.startswith("given ") and " when " in words and " then " in words


@dataclass
class BehaviourDriven(Methodology):
    name = "behaviour-driven-development"
    version = "1"
    evidence = (ScenarioCheck,)

    def get_instructions(self) -> str:
        return "Write every acceptance criterion as: Given ..., when ..., then ...."

    async def after_output_process(
        self, ctx: RunContext[Any], *, output_context: OutputContext, output: Any
    ) -> Any:
        not_scenarios = [c for c in output.acceptance_criteria if not is_scenario(c)]
        await self.record(ctx, ScenarioCheck(not_scenarios=not_scenarios))
        if not_scenarios:
            raise ModelRetry("Rewrite these as Given/When/Then: " + "; ".join(not_scenarios))
        return output

    def judge(self, output: Artifact[Any], evidence: Evidence) -> Verdict:
        if evidence.of(ScenarioCheck)[-1].payload.not_scenarios:
            return failed("some acceptance criteria are not scenarios")
        return passed("every acceptance criterion is a scenario")


@dataclass
class NeverRecords(Methodology):
    name = "never-records"
    version = "1"
    evidence = (ScenarioCheck,)

    def judge(self, output: Artifact[Any], evidence: Evidence) -> Verdict:
        return passed("never reached")


@dataclass
class AlwaysAsks(Methodology):
    name = "always-asks"
    version = "1"

    def judge(self, output: Artifact[Any], evidence: Evidence) -> Verdict:
        return escalate("a person should look at every design")


class Script:
    """A model that returns structured designs in turn, and counts how often it was asked."""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.calls = 0
        self.instructions: list[str | None] = []

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.calls += 1
        self.instructions.append(info.instructions)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, ModelResponse):
            return reply
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, reply)])


def design(*criteria: str) -> dict[str, Any]:
    return {"summary": "One page, index.html.", "acceptance_criteria": list(criteria)}


def designer(*replies: Any, **options: Any) -> tuple[Agent[None, Design], Script]:
    script = Script(*replies)
    options.setdefault("capabilities", [BehaviourDriven()])
    return Agent(FunctionModel(script), name="designer", output_type=Design, **options), script


async def test_on_an_agent_of_its_own_a_methodology_is_an_ordinary_capability() -> None:
    agent, script = designer(design("Blank tasks are not added."), design(SCENARIO))

    result = await agent.run(MISSION)

    assert result.output.acceptance_criteria == [SCENARIO]
    assert script.calls == 2
    assert "Given ..., when ..., then" in (script.instructions[0] or "")


async def test_in_a_station_its_evidence_is_stored_and_its_gate_judges_the_output() -> None:
    agent, _ = designer(design("Blank tasks are not added."), design(SCENARIO))
    store = MemoryStore()

    result = await station(agent).run(MISSION, store=store)

    assert result.outcome == "passed"
    assert result.decision is not None
    assert (result.decision.gate, result.decision.reasons) == (
        "behaviour-driven-development",
        ("every acceptance criterion is a scenario",),
    )
    checks = [(await store.get(ref, ScenarioCheck)).payload for ref in result.evidence]
    assert [check.not_scenarios for check in checks] == [["Blank tasks are not added."], []]


async def test_every_record_after_the_agent_runs_and_the_decision_carry_its_name() -> None:
    agent, _ = designer(design(SCENARIO))
    store = MemoryStore()

    result = await station(agent).run(MISSION, store=store)

    stamp = MethodologyRef(name="behaviour-driven-development", version="1")
    assert result.decision is not None and result.decision.methodologies == (stamp,)
    assert result.output_ref is not None
    refs = [result.output_ref, *result.evidence, *result.conversations.values()]
    for ref in refs:
        assert (await store.get(ref, Untyped)).methodologies == (stamp,)


async def test_evidence_that_was_never_recorded_means_the_station_did_not_run() -> None:
    agent, _ = designer(design(SCENARIO), capabilities=[NeverRecords()])

    result = await station(agent).run(MISSION, store=MemoryStore())

    assert result.outcome == "did_not_run"
    assert result.not_evaluated is not None
    assert result.not_evaluated.gate == "never-records"
    assert result.not_evaluated.missing == ("ScenarioCheck",)


async def test_a_station_gate_and_a_methodology_gate_join_and_any_failure_fails() -> None:
    @gate(version="1")
    def mentions_index(output: Artifact[Design], evidence: Evidence) -> Verdict:
        if "index.html" in output.payload.summary:
            return passed("the design names the page")
        return failed("the design does not name the page")

    agent, _ = designer({"summary": "One page.", "acceptance_criteria": [SCENARIO]})

    result = await station(agent, gate=mentions_index).run(MISSION, store=MemoryStore())

    assert result.outcome == "failed"
    assert result.decision is not None
    assert result.decision.gate == "designer"
    assert result.decision.version == "mentions_index@1+behaviour-driven-development@1"
    assert result.decision.reasons == ("mentions_index: the design does not name the page",)


async def test_when_every_part_passes_each_reason_names_its_gate() -> None:
    @gate(version="1")
    def anything(output: Artifact[Design], evidence: Evidence) -> Verdict:
        return passed("fine")

    agent, _ = designer(design(SCENARIO))

    result = await station(agent, gate=anything).run(MISSION, store=MemoryStore())

    assert result.decision is not None
    assert result.decision.reasons == (
        "anything: fine",
        "behaviour-driven-development: every acceptance criterion is a scenario",
    )


async def test_an_escalation_with_no_failure_asks_a_person() -> None:
    agent, _ = designer(design(SCENARIO), capabilities=[BehaviourDriven(), AlwaysAsks()])

    run = await Factory("software", [station(agent)]).run(MISSION, store=MemoryStore())

    assert run.waiting is not None and run.waiting.kind == "escalation"
    assert run.waiting.reasons == ("always-asks: a person should look at every design",)


async def test_recording_a_type_the_methodology_does_not_list_is_refused() -> None:
    class Note(BaseModel):
        text: str

    @dataclass
    class Careless(Methodology):
        name = "careless"
        version = "1"

        async def after_output_process(
            self, ctx: RunContext[Any], *, output_context: OutputContext, output: Any
        ) -> Any:
            await self.record(ctx, Note(text="not listed"))
            return output

        def judge(self, output: Artifact[Any], evidence: Evidence) -> Verdict:
            return passed("never reached")

    agent, _ = designer(design(SCENARIO), capabilities=[Careless()])

    result = await station(agent).run(MISSION, store=MemoryStore())

    assert result.outcome == "did_not_run"
    assert result.error_type == "MethodologyDefinitionError"
    assert "evidence lists nothing" in (result.reason or "")


def test_a_methodology_without_a_name_or_a_judge_is_refused_when_declared() -> None:
    with pytest.raises(MethodologyDefinitionError) as raised:

        class Unnamed(Methodology):  # pyright: ignore[reportUnusedClass]
            evidence = (ScenarioCheck,)

    assert raised.value.findings == [
        "set name to a string, such as name = '1'",
        "set version to a string, such as version = '1'",
        "define judge(self, output, evidence) to return a Verdict",
    ]


async def test_the_methodology_rounds_are_added_to_the_agents_own_retry_budget() -> None:
    @dataclass
    class TwoRounds(BehaviourDriven):
        name = "two-rounds"
        output_retries = 2

    replies = (design("Blank tasks are not added."), design("Ticks survive."), design(SCENARIO))
    alone, _ = designer(*replies, capabilities=[TwoRounds()])
    with pytest.raises(UnexpectedModelBehavior):
        await alone.run(MISSION)

    in_station, script = designer(*replies, capabilities=[TwoRounds()])
    result = await station(in_station).run(MISSION, store=MemoryStore())

    assert (result.outcome, script.calls) == ("passed", 3)


async def test_a_replayed_agent_call_still_gives_its_methodology_evidence() -> None:
    agent, drafted = designer(design(SCENARIO))
    script = Script(
        ModelResponse(parts=[ToolCallPart("ask_person", {"question": "Keep ticks?"}, "q1")]),
        design(SCENARIO),
    )
    reviewer = Agent(
        FunctionModel(script), name="reviewer", output_type=Design, capabilities=[AskPerson()]
    )

    @station
    async def shape(ctx: StationContext, mission: str) -> Design:
        drafted = await ctx.run_agent(agent, mission)
        reviewed = await ctx.run_agent(reviewer, drafted.output.summary)
        return reviewed.output

    software = Factory("software", [shape])
    store = MemoryStore()
    run = await software.run(MISSION, store=store)
    assert run.waiting is not None and run.waiting.requests is not None
    call = run.waiting.requests.calls[0]

    results = run.waiting.requests.build_results(calls={call.tool_call_id: "Yes."})
    done = await software.resume(run.reply(by="ada", results=results), store=store)

    assert done.outcome == "passed"
    assert drafted.calls == 1
    last = done.steps[-1]
    assert last.decision is not None and last.decision.gate == "behaviour-driven-development"
    assert len(last.evidence) == 1
    assert last.calls[0].evidence == last.evidence


def test_a_factory_lists_each_stations_methodologies_before_anything_runs() -> None:
    method = BehaviourDriven()
    with_method, _ = designer(design(SCENARIO), capabilities=[method])
    without, _ = designer(design(SCENARIO), capabilities=[])

    software = Factory("software", [station(with_method)])
    plain = Factory("software", [station(without)])

    assert software.methodologies() == {"designer": (method,)}
    assert plain.methodologies() == {"designer": ()}
    assert software.digest != plain.digest


def test_one_methodology_on_two_agents_in_a_station_is_joined_once() -> None:
    first, _ = designer(design(SCENARIO))
    second = Agent(
        FunctionModel(Script(design(SCENARIO))), name="second", capabilities=[BehaviourDriven()]
    )

    @station(agents=[first, second])
    async def both(ctx: StationContext, mission: str) -> str:
        return mission

    assert [str(m.ref) for m in both.methodologies] == ["behaviour-driven-development@1"]


def test_a_station_refuses_two_versions_of_one_methodology() -> None:
    @dataclass
    class Newer(BehaviourDriven):
        version = "2"

    first, _ = designer(design(SCENARIO))
    second = Agent(FunctionModel(Script(design(SCENARIO))), name="second", capabilities=[Newer()])

    with pytest.raises(StationDefinitionError, match="versions '1' and '2'"):

        @station(agents=[first, second])
        async def both(ctx: StationContext, mission: str) -> str:  # pyright: ignore[reportUnusedFunction]
            return mission
