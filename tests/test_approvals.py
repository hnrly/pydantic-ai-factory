"""A run waits for a person to approve, settle an escalation or answer an agent, and carries on."""

from __future__ import annotations

import asyncio
import itertools
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

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
    Question,
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
from pydantic_ai_factory.stations._ask import ENOUGH

pytestmark = pytest.mark.anyio

CALL_IDS = itertools.count(1)


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


def question(text: str, **more: Any) -> dict[str, Any]:
    return {"text": text, "why_it_matters": "The design depends on it.", **more}


def asks(*texts: str, call_id: str = "q1") -> ToolCallPart:
    return ToolCallPart("ask_person", {"questions": [question(t) for t in texts]}, call_id)


def ask(*texts: str) -> ModelResponse:
    """One round: a response with one ``ask_person`` call carrying every question.

    Each call gets its own id, as a real model gives it.
    """
    return ModelResponse(parts=[asks(*texts, call_id=f"ask-{next(CALL_IDS)}")])


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

    done = await software.resume(run, run.approve(by="ada"), store=store)

    assert (done.outcome, done.output) == ("passed", "Built.")
    assert [(a.decision, a.by, a.overrode) for a in done.approvals] == [("approved", "ada", False)]


async def test_asking_for_changes_sends_the_work_back_once_with_the_note() -> None:
    designer, script = agent("designer", "One page.", "One page. Blank tasks are not added.")
    software = Factory("software", [station(designer, approval=True)])
    store = MemoryStore()
    run = await software.run("A to-do list page.", store=store)

    again = await software.resume(
        run, run.request_changes(by="ada", note="Blank tasks should not be added."), store=store
    )

    assert again.waiting is not None
    assert again.waiting.output == "One page. Blank tasks are not added."
    assert prompts(script.seen[-1]) == [
        "A to-do list page.",
        "A person asked for changes to your last output.\nada: Blank tasks should not be added.",
    ]
    done = await software.resume(again, again.approve(by="ada"), store=store)
    assert done.outcome == "passed"


async def test_rejecting_ends_the_run_as_failed() -> None:
    designer, _ = agent("designer", "One page.")
    software = Factory("software", [station(designer, approval=True)])
    store = MemoryStore()
    run = await software.run("A to-do list page.", store=store)

    done = await software.resume(run, run.reject(by="ada", note="We don't need this."), store=store)

    assert (done.outcome, done.finished, done.stopped_at) == ("failed", False, "designer")
    assert done.approvals[-1].note == "We don't need this."


async def test_a_decision_is_bound_to_the_exact_output_it_was_made_on() -> None:
    designer, _ = agent("designer", "One page.", "Two pages.")
    software = Factory("software", [station(designer, approval=True)])
    store = MemoryStore()
    first = await software.run("A to-do list page.", store=store)
    stale = first.approve(by="ada")
    second = await software.resume(
        first, first.request_changes(by="ada", note="Shorter."), store=store
    )

    with pytest.raises(ApprovalRefused, match="the decision was made on"):
        await software.resume(second, stale, store=store)


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

    done = await software.resume(run, run.approve(by="ada", note="one file is right"), store=store)

    assert (done.outcome, done.output) == ("passed", "One page, one file.")
    assert [a.overrode for a in done.approvals] == [True]


async def test_a_station_can_raise_escalate_and_offer_its_work() -> None:
    @station
    async def build(ctx: StationContext, design: str) -> str:
        raise Escalate("the design asks for a server we do not have", output="Half built.")

    software = Factory("software", [build])
    store = MemoryStore()
    run = await software.run("One page.", store=store)

    assert run.waiting is not None
    assert (run.waiting.kind, run.waiting.output) == ("escalation", "Half built.")
    done = await software.resume(run, run.approve(by="ada"), store=store)
    assert done.output == "Half built."


