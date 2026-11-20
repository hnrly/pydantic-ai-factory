"""A person's answer is one type: it names its run, is checked and applied by one rule set,
is recorded on the run, and travels as JSON."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import TypeAdapter
from pydantic_ai import Agent, DeferredToolResults, ToolApproved, ToolDenied
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from pydantic_ai_factory import (
    Answer,
    ApprovalRefused,
    Approve,
    AskPerson,
    Changes,
    Escalate,
    Factory,
    MemoryStore,
    Reject,
    Reply,
    Run,
    RunConflict,
    StationContext,
    station,
)

pytestmark = pytest.mark.anyio


def agent(name: str, *replies: ModelResponse | str, **options: Any) -> Agent[None, str]:
    """An agent whose model gives ``replies`` in turn, then repeats the last one."""
    queue = list(replies)

    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        given = queue.pop(0) if len(queue) > 1 else queue[0]
        return given if isinstance(given, ModelResponse) else ModelResponse(parts=[TextPart(given)])

    return Agent(FunctionModel(reply), name=name, **options)


def ask(question: str, call_id: str = "q1") -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart("ask_person", {"question": question}, call_id)])


def approval_factory() -> Factory:
    return Factory(
        "software", [station(agent("designer", "One page.", "Two pages."), approval=True)]
    )


def asking_factory() -> Factory:
    asker = agent("asker", ask("Which colour?"), "Blue.", capabilities=[AskPerson()])
    return Factory("software", [station(asker)])


def escalating_factory() -> Factory:
    @station
    async def build(ctx: StationContext, design: str) -> str:
        raise Escalate("nothing sensible to build")

    return Factory("software", [build])


def blue(run: Run) -> DeferredToolResults:
    """Answer the run's one question."""
    assert run.waiting is not None and run.waiting.requests is not None
    return run.waiting.requests.build_results(
        calls={run.waiting.requests.calls[0].tool_call_id: "Blue."}
    )


async def test_an_answer_names_its_run_and_resume_needs_nothing_else() -> None:
    software = approval_factory()
    store = MemoryStore()
    run = await software.run("A to-do list page.", store=store)
    assert run.waiting is not None

    answer = run.approve(by="ada")

    assert (answer.run_id, answer.revision) == (run.run_id, run.revision)
    assert (answer.station, answer.waiting, answer.work) == (
        "designer",
        "approval",
        run.waiting.artifact,
    )
    done = await software.resume(answer, store=store)
    assert done.outcome == "passed"


async def test_a_tool_reply_is_recorded_in_the_runs_answers_with_who_gave_it() -> None:
    software = asking_factory()
    store = MemoryStore()
    run = await software.run("Pick a colour.", store=store)

    done = await software.resume(run.reply(by="ada", results=blue(run)), store=store)

    [reply] = done.answers
    assert isinstance(reply, Reply)
    assert (reply.by, reply.station, reply.waiting) == ("ada", "asker", "tool-calls")
    assert reply.results.answered_by == {"q1": "ada"}
    assert (await store.get_run(run.run_id)).answers == done.answers


async def test_a_reply_keeps_who_answered_a_call_when_it_already_says() -> None:
    run = await asking_factory().run("Pick a colour.", store=MemoryStore())
    results = blue(run)
    results.answered_by["q1"] = "colour-policy"

    reply = run.reply(by="ada", results=results)

    assert reply.results.answered_by == {"q1": "colour-policy"}


async def test_approving_an_escalation_is_recorded_as_overriding_it() -> None:
    @station
    async def build(ctx: StationContext, design: str) -> str:
        raise Escalate("the design asks for a server", output="Half built.")

    software = Factory("software", [build])
    store = MemoryStore()
    run = await software.run("One page.", store=store)

    done = await software.resume(run.approve(by="ada"), store=store)

    assert [(a.kind, a.overrode) for a in done.answers if isinstance(a, Approve)] == [
        ("approve", True)
    ]


@dataclass
class Case:
    """A waiting run in a store, and an answer that breaks one rule of ``check``."""

    factory: Factory
    run: Run
    store: MemoryStore
    answer: Answer


Make = Callable[[], Awaitable[Case]]


async def _approval_case(
    change: Callable[[Run], Answer], *, runs_on: Factory | None = None
) -> Case:
    software = approval_factory()
    store = MemoryStore()
    run = await software.run("A to-do list page.", store=store)
    return Case(runs_on or software, run, store, change(run))


