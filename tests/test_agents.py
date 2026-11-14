"""Stations run Pydantic AI agents through ctx.run_agent, which keeps their conversations."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel
from pydantic_ai import Agent, Conversation
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from pydantic_ai_factory import (
    Artifact,
    Evidence,
    Factory,
    MemoryStore,
    Rework,
    StationContext,
    StationDefinitionError,
    StationInputInvalid,
    Verdict,
    failed,
    gate,
    passed,
    station,
)
from pydantic_ai_factory.stations._station import replayed_result

pytestmark = pytest.mark.anyio


def replying(*replies: str) -> FunctionModel:
    """A model that gives ``replies`` in turn, then repeats the last one."""
    queue = list(replies)

    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        text = queue.pop(0) if len(queue) > 1 else queue[0]
        return ModelResponse(parts=[TextPart(text)])

    return FunctionModel(reply)


def prompts_seen(messages: list[ModelMessage]) -> list[str]:
    return [
        str(part.content)
        for message in messages
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]


designer = Agent(TestModel(custom_output_text="One page, index.html."), name="designer")
builder = Agent(TestModel(custom_output_text="Built it."), name="builder")
reviewer = Agent(TestModel(custom_output_text="Nothing is wrong."), name="reviewer")


@station
async def build(ctx: StationContext, design: str) -> str:
    await ctx.run_agent(builder, design)
    review = await ctx.run_agent(reviewer, design)
    return review.output


def test_an_agent_becomes_a_station_that_takes_a_prompt_and_hands_on_its_output() -> None:
    design = station(designer)

    result = design.run_sync("A to-do list page.", store=MemoryStore())

    assert (design.name, design.input_type, design.output_type) == ("designer", str, str)
    assert design.agents == (designer,)
    assert result.outcome == "not_judged"


def test_a_station_finds_the_agents_its_code_refers_to() -> None:
    assert build.agents == (builder, reviewer)


def test_a_station_finds_agents_in_its_closure_and_in_functions_defined_inside_it() -> None:
    local = Agent(TestModel(), name="local")

    @station
    async def nested(ctx: StationContext, text: str) -> str:
        async def inner() -> str:
            return (await ctx.run_agent(local, text)).output

        await ctx.run_agent(designer, text)
        return await inner()

    assert {agent.name for agent in nested.agents} == {"local", "designer"}


def test_agents_listed_by_hand_replace_the_ones_inferred() -> None:
    @station(agents=[reviewer])
    async def only_review(ctx: StationContext, text: str) -> str:
        return text

    assert only_review.agents == (reviewer,)


async def test_running_an_agent_the_station_does_not_name_stops_it_and_says_how_to_fix_it() -> None:
    by_kind = {"bug": Agent(TestModel(), name="bug_fixer")}

    @station
    async def triage(ctx: StationContext, kind: str) -> str:
        return (await ctx.run_agent(by_kind[kind], kind)).output

    result = await triage.run("bug", store=MemoryStore())

    assert result.outcome == "did_not_run"
    assert result.error_type == "StationDefinitionError"
    assert result.reason is not None and "add the agent to @station(agents=[...])" in result.reason


def test_a_station_refuses_agents_without_names_or_with_the_same_name() -> None:
    nameless = Agent(TestModel())
    twin = Agent(TestModel(), name="designer")

    with pytest.raises(StationDefinitionError, match="every agent needs a name"):
        station(agents=[nameless])(build.function)
    with pytest.raises(StationDefinitionError, match="two agents are named 'designer'"):
        station(agents=[designer, twin])(build.function)


async def test_each_agents_conversation_is_kept_by_its_name() -> None:
    store = MemoryStore()

    result = await build.run("One page, index.html.", store=store)

    assert set(result.conversations) == {"builder", "reviewer"}
    kept = await store.get(result.conversations["reviewer"], Conversation)
    assert kept.ref.type == "Conversation:reviewer"
    assert prompts_seen(kept.payload.messages) == ["One page, index.html."]


async def test_a_second_run_of_the_same_agent_in_one_attempt_carries_on_its_conversation() -> None:
    talker = Agent(replying("first", "second"), name="talker")

    @station
    async def twice(ctx: StationContext, text: str) -> str:
        await ctx.run_agent(talker, "one")
        return (await ctx.run_agent(talker, "two")).output

    store = MemoryStore()
    result = await twice.run("x", store=store)

    kept = await store.get(result.conversations["talker"], Conversation)
    assert prompts_seen(kept.payload.messages) == ["one", "two"]


async def test_on_rework_the_agent_carries_on_its_conversation_and_is_told_what_was_wrong() -> None:
    writer = Agent(replying("A page.", "A page. Blank tasks are not added."), name="writer")

    @gate(version="1")
    def mentions_blank_tasks(output: Artifact[str], evidence: Evidence) -> Verdict:
        if "blank" in output.payload.lower():
            return passed("the design says what happens to a blank task")
        return failed("the design never says what happens to a blank task")

    design = station(writer, gate=mentions_blank_tasks)
    software = Factory("software", [design], rework=[Rework(design, limit=2)])
    store = MemoryStore()

    run = await software.run("A to-do list page.", store=store)

    assert [step.outcome for step in run.steps] == ["failed", "passed"]
    assert run.output == "A page. Blank tasks are not added."
    kept = await store.get(run.steps[-1].conversations["writer"], Conversation)
    assert prompts_seen(kept.payload.messages) == [
        "A to-do list page.",
        "Your last output did not pass its checks.\n"
        "- the design never says what happens to a blank task",
    ]


async def test_the_context_passes_other_arguments_to_the_agent_run() -> None:
    seen: list[Any] = []
    greeter = Agent(TestModel(), name="greeter", deps_type=str)

    @greeter.instructions
    def say_deps(ctx: Any) -> str:
        seen.append(ctx.deps)
        return "Be brief."

    @station
    async def greet(ctx: StationContext, text: str) -> str:
        return (await ctx.run_agent(greeter, text, deps="from the station")).output

    await greet.run("hi", store=MemoryStore())

    assert seen == ["from the station"]


async def test_the_context_refuses_arguments_it_sets_itself() -> None:
    @station
    async def meddle(ctx: StationContext, text: str) -> str:
        return (await ctx.run_agent(designer, text, message_history=[])).output

    result = await meddle.run("x", store=MemoryStore())

    assert result.error_type == StationInputInvalid.__name__
    assert result.reason is not None and "sets message_history itself" in result.reason


def test_a_factory_lists_every_agent_before_anything_runs() -> None:
    software = Factory("software", [station(designer), build])

    assert software.agents() == {"designer": (designer,), "build": (builder, reviewer)}


class Task(BaseModel):
    text: str


def test_a_station_may_take_and_return_any_type_pydantic_can_serialise() -> None:
    @station
    async def split(ctx: StationContext, text: str) -> list[Task]:
        return [Task(text=line) for line in text.splitlines()]

    software = Factory("software", [split])

    run = software.run_sync("buy milk\nwalk the dog", store=MemoryStore())

    assert run.output == [Task(text="buy milk"), Task(text="walk the dog")]
    assert run.output_ref is not None and run.output_ref.type == "list"


def test_a_station_refuses_the_wrong_type_of_input_before_anything_runs() -> None:
    with pytest.raises(StationInputInvalid, match="expected str, not int"):
        station(designer).run_sync(3, store=MemoryStore())  # pyright: ignore[reportArgumentType]


def test_a_replayed_result_reads_like_the_run_it_replays() -> None:
    # replayed_result builds AgentRunResult from Pydantic AI's private agent state. This
    # pins the parts a station reads, so an upgrade that changes them fails here first.
    original = Agent(TestModel(custom_output_text="Built."), name="builder").run_sync("go")

    replayed = replayed_result(original.output, original.conversation)

    assert replayed.output == "Built."
    assert replayed.all_messages() == original.all_messages()
    assert replayed.usage == original.usage
    assert replayed.conversation.messages == original.conversation.messages
    assert replayed.conversation.conversation_id == original.conversation.conversation_id