async def test_an_escalation_with_no_work_cannot_be_approved_only_changed_or_rejected() -> None:
    @station
    async def build(ctx: StationContext, design: str) -> str:
        raise Escalate("nothing sensible to build")

    software = Factory("software", [build])
    store = MemoryStore()
    run = await software.run("One page.", store=store)

    with pytest.raises(ApprovalRefused, match="offered no work to accept"):
        await software.resume(run, run.approve(by="ada"), store=store)


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
    done = await software.resume(run, run.approve(by="ada"), store=store)
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
    assert requests.metadata[requests.calls[0].tool_call_id] == {
        "questions": [
            {
                "text": "Should ticked tasks survive a reload?",
                "why_it_matters": "The design depends on it.",
                "options": None,
                "recommended": None,
            }
        ],
        "question": "1. Should ticked tasks survive a reload?\n"
        "   Why it matters: The design depends on it.",
    }

    answer = requests.build_results(calls={requests.calls[0].tool_call_id: "Yes."})
    done = await software.resume(run, answer, store=store)

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
    assert run.waiting is not None and run.waiting.requests is not None
    call = run.waiting.requests.calls[0]

    done = await software.resume(
        run, run.waiting.requests.build_results(calls={call.tool_call_id: "Yes."}), store=store
    )

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
    assert run.waiting is not None and run.waiting.requests is not None
    approval = run.waiting.requests.approvals[0]

    answer = run.waiting.requests.build_results(approvals={approval.tool_call_id: True})
    done = await software.resume(run, answer, store=store)

    assert (done.outcome, published) == ("passed", ["index.html"])


async def test_the_wrong_kind_of_answer_is_refused_and_changes_nothing() -> None:
    designer, _ = agent("designer", "One page.")
    software = Factory("software", [station(designer, approval=True)])
    store = MemoryStore()
    run = await software.run("A to-do list page.", store=store)

    with pytest.raises(ApprovalRefused, match="waits for a decision"):
        await software.resume(run, DeferredToolResults(), store=store)
    done = await software.resume(run, run.approve(by="ada"), store=store)
    with pytest.raises(ApprovalRefused, match="is not waiting"):
        await software.resume(done, run.approve(by="ada"), store=store)
    with pytest.raises(ApprovalRefused, match="is not waiting for a decision"):
        done.approve(by="ada")


async def test_a_run_is_resumed_only_on_a_factory_of_the_same_shape() -> None:
    designer, _ = agent("designer", "One page.")
    store = MemoryStore()
    run = await Factory("software", [station(designer, approval=True)]).run("x", store=store)
    reshaped = Factory("software", [station(designer, approval=True, version="2")])

    with pytest.raises(ApprovalRefused, match="another shape"):
        await reshaped.resume(run, run.approve(by="ada"), store=store)


async def test_a_run_saved_in_a_folder_is_answered_from_another_store_object(
    tmp_path: Path,
) -> None:
    designer, _ = agent("designer", "One page.")
    software = Factory("software", [station(designer, approval=True)])
    run = await software.run("A to-do list page.", store=LocalStore(tmp_path))

    later = LocalStore(tmp_path)
    loaded = await later.get_run(run.run_id)
    assert loaded.waiting is not None and loaded.waiting.output == "One page."
    done = await software.resume(loaded, loaded.approve(by="ada"), store=later)

    assert done.outcome == "passed"
    assert (await later.get_run(run.run_id)).outcome == "passed"


@pytest.mark.parametrize("make_store", [MemoryStore, LocalStore], ids=["memory", "local"])
async def test_two_answers_to_the_same_run_cannot_both_win(make_store: Any, tmp_path: Path) -> None:
    store = make_store(tmp_path) if make_store is LocalStore else make_store()
    designer, _ = agent("designer", "One page.")
    software = Factory("software", [station(designer, approval=True)])
    run = await software.run("A to-do list page.", store=store)

    await software.resume(run, run.approve(by="ada"), store=store)

    with pytest.raises(RunConflict, match="has moved on"):
        await software.resume(run, run.reject(by="bob", note="No."), store=store)
    assert (await store.get_run(run.run_id)).approvals[0].by == "ada"


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

    approving = asyncio.create_task(software.resume(run, run.approve(by="ada"), store=store))
    await started.wait()
    with pytest.raises(RunConflict, match="has moved on"):
        await software.resume(run, run.reject(by="bob", note="No."), store=store)
    release.set()
    done = await approving

    assert done.outcome == "passed"
    assert published == ["One page."]
    saved = await store.get_run(run.run_id)
    assert [(d.by, d.decision) for d in saved.approvals] == [("ada", "approved")]