async def stale() -> Case:
    return await _approval_case(
        lambda run: run.approve(by="ada").model_copy(update={"revision": run.revision - 1})
    )


async def not_waiting() -> Case:
    software = approval_factory()
    store = MemoryStore()
    waiting = await software.run("A to-do list page.", store=store)
    done = await software.resume(waiting.approve(by="ada"), store=store)
    # Made as if at the current revision, so only "not waiting" is wrong with it.
    answer = waiting.approve(by="bob").model_copy(update={"revision": done.revision})
    return Case(software, done, store, answer)


async def another_shape() -> Case:
    reshaped = Factory(
        "software", [station(agent("designer", "One page."), approval=True, version="2")]
    )
    return await _approval_case(lambda run: run.approve(by="ada"), runs_on=reshaped)


async def another_station() -> Case:
    return await _approval_case(
        lambda run: run.approve(by="ada").model_copy(update={"station": "builder"})
    )


async def another_wait() -> Case:
    return await _approval_case(
        lambda run: run.approve(by="ada").model_copy(update={"waiting": "escalation"})
    )


async def other_work() -> Case:
    return await _approval_case(lambda run: run.approve(by="ada").model_copy(update={"work": None}))


async def claims_to_override() -> Case:
    return await _approval_case(
        lambda run: run.approve(by="ada").model_copy(update={"overrode": True})
    )


async def a_reply_to_an_approval() -> Case:
    return await _approval_case(lambda run: run.reply(by="ada", results=DeferredToolResults()))


async def an_approval_of_tool_calls() -> Case:
    software = asking_factory()
    store = MemoryStore()
    run = await software.run("Pick a colour.", store=store)
    return Case(software, run, store, run.approve(by="ada"))


async def approving_no_work() -> Case:
    software = escalating_factory()
    store = MemoryStore()
    run = await software.run("One page.", store=store)
    return Case(software, run, store, run.approve(by="ada"))


async def _reply_case(results: Callable[[Run], DeferredToolResults]) -> Case:
    software = asking_factory()
    store = MemoryStore()
    run = await software.run("Pick a colour.", store=store)
    return Case(software, run, store, run.reply(by="ada", results=results(run)))


async def a_reply_missing_a_call() -> Case:
    return await _reply_case(lambda run: DeferredToolResults())


async def a_reply_to_a_call_nobody_made() -> Case:
    return await _reply_case(lambda run: DeferredToolResults(calls={"q1": "Blue.", "q9": "Red."}))


async def a_reply_answering_a_call_twice() -> Case:
    return await _reply_case(
        lambda run: DeferredToolResults(calls={"q1": "Blue."}, approvals={"q1": True})
    )


RULES: dict[str, tuple[Make, bool, str]] = {
    "stale": (stale, True, "has moved on since revision"),
    "not waiting": (not_waiting, False, "is not waiting"),
    "another shape": (another_shape, False, "another shape"),
    "another station": (another_station, False, "the answer was made on"),
    "another wait": (another_wait, False, "the answer was made on"),
    "other work": (other_work, False, "the answer was made on"),
    "claims to override": (claims_to_override, False, "only the factory sets overrode"),
    "a reply to an approval": (a_reply_to_an_approval, False, "use run.approve"),
    "an approval of tool calls": (an_approval_of_tool_calls, False, "answer them with run.reply"),
    "approving no work": (approving_no_work, False, "offered no work to accept"),
    "a reply missing a call": (a_reply_missing_a_call, False, "no result for q1"),
    "a reply to a call nobody made": (
        a_reply_to_a_call_nobody_made,
        False,
        "a result for q9, which is not a pending call",
    ),
    "a reply answering a call twice": (
        a_reply_answering_a_call_twice,
        False,
        "more than one result for q1",
    ),
}


@pytest.mark.parametrize("rule", list(RULES))
async def test_check_and_resume_refuse_the_same_answers_for_the_same_reasons(rule: str) -> None:
    make, stale, says = RULES[rule]
    case = await make()

    problem = case.factory.check(case.run, case.answer)

    assert problem is not None
    assert problem.stale is stale
    assert says in problem.reason
    with pytest.raises(RunConflict if stale else ApprovalRefused) as refused:
        await case.factory.resume(case.answer, store=case.store)
    assert str(refused.value) == problem.reason
    assert await case.store.get_run(case.run.run_id) == case.run  # nothing changed


