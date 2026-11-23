"""A person's answer is one type: it names its run, is checked and applied by one rule set,
is recorded on the run, and travels as JSON."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import pytest
from asking import ask, ask_call, asked
from pydantic import TypeAdapter
from pydantic_ai import Agent, DeferredToolResults, ToolApproved, ToolDenied
from pydantic_ai.exceptions import CallDeferred
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from queued import queued_reply

from pydantic_ai_factory import (
    Action,
    Answer,
    ApprovalRefused,
    Approve,
    AskPerson,
    Changes,
    Escalate,
    Factory,
    MemoryStore,
    Question,
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

    done = await software.resume(run.reply("Blue.", by="ada"), store=store)

    [reply] = done.answers
    assert isinstance(reply, Reply)
    assert (reply.by, reply.station, reply.waiting) == ("ada", "asker", "tool-calls")
    assert reply.results.answered_by == {"q1": "ada"}
    assert (await store.get_run(run.run_id)).answers == done.answers


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
    return await _approval_case(queued_reply)


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
    return Case(software, run, store, queued_reply(run, results(run)))


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
    "a reply missing a call": (a_reply_missing_a_call, False, "no answer for 'Which colour?'"),
    "a reply to a call nobody made": (
        a_reply_to_a_call_nobody_made,
        False,
        "an answer for 'q9', which it is not waiting on",
    ),
    "a reply answering a call twice": (
        a_reply_answering_a_call_twice,
        False,
        "more than one answer for 'Which colour?'",
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
        queued_reply(run, results),
        run.reply("Blue.", by="ada"),
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


def two_questions_factory() -> Factory:
    both = ModelResponse(
        parts=[
            ask_call("Keep ticked tasks?", call_id="q1"),
            ask_call("What colour are done tasks?", call_id="q2"),
        ]
    )
    asker = agent("asker", both, "Kept, in grey.", capabilities=[AskPerson()])
    return Factory("software", [station(asker)])


def publishing_factory(*, then_ask: bool = False) -> tuple[Factory, list[str]]:
    published: list[str] = []
    parts = [ToolCallPart("publish_page", {"path": "index.html"}, "p1")]
    if then_ask:
        parts.append(ask_call("Which page next?", call_id="q1"))

    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        returned = [
            part
            for message in messages
            for part in getattr(message, "parts", [])
            if part.part_kind in ("tool-return", "retry-prompt")
        ]
        if not returned:
            return ModelResponse(parts=list(parts))
        said = "; ".join(str(part.content) for part in returned)
        return ModelResponse(parts=[TextPart(f"Done: {said}")])

    publisher = Agent(FunctionModel(reply), name="publisher", capabilities=[AskPerson()])

    @publisher.tool_plain(requires_approval=True)
    def publish_page(path: str) -> str:
        published.append(path)
        return f"published {path}"

    return Factory("software", [station(publisher)]), published


async def test_the_one_question_a_run_waits_on_prints_as_its_text() -> None:
    run = await asking_factory().run("Pick a colour.", store=MemoryStore())

    assert isinstance(run.question, Question)
    assert str(run.question) == "Which colour?"
    assert (run.questions, run.action, run.actions) == ((run.question,), None, ())


async def test_a_reply_to_several_questions_maps_each_question_to_its_answer() -> None:
    software = two_questions_factory()
    store = MemoryStore()
    run = await software.run("A to-do list page.", store=store)
    assert run.question is None

    first, second = run.questions
    answer = run.reply({first: "Yes, keep them.", second: "Grey."}, by="ada")
    done = await software.resume(answer, store=store)

    assert (str(first), str(second)) == ("Keep ticked tasks?", "What colour are done tasks?")
    assert done.outcome == "passed"
    assert answer.results.calls == {"q1": {"Q1": ["Yes, keep them."]}, "q2": {"Q1": ["Grey."]}}
    assert answer.results.answered_by == {"q1": "ada", "q2": "ada"}


async def test_one_text_reply_is_refused_when_the_run_waits_on_more_than_one_question() -> None:
    run = await two_questions_factory().run("A to-do list page.", store=MemoryStore())

    with pytest.raises(ApprovalRefused, match=r"waits on 2 questions; answer each one: run\.reply"):
        run.reply("Yes.", by="ada")


async def test_a_reply_that_misses_a_question_is_refused_by_name_before_it_is_queued() -> None:
    run = await two_questions_factory().run("A to-do list page.", store=MemoryStore())
    first, _ = run.questions

    with pytest.raises(ApprovalRefused, match="no answer for 'What colour are done tasks\\?'"):
        run.reply({first: "Yes."}, by="ada")


async def test_run_reply_and_check_refuse_a_bad_reply_with_the_same_reason() -> None:
    software = two_questions_factory()
    run = await software.run("A to-do list page.", store=MemoryStore())
    first, _ = run.questions

    with pytest.raises(ApprovalRefused) as refused:
        run.reply({first: "Yes."}, by="ada")
    problem = software.check(
        run, queued_reply(run, DeferredToolResults(calls={"q1": {"Q1": ["Yes."]}}))
    )

    assert problem is not None
    assert str(refused.value) == problem.reason


async def test_an_action_the_run_waits_on_runs_once_it_is_allowed() -> None:
    software, published = publishing_factory()
    store = MemoryStore()
    run = await software.run("Publish the page.", store=store)

    assert isinstance(run.action, Action)
    assert str(run.action) == "publish_page(path='index.html')"
    done = await software.resume(run.allow(by="ada"), store=store)

    assert (done.outcome, published) == ("passed", ["index.html"])


async def test_a_denied_action_does_not_run_and_the_agent_is_told_why() -> None:
    software, published = publishing_factory()
    store = MemoryStore()
    run = await software.run("Publish the page.", store=store)

    done = await software.resume(run.deny(by="ada", note="Not on a Friday."), store=store)

    assert (done.outcome, published) == ("passed", [])
    assert done.output == "Done: Not on a Friday."


async def test_denying_an_action_needs_a_note_that_is_not_blank() -> None:
    software, _ = publishing_factory()
    run = await software.run("Publish the page.", store=MemoryStore())

    with pytest.raises(ApprovalRefused, match="needs a note"):
        run.deny(by="ada", note="  ")


async def test_a_question_and_an_action_together_are_answered_in_one_mapping() -> None:
    software, published = publishing_factory(then_ask=True)
    store = MemoryStore()
    run = await software.run("Publish the page.", store=store)

    with pytest.raises(ApprovalRefused, match="waits on 1 question and 1 action"):
        run.allow(by="ada")
    assert run.action is not None and run.question is not None
    answer = run.reply({run.action: True, run.question: "The about page."}, by="ada")
    done = await software.resume(answer, store=store)

    assert (done.outcome, published) == ("passed", ["index.html"])


async def test_an_action_answered_with_text_is_refused() -> None:
    software, _ = publishing_factory()
    run = await software.run("Publish the page.", store=MemoryStore())
    assert run.action is not None

    with pytest.raises(ApprovalRefused, match="allowed with True or denied with False"):
        run.reply({run.action: "yes"}, by="ada")


async def test_allowing_is_refused_when_the_run_waits_on_a_question() -> None:
    run = await asking_factory().run("Pick a colour.", store=MemoryStore())

    with pytest.raises(ApprovalRefused, match="waits on 1 question"):
        run.allow(by="ada")


async def test_a_stale_reply_is_reported_as_stale_before_its_answers_are_checked() -> None:
    run = await asking_factory().run("Pick a colour.", store=MemoryStore())

    with pytest.raises(RunConflict, match="has moved on"):
        run.reply({"q9": "Red."}, by="ada", revision=run.revision - 1)


async def test_a_question_from_a_tool_of_your_own_is_answered_in_words() -> None:
    asker = agent(
        "asker",
        ModelResponse(parts=[ToolCallPart("ask_me", {"question": "Colour?"}, "q1")]),
        "Blue.",
    )

    @asker.tool_plain
    def ask_me(question: str) -> str:
        raise CallDeferred({"question": question}, kind="question")

    software = Factory("software", [station(asker)])
    store = MemoryStore()
    run = await software.run("Pick a colour.", store=store)
    question = run.question
    assert question is not None
    assert (str(question), question.header, question.options) == ("Colour?", None, ())

    answer = run.reply("Blue, please.", by="ada")
    done = await software.resume(answer, store=store)

    assert answer.results.calls == {"q1": "Blue, please."}
    assert done.outcome == "passed"


async def test_a_run_waiting_on_the_old_ask_person_tool_is_answered_in_words() -> None:
    # Before AskUser, AskPerson deferred an `ask_person` call with its question in the metadata.
    before = agent(
        "asker",
        ModelResponse(parts=[ToolCallPart("ask_person", {"question": "Colour?"}, "q1")]),
        "Blue.",
    )

    @before.tool_plain
    def ask_person(question: str) -> str:
        raise CallDeferred({"question": question})

    store = MemoryStore()
    run = await Factory("software", [station(before)]).run("Pick a colour.", store=store)
    question = run.question
    assert question is not None
    assert (str(question), question.tool, question.header, question.options) == (
        "Colour?",
        "ask_person",
        None,
        (),
    )

    now = Factory("software", [station(agent("asker", "Blue.", capabilities=[AskPerson()]))])
    answer = run.reply("Blue, please.", by="ada")
    done = await now.resume(answer, store=store)

    assert answer.results.calls == {"q1": "Blue, please."}
    assert done.outcome == "passed"


async def test_an_ask_person_question_is_answered_with_a_label_or_your_own_words() -> None:
    both = ask(asked("Keep ticks?", "Ticks"), asked("Colour?", "Colour", ("Grey", "Green")))
    asker = agent("asker", both, "Kept, in blue.", capabilities=[AskPerson()])
    software = Factory("software", [station(asker)])
    store = MemoryStore()
    run = await software.run("A to-do list page.", store=store)
    ticks, colour = run.questions
    assert (ticks.header, [option.label for option in colour.options]) == (
        "Ticks",
        ["Grey", "Green"],
    )

    with pytest.raises(ApprovalRefused, match="unanswered questions: \\['Colour'\\]"):
        run.reply({ticks: "Yes (Recommended)"}, by="ada")
    answer = run.reply({ticks: "Yes (Recommended)", colour: "Blue, please."}, by="ada")
    done = await software.resume(answer, store=store)

    assert answer.results.calls == {
        "q1": {"Ticks": ["Yes (Recommended)"], "Colour": ["Blue, please."]}
    }
    assert done.outcome == "passed"


async def test_a_multi_select_question_takes_a_list_of_labels() -> None:
    picks = ask(asked("Which pages?", "Pages", ("Home", "About", "Blog"), multi_select=True))
    asker = agent("asker", picks, "Built.", capabilities=[AskPerson()])
    run = await Factory("software", [station(asker)]).run("A site.", store=MemoryStore())
    question = run.question
    assert question is not None and question.multi_select

    answer = run.reply({question: ["Home", "Blog"]}, by="ada")

    assert answer.results.calls == {"q1": {"Pages": ["Home", "Blog"]}}
    with pytest.raises(ApprovalRefused, match="picked options it does not offer"):
        run.reply({question: ["Home", "Shop"]}, by="ada")