async def test_a_tool_call_answer_claims_the_run_before_the_station_runs_again() -> None:
    store = MemoryStore()
    asker, script = agent("asker", ask("Which colour?"), "Blue.", capabilities=[AskPerson()])
    software = Factory("software", [station(asker)])
    run = await software.run("Pick a colour.", store=store)
    waiting = run.waiting
    assert waiting is not None and waiting.requests is not None
    call = waiting.requests.calls[0].tool_call_id
    answer = DeferredToolResults(calls={call: "Blue"})

    claims: list[tuple[str, int]] = []

    async def watch(saved: Any) -> None:
        claims.append((saved.outcome, script.calls))

    done = await software.resume(run, answer, store=store, on_step=watch)

    assert done.outcome == "passed"
    assert claims[0] == ("running", 1)  # saved before the model was asked again
    with pytest.raises(RunConflict, match="has moved on"):
        await software.resume(run, answer, store=store)
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
    assert run.waiting is not None and run.waiting.requests is not None
    call = run.waiting.requests.calls[0]
    order.reverse()

    done = await software.resume(
        run, run.waiting.requests.build_results(calls={call.tool_call_id: "Blue."}), store=store
    )

    assert done.outcome == "did_not_run"
    assert done.steps[-1].error_type == "StationDefinitionError"
    assert "keep the code between agent runs deterministic" in (done.steps[-1].reason or "")


def ask_until_done(agent: Agent[None, str]) -> tuple[Any, list[DeferredToolRequests]]:
    """Run ``agent``, answering every round it asks, and keep each round it waited on."""
    waited: list[DeferredToolRequests] = []
    result = agent.run_sync("x", output_type=[str, DeferredToolRequests])
    while isinstance(result.output, DeferredToolRequests):
        waited.append(result.output)
        answer = result.output.build_results(
            calls={call.tool_call_id: "Yes." for call in result.output.calls}
        )
        result = agent.run_sync(
            None,
            conversation=result.conversation,
            deferred_tool_results=answer,
            output_type=[str, DeferredToolRequests],
        )
    return result, waited


def tool_results(script: Script) -> list[str]:
    """What each ``ask_person`` call returned, as the model last saw it."""
    return [
        str(part.content)
        for message in script.seen[-1]
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "ask_person"
    ]


def test_a_round_of_four_questions_waits_once() -> None:
    round_of_four = ModelResponse(
        parts=[
            ToolCallPart(
                "ask_person",
                {
                    "questions": [
                        question(
                            "Should ticked tasks survive a reload?",
                            options=["Yes, in the browser", "No"],
                            recommended=0,
                        ),
                        question("Can a ticked task be unticked?"),
                        question("What does a blank task do?"),
                        question("Do tasks keep their order?"),
                    ]
                },
                "q1",
            )
        ]
    )
    designer, script = agent("designer", round_of_four, "Done.", capabilities=[AskPerson()])

    result, waited = ask_until_done(designer)

    assert (result.output, script.calls, len(waited)) == ("Done.", 2, 1)
    [call] = waited[0].calls
    metadata = waited[0].metadata[call.tool_call_id]
    assert [q["text"] for q in metadata["questions"]][-1] == "Do tasks keep their order?"
    assert metadata["question"].splitlines()[:4] == [
        "1. Should ticked tasks survive a reload?",
        "   Why it matters: The design depends on it.",
        "   a) Yes, in the browser (recommended)",
        "   b) No",
    ]
    assert "4. Do tasks keep their order?" in metadata["question"]


def test_several_calls_in_one_response_are_one_round() -> None:
    together = ModelResponse(
        parts=[asks("One?", call_id="q1"), asks("Two?", call_id="q2"), asks("Three?", call_id="q3")]
    )
    designer, script = agent(
        "designer", together, ask("Four?"), "Done.", capabilities=[AskPerson(max_rounds=2)]
    )

    result, waited = ask_until_done(designer)

    assert result.output == "Done."
    assert [len(round.calls) for round in waited] == [3, 1]
    assert ENOUGH not in tool_results(script)