async def test_check_refuses_an_answer_for_another_run() -> None:
    software = approval_factory()
    store = MemoryStore()
    first = await software.run("A to-do list page.", store=store)
    second = await software.run("A shopping list page.", store=store)

    problem = software.check(first, second.approve(by="ada"))

    assert problem is not None and not problem.stale
    assert f"the answer is for run {second.run_id!r}" in problem.reason


async def test_check_finds_nothing_wrong_with_an_answer_that_fits() -> None:
    software = approval_factory()
    run = await software.run("A to-do list page.", store=MemoryStore())

    assert software.check(run, run.approve(by="ada")) is None
    assert software.check(run, run.reject(by="ada", note="Not now.")) is None


async def test_an_answer_made_with_an_earlier_revision_is_stale_after_the_run_waits_again() -> None:
    software = approval_factory()
    store = MemoryStore()
    first = await software.run("A to-do list page.", store=store)
    earlier = first.approve(by="bob")
    again = await software.resume(first.request_changes(by="ada", note="Shorter."), store=store)
    assert again.outcome == "waiting"

    problem = software.check(again, earlier)

    assert problem is not None and problem.stale
    with pytest.raises(RunConflict, match="has moved on"):
        await software.resume(earlier, store=store)
    assert await store.get_run(again.run_id) == again


async def test_an_answer_round_trips_through_json() -> None:
    run = await asking_factory().run("Pick a colour.", store=MemoryStore())
    results = DeferredToolResults(
        approvals={
            "a1": ToolApproved(override_args={"path": "about.html"}),
            "a2": ToolDenied("Not on a Friday."),
            "a3": True,
        },
        calls={"c1": "Blue.", "c2": {"colour": "blue", "shades": [1, 2]}},
        metadata={"a1": {"asked": "publish"}},
        answered_by={"c1": "colour-policy"},
    )
    adapter = TypeAdapter[Answer](Answer)
    answers: list[Answer] = [
        run.reply(by="ada", results=results),
        run.approve(by="ada", note="Fine."),
        run.request_changes(by="ada", note="Shorter."),
        run.reject(by="ada", note="No."),
    ]

    for answer in answers:
        assert adapter.validate_json(adapter.dump_json(answer)) == answer


def test_changes_and_rejections_need_a_note_that_is_not_blank() -> None:
    fields: dict[str, Any] = {
        "run_id": "run-1",
        "revision": 1,
        "station": "designer",
        "waiting": "approval",
        "work": None,
        "by": "ada",
        "at": "2026-11-20T09:00:00Z",
    }
    for kind in (Changes, Reject):
        with pytest.raises(ValueError, match="needs a note"):
            kind.model_validate({**fields, "note": "  "})


async def test_an_answer_built_from_a_run_that_has_since_passed_is_refused_as_stale() -> None:
    software = approval_factory()
    store = MemoryStore()
    waiting = await software.run("A to-do list page.", store=store)
    passed = await software.resume(waiting.approve(by="ada"), store=store)

    with pytest.raises(RunConflict, match="has moved on since revision"):
        passed.reject(by="bob", note="No.", revision=waiting.revision)


async def test_check_reports_an_answer_that_is_both_stale_and_not_waiting_as_stale() -> None:
    software = approval_factory()
    store = MemoryStore()
    waiting = await software.run("A to-do list page.", store=store)
    late = waiting.reject(by="bob", note="No.")
    passed = await software.resume(waiting.approve(by="ada"), store=store)

    problem = software.check(passed, late)

    assert problem is not None and problem.stale
    with pytest.raises(RunConflict):
        await software.resume(late, store=store)


async def test_an_answer_naming_a_revision_the_run_has_not_reached_is_refused_but_not_stale() -> (
    None
):
    software = approval_factory()
    run = await software.run("A to-do list page.", store=MemoryStore())

    with pytest.raises(ApprovalRefused, match="is only at revision"):
        run.approve(by="ada", revision=run.revision + 5)
    ahead = run.approve(by="ada").model_copy(update={"revision": run.revision + 5})
    problem = software.check(run, ahead)

    assert problem is not None and not problem.stale
    assert "is only at revision" in problem.reason
