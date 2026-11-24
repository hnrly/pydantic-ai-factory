"""A run waits for a person to approve, settle an escalation or answer an agent, and carries on."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from asking import ASK, ask, ask_call
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from queued import queued_reply

from pydantic_ai_factory import (
    ApprovalRefused,
    Artifact,
    AskPerson,
    DefinitionError,
    Escalate,
    Evidence,
    Factory,
    LocalStore,
    MemoryStore,
    Rework,
    RunConflict,
    StationContext,
    Verdict,
    escalate,
    failed,
    gate,
    passed,
    station,
)

pytestmark = pytest.mark.anyio


class Script:
    """A model that answers in turn and counts how often it was asked."""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.calls = 0
        self.seen: list[list[ModelMessage]] = []

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.calls += 1
        self.seen.append(list(messages))
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, ModelResponse):
            return reply
        return ModelResponse(parts=[TextPart(reply)])


def agent(name: str, *replies: Any, **options: Any) -> tuple[Agent[None, str], Script]:
    script = Script(*replies)
    return Agent(FunctionModel(script), name=name, **options), script


def prompts(messages: list[ModelMessage]) -> list[str]:
    return [
        str(part.content)
        for message in messages
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]


async def test_a_station_with_approval_waits_for_a_person_and_carries_on_when_approved() -> None:
    designer, _ = agent("designer", "One page.")
    builder, _ = agent("builder", "Built.")
    software = Factory("software", [station(designer, approval=True), station(builder)])
    store = MemoryStore()

    run = await software.run("A to-do list page.", store=store)

    assert (run.outcome, run.stopped_at) == ("waiting", "designer")
    assert run.waiting is not None
    assert (run.waiting.kind, run.waiting.output) == ("approval", "One page.")
    assert run.latest("builder") is None

    done = await software.resume(run.approve(by="ada"), store=store)

    assert (done.outcome, done.output) == ("passed", "Built.")
    assert [a.model_dump(include={"kind", "by", "overrode"}) for a in done.answers] == [
        {"kind": "approve", "by": "ada", "overrode": False}
    ]


async def test_asking_for_changes_sends_the_work_back_once_with_the_note() -> None:
    designer, script = agent("designer", "One page.", "One page. Blank tasks are not added.")
    software = Factory("software", [station(designer, approval=True)])
    store = MemoryStore()
    run = await software.run("A to-do list page.", store=store)

    again = await software.resume(
        run.request_changes(by="ada", note="Blank tasks should not be added."), store=store
    )

    assert again.waiting is not None
    assert again.waiting.output == "One page. Blank tasks are not added."
    assert prompts(script.seen[-1]) == [
        "A to-do list page.",
        "A person asked for changes to your last output.\nada: Blank tasks should not be added.",
    ]
    done = await software.resume(again.approve(by="ada"), store=store)
    assert done.outcome == "passed"


async def test_rejecting_ends_the_run_as_failed() -> None:
    designer, _ = agent("designer", "One page.")
    software = Factory("software", [station(designer, approval=True)])
    store = MemoryStore()
    run = await software.run("A to-do list page.", store=store)

    done = await software.resume(run.reject(by="ada", note="We don't need this."), store=store)

    assert (done.outcome, done.finished, done.stopped_at) == ("failed", False, "designer")
    assert done.answers[-1].model_dump(include={"kind", "note"}) == {
        "kind": "reject",
        "note": "We don't need this.",
    }


async def test_an_answer_is_bound_to_the_exact_output_it_was_made_on() -> None:
    designer, _ = agent("designer", "One page.", "Two pages.")
    software = Factory("software", [station(designer, approval=True)])
    store = MemoryStore()
    first = await software.run("A to-do list page.", store=store)
    assert first.waiting is not None
    second = await software.resume(first.request_changes(by="ada", note="Shorter."), store=store)
    on_the_first_output = second.approve(by="ada").model_copy(
        update={"work": first.waiting.artifact}
    )

    with pytest.raises(ApprovalRefused, match="the answer was made on"):
        await software.resume(on_the_first_output, store=store)


async def test_asking_for_changes_and_rejecting_need_a_note() -> None:
    designer, _ = agent("designer", "One page.")
    software = Factory("software", [station(designer, approval=True)])
    run = await software.run("A to-do list page.", store=MemoryStore())

    with pytest.raises(ApprovalRefused, match="needs a note"):
        run.reject(by="ada", note=" ")


async def test_approving_an_escalation_overrides_the_gate_without_asking_twice() -> None:
    designer, _ = agent("designer", "One page, one file.")

    @gate(version="1")
    def one_file_is_a_judgement_call(output: Artifact[str], evidence: Evidence) -> Verdict:
        return escalate("whether one file is enough is for a person to say")

    design = station(designer, gate=one_file_is_a_judgement_call, approval=True)
    software = Factory("software", [design])
    store = MemoryStore()

    run = await software.run("A to-do list page.", store=store)
    assert run.waiting is not None
    assert (run.waiting.kind, run.waiting.reasons) == (
        "escalation",
        ("whether one file is enough is for a person to say",),
    )

    done = await software.resume(run.approve(by="ada", note="one file is right"), store=store)

    assert (done.outcome, done.output) == ("passed", "One page, one file.")
    assert [a.model_dump(include={"kind", "overrode"}) for a in done.answers] == [
        {"kind": "approve", "overrode": True}
    ]


async def test_a_station_can_raise_escalate_and_offer_its_work() -> None:
    @station
    async def build(ctx: StationContext, design: str) -> str:
        raise Escalate("the design asks for a server we do not have", output="Half built.")

    software = Factory("software", [build])
    store = MemoryStore()
    run = await software.run("One page.", store=store)

    assert run.waiting is not None
    assert (run.waiting.kind, run.waiting.output) == ("escalation", "Half built.")
    done = await software.resume(run.approve(by="ada"), store=store)
    assert done.output == "Half built."


async def test_an_escalation_with_no_work_cannot_be_approved_only_changed_or_rejected() -> None:
    @station
    async def build(ctx: StationContext, design: str) -> str:
        raise Escalate("nothing sensible to build")

    software = Factory("software", [build])
    store = MemoryStore()
    run = await software.run("One page.", store=store)

    with pytest.raises(ApprovalRefused, match="offered no work to accept"):
        await software.resume(run.approve(by="ada"), store=store)


async def test_rework_that_runs_out_can_escalate_to_a_person() -> None:
    designer, _ = agent("designer", "One page.")

    @gate(version="1")
    def mentions_blank_tasks(output: Artifact[str], evidence: Evidence) -> Verdict:
        if "blank" in output.payload:
            return passed("blank tasks are covered")
        return failed("the design never says what happens to a blank task")

    design = station(designer, gate=mentions_blank_tasks)
    software = Factory("software", [design], rework=[Rework(design, limit=1, on_limit="escalate")])
    store = MemoryStore()

    run = await software.run("A to-do list page.", store=store)

    assert run.waiting is not None and run.waiting.kind == "escalation"
    assert run.waiting.reasons[0] == "rework ran out"
    done = await software.resume(run.approve(by="ada"), store=store)
    assert (done.outcome, done.output) == ("passed", "One page.")


async def test_an_agent_question_makes_the_run_wait_until_the_person_answers() -> None:
    designer, script = agent(
        "designer",
        ask("Should ticked tasks survive a reload?"),
        "One page. Ticked tasks are saved.",
        capabilities=[AskPerson()],
    )
    software = Factory("software", [station(designer)])
    store = MemoryStore()

    run = await software.run("A to-do list page.", store=store)

    assert run.waiting is not None and run.waiting.kind == "tool-calls"
    assert run.waiting.agent == "designer"
    requests = run.waiting.requests
    assert isinstance(requests, DeferredToolRequests)
    asked = requests.metadata[requests.calls[0].tool_call_id]["questions"]
    assert [question["question"] for question in asked] == ["Should ticked tasks survive a reload?"]

    assert str(run.question) == "Should ticked tasks survive a reload?"
    done = await software.resume(run.reply("Yes.", by="ada"), store=store)

    assert (done.outcome, done.output) == ("passed", "One page. Ticked tasks are saved.")
    assert script.calls == 2


async def test_on_resume_finished_agent_calls_replay_without_calling_the_model() -> None:
    builder, built = agent("builder", "Built index.html.")
    reviewer, _ = agent(
        "reviewer",
        ask("Is a page with no tasks allowed to show a hint?"),
        "Nothing is wrong.",
        capabilities=[AskPerson()],
    )

    @station
    async def build(ctx: StationContext, design: str) -> str:
        made = await ctx.run_agent(builder, design)
        review = await ctx.run_agent(reviewer, made.output)
        return f"{made.output} {review.output}"

    software = Factory("software", [build])
    store = MemoryStore()
    run = await software.run("One page.", store=store)
    done = await software.resume(run.reply("Yes.", by="ada"), store=store)

    assert done.output == "Built index.html. Nothing is wrong."
    assert built.calls == 1
    assert [step.attempt for step in done.steps] == [1, 1]


async def test_a_tool_that_needs_approval_waits_and_runs_once_approved() -> None:
    published: list[str] = []
    builder, _ = agent(
        "builder",
        ModelResponse(parts=[ToolCallPart("publish_page", {"path": "index.html"}, "p1")]),
        "Published.",
    )

    @builder.tool_plain(requires_approval=True)
    def publish_page(path: str) -> str:
        published.append(path)
        return f"published {path}"

    software = Factory("software", [station(builder)])
    store = MemoryStore()
    run = await software.run("Publish the page.", store=store)
    done = await software.resume(run.allow(by="ada"), store=store)

    assert (done.outcome, published) == ("passed", ["index.html"])


async def test_the_wrong_kind_of_answer_is_refused_and_changes_nothing() -> None:
    designer, _ = agent("designer", "One page.")
    software = Factory("software", [station(designer, approval=True)])
    store = MemoryStore()
    run = await software.run("A to-do list page.", store=store)

    with pytest.raises(ApprovalRefused, match=r"waits for approval; use run\.approve"):
        run.reply("Yes.", by="ada")
    with pytest.raises(ApprovalRefused, match=r"waits for approval; use run\.approve"):
        await software.resume(queued_reply(run), store=store)
    done = await software.resume(run.approve(by="ada"), store=store)
    with pytest.raises(ApprovalRefused, match="is not waiting"):
        done.approve(by="ada")


async def test_a_run_is_resumed_only_on_a_factory_of_the_same_shape() -> None:
    designer, _ = agent("designer", "One page.")
    store = MemoryStore()
    run = await Factory("software", [station(designer, approval=True)]).run("x", store=store)
    reshaped = Factory("software", [station(designer, approval=True, version="2")])

    with pytest.raises(ApprovalRefused, match="another shape"):
        await reshaped.resume(run.approve(by="ada"), store=store)


async def test_a_run_saved_in_a_folder_is_answered_from_another_store_object(
    tmp_path: Path,
) -> None:
    designer, _ = agent("designer", "One page.")
    software = Factory("software", [station(designer, approval=True)])
    run = await software.run("A to-do list page.", store=LocalStore(tmp_path))

    later = LocalStore(tmp_path)
    loaded = await later.get_run(run.run_id)
    assert loaded.waiting is not None and loaded.waiting.output == "One page."
    done = await software.resume(loaded.approve(by="ada"), store=later)

    assert done.outcome == "passed"
    assert (await later.get_run(run.run_id)).outcome == "passed"


@pytest.mark.parametrize("make_store", [MemoryStore, LocalStore], ids=["memory", "local"])
async def test_two_answers_to_the_same_run_cannot_both_win(make_store: Any, tmp_path: Path) -> None:
    store = make_store(tmp_path) if make_store is LocalStore else make_store()
    designer, _ = agent("designer", "One page.")
    software = Factory("software", [station(designer, approval=True)])
    run = await software.run("A to-do list page.", store=store)

    await software.resume(run.approve(by="ada"), store=store)

    with pytest.raises(RunConflict, match="has moved on"):
        await software.resume(run.reject(by="bob", note="No."), store=store)
    assert (await store.get_run(run.run_id)).answers[0].by == "ada"


async def test_an_answer_claims_the_run_before_any_resumed_work_starts() -> None:
    store = MemoryStore()
    designer, _ = agent("designer", "One page.")
    started, release = asyncio.Event(), asyncio.Event()
    published: list[str] = []

    @station
    async def publish(ctx: StationContext, design: str) -> str:
        published.append(design)
        started.set()
        await release.wait()
        return "published"

    software = Factory("software", [station(designer, approval=True), publish])
    run = await software.run("A to-do list page.", store=store)

    approving = asyncio.create_task(software.resume(run.approve(by="ada"), store=store))
    await started.wait()
    with pytest.raises(RunConflict, match="has moved on"):
        await software.resume(run.reject(by="bob", note="No."), store=store)
    release.set()
    done = await approving

    assert done.outcome == "passed"
    assert published == ["One page."]
    saved = await store.get_run(run.run_id)
    assert [(a.by, a.kind) for a in saved.answers] == [("ada", "approve")]


async def test_a_tool_call_answer_claims_the_run_before_the_station_runs_again() -> None:
    store = MemoryStore()
    asker, script = agent("asker", ask("Which colour?"), "Blue.", capabilities=[AskPerson()])
    software = Factory("software", [station(asker)])
    run = await software.run("Pick a colour.", store=store)
    answer = run.reply("Blue", by="ada")

    claims: list[tuple[str, int]] = []

    async def watch(saved: Any) -> None:
        claims.append((saved.outcome, script.calls))

    done = await software.resume(answer, store=store, on_step=watch)

    assert done.outcome == "passed"
    assert claims[0] == ("running", 1)  # saved before the model was asked again
    with pytest.raises(RunConflict, match="has moved on"):
        await software.resume(answer, store=store)
    assert script.calls == 2


def _checked(output: str) -> str:
    return output


async def test_an_agent_with_an_output_validator_runs_inside_a_station() -> None:
    checked = Agent(
        FunctionModel(lambda m, i: ModelResponse(parts=[TextPart("One page.")])), name="checked"
    )
    checked.output_validator(_checked)

    result = await station(checked).run("A to-do list page.", store=MemoryStore())

    assert result.outcome == "not_judged", result.reason


async def test_a_run_id_cannot_be_used_twice() -> None:
    designer, _ = agent("designer", "One page.")
    software = Factory("software", [station(designer)])
    store = MemoryStore()
    await software.run("x", store=store, run_id="r1")

    with pytest.raises(RunConflict, match="used twice"):
        await software.run("x", store=store, run_id="r1")


async def test_a_resumed_station_that_runs_its_agents_in_another_order_did_not_run() -> None:
    asker, _ = agent("asker", ask("Which colour?"), "Blue.", capabilities=[AskPerson()])
    helper, _ = agent("helper", "Red.")
    order = [helper, asker]

    @station(agents=[asker, helper])
    async def fickle(ctx: StationContext, text: str) -> str:
        await ctx.run_agent(order[0], text)
        return (await ctx.run_agent(order[1], text)).output

    software = Factory("software", [fickle])
    store = MemoryStore()
    run = await software.run("x", store=store)
    order.reverse()

    done = await software.resume(run.reply("Blue.", by="ada"), store=store)

    assert done.outcome == "did_not_run"
    assert done.steps[-1].error_type == "StationDefinitionError"
    assert "keep the code between agent runs deterministic" in (done.steps[-1].reason or "")


def tools_seen(
    *responses: ModelResponse, max_rounds: int
) -> tuple[Agent[None, str], list[list[str]]]:
    """An agent with ``AskPerson`` whose model gives ``responses`` while it may ask."""
    seen: list[list[str]] = []
    queue = list(responses)

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append([tool.name for tool in info.function_tools])
        if ASK in seen[-1] and queue:
            return queue.pop(0)
        return ModelResponse(parts=[TextPart("Done.")])

    asker = Agent(
        FunctionModel(model), name="designer", capabilities=[AskPerson(max_rounds=max_rounds)]
    )
    return asker, seen


def answer_all(requests: DeferredToolRequests) -> DeferredToolResults:
    return DeferredToolResults(
        calls={
            call.tool_call_id: {q["header"]: ["No"] for q in call.args_as_dict()["questions"]}
            for call in requests.calls
        }
    )


def test_ask_person_takes_its_tool_away_once_its_rounds_are_answered() -> None:
    designer, seen = tools_seen(ask("One?"), ask("Two?", call_id="q2"), max_rounds=1)
    result = designer.run_sync("x", output_type=[str, DeferredToolRequests])
    assert isinstance(result.output, DeferredToolRequests)

    again = designer.run_sync(
        None,
        conversation=result.conversation,
        deferred_tool_results=answer_all(result.output),
        output_type=[str, DeferredToolRequests],
    )

    assert again.output == "Done."
    assert seen == [[ASK], []]


def test_once_its_rounds_are_answered_ask_person_tells_the_agent_to_carry_on() -> None:
    said: list[str] = []
    queue = [ask("One?")]

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        said.append(info.instructions or "")
        return queue.pop(0) if queue else ModelResponse(parts=[TextPart("Done.")])

    designer = Agent(FunctionModel(model), name="designer", capabilities=[AskPerson(max_rounds=1)])
    result = designer.run_sync("x", output_type=[str, DeferredToolRequests])
    assert isinstance(result.output, DeferredToolRequests)

    designer.run_sync(
        None,
        conversation=result.conversation,
        deferred_tool_results=answer_all(result.output),
        output_type=[str, DeferredToolRequests],
    )

    assert ASK in said[0]
    assert ASK not in said[1]
    assert "Do not ask again" in said[1]


def test_several_ask_calls_in_one_response_are_one_round() -> None:
    together = ModelResponse(parts=[ask_call("One?"), ask_call("Two?", call_id="q2")])
    designer, seen = tools_seen(together, max_rounds=2)
    result = designer.run_sync("x", output_type=[str, DeferredToolRequests])
    assert isinstance(result.output, DeferredToolRequests)
    assert len(result.output.calls) == 2

    designer.run_sync(
        None,
        conversation=result.conversation,
        deferred_tool_results=answer_all(result.output),
        output_type=[str, DeferredToolRequests],
    )

    assert seen == [[ASK], [ASK]]


def test_ask_person_with_no_rounds_gives_the_agent_no_way_to_ask() -> None:
    designer, seen = tools_seen(ask("One?"), max_rounds=0)

    assert designer.run_sync("x").output == "Done."
    assert seen == [[]]


def test_ask_person_says_how_to_ask_in_its_instructions() -> None:
    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        said.append(info.instructions or "")
        return ModelResponse(parts=[TextPart("Done.")])

    said: list[str] = []
    Agent(FunctionModel(model), name="designer", capabilities=[AskPerson(max_rounds=2)]).run_sync(
        "x"
    )

    assert "2 rounds of questions" in said[0]
    assert '" (Recommended)"' in said[0] and "why it matters" in said[0]


@pytest.mark.parametrize("rounds", [-1, 1.5, True, "3"])
def test_a_badly_set_up_ask_person_is_refused(rounds: object) -> None:
    with pytest.raises(DefinitionError, match="max_rounds must be a whole number, 0 or more"):
        AskPerson(max_rounds=rounds)  # pyright: ignore[reportArgumentType]