def test_a_fourth_round_is_told_to_carry_on() -> None:
    designer, script = agent(
        "designer",
        ask("One?"),
        ask("Two?"),
        ask("Three?"),
        ask("Four?"),
        "Done.",
        capabilities=[AskPerson()],
    )

    result, waited = ask_until_done(designer)

    assert (result.output, len(waited), script.calls) == ("Done.", 3, 5)
    assert tool_results(script)[-1] == ENOUGH


def test_max_rounds_of_one_stops_after_the_first_round() -> None:
    designer, script = agent(
        "designer", ask("One?"), ask("Two?"), "Done.", capabilities=[AskPerson(max_rounds=1)]
    )

    result, waited = ask_until_done(designer)

    assert (result.output, len(waited), script.calls) == ("Done.", 1, 3)
    assert tool_results(script)[-1] == ENOUGH


def test_max_rounds_of_zero_takes_the_tool_away() -> None:
    offered: list[str] = []

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        offered.extend(tool.name for tool in info.function_tools)
        return ModelResponse(parts=[TextPart("Done.")])

    designer = Agent(FunctionModel(model), name="designer", capabilities=[AskPerson(max_rounds=0)])

    assert designer.run_sync("x").output == "Done."
    assert offered == []


def test_five_questions_in_a_round_are_refused() -> None:
    designer, script = agent(
        "designer",
        ask("One?", "Two?", "Three?", "Four?", "Five?"),
        ask("One?"),
        ask("Two?"),
        ask("Three?"),
        ask("Four?"),
        "Done.",
        capabilities=[AskPerson()],
    )

    result, waited = ask_until_done(designer)

    assert result.output == "Done."
    assert [len(round.calls[0].args_as_dict()["questions"]) for round in waited] == [1, 1, 1]
    refusal = tool_results(script)[0]
    assert "1 to 4 questions, and this one asked 5" in refusal
    assert tool_results(script)[-1] == ENOUGH  # the refused round was not counted


def test_questions_over_the_limit_across_calls_in_one_response_are_refused() -> None:
    too_many = ModelResponse(
        parts=[asks("One?", "Two?", call_id="q1"), asks("Three?", "Four?", call_id="q2")]
    )
    designer, script = agent(
        "designer", too_many, ask("One?"), "Done.", capabilities=[AskPerson(max_per_round=3)]
    )

    result, waited = ask_until_done(designer)

    assert (result.output, len(waited)) == ("Done.", 1)
    assert "1 to 3 questions, and this one asked 4" in tool_results(script)[0]


@pytest.mark.parametrize(
    ("given", "problem"),
    [
        ({"options": ["A", "B", "C", "D"]}, "at most 3 items"),
        ({"options": ["A", "B"], "recommended": 1}, "put the recommended option first"),
        ({"recommended": 0}, "recommended needs options"),
        ({"why_it_matters": ""}, "at least 1 character"),
    ],
)
def test_a_question_that_breaks_the_rules_is_refused(given: dict[str, Any], problem: str) -> None:
    with pytest.raises(ValidationError, match=problem):
        Question.model_validate(question("Keep ticks?", **given))


def test_an_open_question_and_one_with_a_recommended_first_option_are_accepted() -> None:
    assert Question.model_validate(question("Keep ticks?")).options is None
    chosen = Question.model_validate(question("Keep ticks?", options=["Yes", "No"], recommended=0))
    assert chosen.recommended == 0


@pytest.mark.parametrize(
    ("settings", "problem"),
    [
        ({"max_rounds": -1}, "max_rounds must be a whole number, 0 or more"),
        ({"max_per_round": 0}, "max_per_round must be a whole number, 1 or more"),
        ({"max_rounds": True}, "max_rounds must be a whole number"),
    ],
)
def test_a_badly_set_up_ask_person_is_refused(settings: dict[str, Any], problem: str) -> None:
    with pytest.raises(DefinitionError, match=problem):
        AskPerson(**settings)
