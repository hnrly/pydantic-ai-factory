"""Elephant Goldfish: fresh goldfish review the author's draft inside the author's own run."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from asking import ASK, ask
from pydantic import BaseModel
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.capabilities import Toolset
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.usage import UsageLimits

from pydantic_ai_factory import (
    AskPerson,
    DefinitionError,
    Factory,
    MemoryStore,
    MethodologyDefinitionError,
    StationContext,
    station,
)
from pydantic_ai_factory.methodologies import (
    ElephantGoldfish,
    Goldfish,
    GoldfishReport,
    Standard,
)

pytestmark = pytest.mark.anyio

MISSION = "A to-do list web page where I can add tasks and tick them off."
DRAFT = "One page. Type a task and press Enter to add it; tick its box to mark it done."
FIXED = DRAFT + " Pressing Enter on a blank task adds nothing."
BLANK: dict[str, Any] = {
    "severity": "Major",
    "issue": "It does not say what happens to a blank task.",
}
NIT: dict[str, Any] = {"severity": "Nit", "issue": "Say 'tick box', not 'box'."}
CLEAR: dict[str, Any] = {
    "paraphrase": "A page that keeps a list of tasks you can tick off.",
    "findings": [],
}

ACCESSIBLE = Standard("accessible", "Everything works with a keyboard and a screen reader.")
POND = (
    Goldfish("newcomer", stage="comprehension", job="Review as newcomer."),
    Goldfish("critique", stage="critique", job="Review as critique.", standard=ACCESSIBLE),
)


class Author:
    """The author's model: replies in turn, and keeps what it was sent and offered."""

    def __init__(self, *replies: str | ModelResponse) -> None:
        self.replies = list(replies)
        self.seen: list[list[ModelMessage]] = []
        self.tools: list[list[str]] = []
        self.instructions: list[str] = []

    @property
    def calls(self) -> int:
        return len(self.seen)

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.seen.append(list(messages))
        self.tools.append([tool.name for tool in info.function_tools])
        self.instructions.append(info.instructions or "")
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return reply if isinstance(reply, ModelResponse) else ModelResponse(parts=[TextPart(reply)])


class Pond:
    """The goldfish's model: each goldfish, told apart by its job, replies in turn."""

    def __init__(self, **replies: list[Any]) -> None:
        self.replies = replies
        self.prompts: dict[str, list[str]] = {name: [] for name in replies}
        self.tools: dict[str, list[str]] = {name: [] for name in replies}

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        name = next(n for n in self.replies if f"Review as {n}." in (info.instructions or ""))
        first = messages[0]
        assert isinstance(first, ModelRequest)
        self.prompts[name].append(str(getattr(first.parts[-1], "content", "")))
        self.tools[name] = [tool.name for tool in info.function_tools]
        queue = self.replies[name]
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, Exception):
            raise reply
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, reply)])


def call(tool: str, **args: Any) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool, args, f"{tool}-1")])


def designer(
    author: Author, pond: Pond | None = None, *, asks: bool = True, **settings: Any
) -> tuple[Agent[None, str], ElephantGoldfish]:
    """An author with Elephant Goldfish, which brings ``AskPerson`` unless ``asks`` is false."""
    if not asks:
        settings["ask"] = None
    method = ElephantGoldfish(
        goldfish=POND, model=FunctionModel(pond) if pond else None, **settings
    )
    return Agent(FunctionModel(author), name="designer", capabilities=[method]), method


async def reports_of(result: Any, store: MemoryStore) -> list[GoldfishReport]:
    return [
        (await store.get(ref, GoldfishReport)).payload
        for ref in result.evidence
        if ref.type == "GoldfishReport"
    ]


def retries(messages: list[ModelMessage]) -> list[str]:
    """The feedback the author was sent when its output was sent back."""
    return [
        str(part.content)
        for message in messages
        for part in message.parts
        if isinstance(part, UserPromptPart) and "retry_feedback" in str(part.content)
    ]


async def test_a_clean_first_round_passes_and_stamps_the_methodology() -> None:
    author = Author(DRAFT)
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": []}])
    agent, _ = designer(author, pond)
    store = MemoryStore()

    result = await station(agent).run(MISSION, store=store)

    assert result.outcome == "passed"
    assert result.decision is not None
    assert result.decision.gate == "clean_final_round"
    assert result.decision.reasons == ("round 1 was clean: 2 goldfish found nothing blocking",)
    assert [str(m) for m in result.decision.methodologies] == ["elephant-goldfish@1"]
    assert author.calls == 1
    [report] = await reports_of(result, store)
    assert (report.round, report.goldfish, report.findings) == (1, ("newcomer", "critique"), ())
    assert report.results[0].paraphrase == CLEAR["paraphrase"]
    stored = await store.get(result.evidence[0], GoldfishReport)
    assert [str(m) for m in stored.methodologies] == ["elephant-goldfish@1"]


async def test_a_goldfish_sees_only_the_draft_its_job_and_its_standard() -> None:
    author = Author(DRAFT)
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": []}])
    agent, method = designer(author, pond)

    await station(agent).run(MISSION, store=MemoryStore())

    [prompt] = pond.prompts["critique"]
    assert DRAFT in prompt and MISSION not in prompt
    critic = method._agents["critique"]  # pyright: ignore[reportPrivateUsage]
    assert critic.name == "goldfish-critique"
    instructions = str(critic._instructions)  # pyright: ignore[reportPrivateUsage]
    assert "Review as critique." in instructions and ACCESSIBLE.text in instructions


async def test_a_major_goes_back_to_the_author_who_fixes_it_in_the_same_conversation() -> None:
    author = Author(DRAFT, FIXED)
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": [BLANK]}, {"findings": []}])
    agent, _ = designer(author, pond)
    store = MemoryStore()

    result = await station(agent).run(MISSION, store=store)

    assert result.outcome == "passed"
    assert result.decision is not None
    assert result.decision.reasons == ("round 2 was clean: 2 goldfish found nothing blocking",)
    assert author.calls == 2
    [feedback] = retries(author.seen[1])
    assert "round 1 of 2" in feedback
    assert "r1-critique-1, Major: It does not say what happens to a blank task." in feedback
    assert CLEAR["paraphrase"] in feedback
    assert any(DRAFT in str(part) for m in author.seen[1] for part in m.parts)
    assert [r.round for r in await reports_of(result, store)] == [1, 2]
    assert len(result.calls) == 1


async def test_a_nit_never_blocks_a_round() -> None:
    author = Author(DRAFT)
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": [NIT]}])
    agent, _ = designer(author, pond)
    store = MemoryStore()

    result = await station(agent).run(MISSION, store=store)

    assert (result.outcome, author.calls) == ("passed", 1)
    [report] = await reports_of(result, store)
    assert report.clean and [f.severity for f in report.findings] == ["Nit"]


async def test_findings_are_numbered_by_round_goldfish_and_order() -> None:
    author = Author(DRAFT, FIXED)
    pond = Pond(
        newcomer=[{**CLEAR, "findings": [NIT]}],
        critique=[{"findings": [BLANK, NIT]}, {"findings": [NIT]}],
    )
    agent, _ = designer(author, pond)
    store = MemoryStore()

    result = await station(agent).run(MISSION, store=store)

    ids = [f.id for report in await reports_of(result, store) for f in report.findings]
    assert ids == [
        "r1-newcomer-1",
        "r1-critique-1",
        "r1-critique-2",
        "r2-newcomer-1",
        "r2-critique-1",
    ]


async def test_a_contested_finding_goes_to_a_person_as_an_escalation() -> None:
    author = Author(
        DRAFT,
        call("contest", finding_id="r1-critique-1", evidence="Blank tasks are out of scope."),
        DRAFT,
    )
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": [BLANK]}, {"findings": []}])
    agent, _ = designer(author, pond)
    store = MemoryStore()

    result = await station(agent).run(MISSION, store=store)

    assert result.outcome == "waiting"
    assert result.waiting is not None and result.waiting.kind == "escalation"
    assert result.waiting.reasons == (
        "r1-critique-1 contested: It does not say what happens to a blank task. "
        "(Blank tasks are out of scope.)",
    )
    first = (await reports_of(result, store))[0]
    assert [(f.answer, f.note) for f in first.findings] == [
        ("contested", "Blank tasks are out of scope.")
    ]


async def test_an_escalated_finding_goes_to_a_person_as_an_escalation() -> None:
    author = Author(
        DRAFT,
        call("escalate", finding_id="r1-critique-1", reason="Only the owner knows."),
        DRAFT,
    )
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": [BLANK]}, {"findings": []}])
    agent, _ = designer(author, pond)

    result = await station(agent).run(MISSION, store=MemoryStore())

    assert result.waiting is not None and result.waiting.kind == "escalation"
    assert result.waiting.reasons[0].startswith("r1-critique-1 escalated:")
    assert result.waiting.output == DRAFT


async def test_a_nit_cannot_be_contested() -> None:
    author = Author(DRAFT, call("contest", finding_id="r1-critique-2", evidence="No."), FIXED)
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": [BLANK, NIT]}, {"findings": []}])
    agent, _ = designer(author, pond, rounds=3)

    result = await station(agent).run(MISSION, store=MemoryStore())

    assert result.outcome == "passed"
    told = [str(getattr(part, "content", "")) for m in author.seen[2] for part in m.parts]
    assert "r1-critique-2 is a Nit. Nits never block: fix it or leave it." in told


async def test_a_finding_still_open_when_the_rounds_run_out_goes_to_a_person() -> None:
    author = Author(DRAFT, DRAFT)
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": [BLANK]}])
    agent, _ = designer(author, pond)
    store = MemoryStore()

    result = await station(agent).run(MISSION, store=store)

    assert (result.outcome, author.calls) == ("waiting", 2)
    assert result.waiting is not None and result.waiting.kind == "escalation"
    assert result.waiting.reasons == (
        "r2-critique-1 still open after 2 rounds: It does not say what happens to a blank task.",
    )
    assert len(await reports_of(result, store)) == 2


async def test_a_goldfish_that_fails_means_the_station_did_not_run() -> None:
    author = Author(DRAFT)
    pond = Pond(newcomer=[CLEAR], critique=[TimeoutError("the model did not answer")])
    agent, _ = designer(author, pond)

    result = await station(agent).run(MISSION, store=MemoryStore())

    assert result.outcome == "did_not_run"
    assert result.decision is None
    assert result.error_type == "GoldfishFailed"
    assert "goldfish 'critique' did not run in round 1" in (result.reason or "")


async def test_the_author_may_ask_a_question_and_the_run_waits_for_the_answer() -> None:
    author = Author(ask("Should ticked tasks survive a reload?"), FIXED)
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": []}])
    agent, _ = designer(author, pond)
    software = Factory("software", [station(agent)])
    store = MemoryStore()

    run = await software.run(MISSION, store=store)

    assert run.waiting is not None and run.waiting.kind == "tool-calls"
    assert pond.prompts["critique"] == []
    done = await software.resume(run.reply("Yes.", by="ada"), store=store)

    assert (done.outcome, done.output) == ("passed", FIXED)
    assert ASK in author.tools[0]


def both(*responses: ModelResponse) -> ModelResponse:
    return ModelResponse(parts=[part for response in responses for part in response.parts])


async def answer_questions(software: Factory, run: Any, store: MemoryStore) -> Any:
    while run.outcome == "waiting" and run.waiting.kind == "tool-calls":
        run = await software.resume(run.reply("Yes.", by="ada"), store=store)
    return run


@pytest.mark.parametrize("together", [False, True], ids=["after", "together"])
async def test_a_finding_contested_before_a_question_still_goes_to_a_person(
    together: bool,
) -> None:
    contest = call("contest", finding_id="r1-critique-1", evidence="Blank tasks are out of scope.")
    question = ask("Should ticked tasks survive a reload?")
    replies = [both(contest, question)] if together else [contest, question]
    author = Author(DRAFT, *replies, DRAFT)
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": [BLANK]}, {"findings": []}])
    agent, _ = designer(author, pond)
    software = Factory("software", [station(agent)])
    store = MemoryStore()

    done = await answer_questions(software, await software.run(MISSION, store=store), store)

    assert done.waiting is not None and done.waiting.kind == "escalation"
    assert done.waiting.reasons == (
        "r1-critique-1 contested: It does not say what happens to a blank task. "
        "(Blank tasks are out of scope.)",
    )


async def test_a_question_between_rounds_does_not_reset_the_round_count() -> None:
    question = ask("Should ticked tasks survive a reload?")
    author = Author(DRAFT, question, DRAFT)
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": [BLANK]}])
    agent, _ = designer(author, pond, rounds=2)
    software = Factory("software", [station(agent)])
    store = MemoryStore()

    done = await answer_questions(software, await software.run(MISSION, store=store), store)

    assert done.waiting is not None and done.waiting.kind == "escalation"
    assert done.waiting.reasons == (
        "r2-critique-1 still open after 2 rounds: It does not say what happens to a blank task.",
    )
    assert len(pond.prompts["critique"]) == 2


async def test_goldfish_requests_count_once_against_the_authors_usage() -> None:
    author = Author(DRAFT)
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": []}])
    agent, _ = designer(author, pond)

    result = await agent.run(MISSION)

    assert result.usage.requests == 3  # the author, then two goldfish


async def test_goldfish_stop_when_the_authors_request_limit_is_reached() -> None:
    author = Author(DRAFT)
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": []}])
    agent, _ = designer(author, pond)

    with pytest.raises(UsageLimitExceeded):
        await agent.run(MISSION, usage_limits=UsageLimits(request_limit=2))
    assert pond.prompts["critique"] == []


async def test_elephant_goldfish_brings_ask_person_so_the_author_can_ask() -> None:
    author = Author(DRAFT)
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": []}])
    agent, _ = designer(author, pond)

    await station(agent).run(MISSION, store=MemoryStore())

    assert sorted(author.tools[0]) == sorted([ASK, "contest", "escalate"])
    assert "3 rounds of questions" in author.instructions[0]


async def test_with_ask_none_elephant_goldfish_gives_the_author_no_way_to_ask() -> None:
    author = Author(DRAFT)
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": []}])
    agent, _ = designer(author, pond, asks=False)

    await station(agent).run(MISSION, store=MemoryStore())

    assert sorted(author.tools[0]) == ["contest", "escalate"]


async def test_an_ask_person_the_agent_lists_wins_over_the_one_elephant_goldfish_brings() -> None:
    author = Author(DRAFT)
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": []}])
    method = ElephantGoldfish(goldfish=POND, model=FunctionModel(pond), ask=AskPerson(max_rounds=5))
    agent = Agent(
        FunctionModel(author), name="designer", capabilities=[method, AskPerson(max_rounds=1)]
    )

    await station(agent).run(MISSION, store=MemoryStore())

    assert author.tools[0].count(ASK) == 1
    assert "1 round of questions" in author.instructions[0]
    assert "5 rounds" not in author.instructions[0]


def test_elephant_goldfish_refuses_an_ask_that_is_not_ask_person() -> None:
    with pytest.raises(MethodologyDefinitionError, match="ask must be an AskPerson"):
        ElephantGoldfish(goldfish=POND, ask="yes")  # pyright: ignore[reportArgumentType]


def test_two_ask_person_the_agent_lists_must_agree() -> None:
    with pytest.raises(DefinitionError, match="two AskPerson with different max_rounds"):
        Agent(
            TestModel(),
            name="asker",
            capabilities=[AskPerson(max_rounds=1), AskPerson(max_rounds=2)],
        )


async def test_on_resume_a_replayed_author_call_keeps_its_reports() -> None:
    author = Author(DRAFT, FIXED)
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": [BLANK]}, {"findings": []}])
    agent, _ = designer(author, pond)
    checker = Agent(
        FunctionModel(Author(ask("Ship it?"), "Yes.")),
        name="checker",
        capabilities=[AskPerson()],
    )

    @station
    async def design(ctx: StationContext, mission: str) -> str:
        drafted = await ctx.run_agent(agent, mission)
        await ctx.run_agent(checker, drafted.output)
        return drafted.output

    software = Factory("software", [design])
    store = MemoryStore()
    run = await software.run(MISSION, store=store)
    done = await software.resume(run.reply("Yes.", by="ada"), store=store)

    assert done.outcome == "passed"
    assert author.calls == 2
    last = done.steps[-1]
    assert [r.round for r in await reports_of(last, store)] == [1, 2]
    assert last.calls[0].evidence == last.evidence


async def test_by_default_the_goldfish_use_the_authors_model() -> None:
    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if "goldfish" in (info.instructions or "") and info.output_tools:
            args = CLEAR if "paraphrase" in str(info.output_tools[0].parameters_json_schema) else {}
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])
        return ModelResponse(parts=[TextPart(DRAFT)])

    method = ElephantGoldfish(goldfish=POND)
    agent = Agent(FunctionModel(model), name="designer", capabilities=[method])

    result = await station(agent).run(MISSION, store=MemoryStore())

    assert result.outcome == "passed"


async def test_a_last_round_of_a_different_draft_fails_the_gate() -> None:
    author = Author(DRAFT)
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": []}])
    agent, _ = designer(author, pond)

    @station
    async def design(ctx: StationContext, mission: str) -> str:
        drafted = await ctx.run_agent(agent, mission)
        return drafted.output + " Edited after review."

    result = await design.run(MISSION, store=MemoryStore())

    assert result.outcome == "failed"
    assert result.decision is not None
    assert "read a different draft" in result.decision.reasons[0]


async def test_a_structured_draft_is_reviewed_as_json() -> None:
    class Design(BaseModel):
        summary: str

    pond = Pond(newcomer=[CLEAR], critique=[{"findings": []}])

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"summary": DRAFT})])

    method = ElephantGoldfish(goldfish=POND, model=FunctionModel(pond))
    agent = Agent(FunctionModel(model), name="designer", output_type=Design, capabilities=[method])

    result = await station(agent).run(MISSION, store=MemoryStore())

    assert result.outcome == "passed"
    assert '"summary"' in pond.prompts["critique"][0]


def test_on_an_agent_of_its_own_it_reviews_and_revises_like_any_capability() -> None:
    author = Author(DRAFT, FIXED)
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": [BLANK]}, {"findings": []}])
    agent, _ = designer(author, pond)

    result = agent.run_sync(MISSION, retries={"output": 3})

    assert result.output == FIXED
    assert author.calls == 2


def test_a_factory_lists_elephant_goldfish_among_its_methodologies() -> None:
    agent, method = designer(Author(DRAFT))

    assert Factory("software", [station(agent)]).methodologies() == {"designer": (method,)}
    assert method.output_retries == 2


@pytest.mark.parametrize(
    ("settings", "problem"),
    [
        ({"goldfish": ()}, "at least one goldfish"),
        ({"goldfish": "newcomer"}, "must be a list of Goldfish"),
        ({"goldfish": (POND[0], POND[0])}, "two goldfish are named 'newcomer'"),
        ({"goldfish": POND, "rounds": 0}, "rounds must be a whole number, 1 or more"),
    ],
)
def test_a_badly_set_up_elephant_goldfish_is_refused(
    settings: dict[str, Any], problem: str
) -> None:
    with pytest.raises(MethodologyDefinitionError, match=problem):
        ElephantGoldfish(**settings)


def test_a_goldfish_needs_a_short_name_a_stage_and_a_job() -> None:
    with pytest.raises(MethodologyDefinitionError, match="letters, digits"):
        Goldfish("two words", stage="critique", job="Find faults.")
    with pytest.raises(MethodologyDefinitionError, match="stage must be one of"):
        Goldfish("critic", stage="proofread", job="Find faults.")  # pyright: ignore[reportArgumentType]
    with pytest.raises(MethodologyDefinitionError, match="the job"):
        Goldfish("critic", stage="critique", job=" ")


def test_a_deferred_question_is_not_reviewed_as_a_draft() -> None:
    author = Author(ask("Keep ticks?"))
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": []}])
    agent, _ = designer(author, pond)

    result = agent.run_sync(MISSION, output_type=[str, DeferredToolRequests])

    assert isinstance(result.output, DeferredToolRequests)
    assert pond.prompts["critique"] == []


READY = Goldfish("ready", stage="readiness", job="Review as ready.")


class Design(BaseModel):
    summary: str


def drafts_a_design(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"summary": DRAFT})])


async def test_goldfish_read_the_text_from_to_text() -> None:
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": []}])
    method = ElephantGoldfish(
        goldfish=POND, model=FunctionModel(pond), to_text=lambda d: f"# Design\n\n{d.summary}"
    )
    agent = Agent(
        FunctionModel(drafts_a_design), name="designer", output_type=Design, capabilities=[method]
    )

    result = await station(agent).run(MISSION, store=MemoryStore())

    assert result.outcome == "passed"  # the gate still checks the exact output
    for prompt in (pond.prompts["newcomer"][0], pond.prompts["critique"][0]):
        assert f"# Design\n\n{DRAFT}" in prompt and '"summary"' not in prompt


async def test_only_goldfish_that_see_the_request_get_it() -> None:
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": []}], ready=[{"findings": []}])
    seeing = Goldfish("ready", stage="readiness", job="Review as ready.", sees_request=True)
    method = ElephantGoldfish(goldfish=(*POND, seeing), model=FunctionModel(pond))
    agent = Agent(FunctionModel(Author(DRAFT)), name="designer", capabilities=[method])

    await station(agent).run(MISSION, store=MemoryStore())

    [ready] = pond.prompts["ready"]
    assert "<request-" in ready and MISSION in ready
    for name in ("newcomer", "critique"):
        assert "<request-" not in pond.prompts[name][0] and MISSION not in pond.prompts[name][0]


async def test_a_request_function_chooses_the_request_goldfish_see() -> None:
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": []}])
    seeing = Goldfish(
        "newcomer", stage="comprehension", job="Review as newcomer.", sees_request=True
    )
    method = ElephantGoldfish(
        goldfish=(seeing, POND[1]),
        model=FunctionModel(pond),
        request=lambda ctx: "The brief, as the person wrote it.",
    )
    agent = Agent(FunctionModel(Author(DRAFT)), name="designer", capabilities=[method])

    await agent.run(MISSION)

    [prompt] = pond.prompts["newcomer"]
    assert "The brief, as the person wrote it." in prompt and MISSION not in prompt


async def test_critique_gets_the_explain_back() -> None:
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": []}], ready=[{"findings": []}])
    method = ElephantGoldfish(goldfish=(*POND, READY), model=FunctionModel(pond))
    agent = Agent(FunctionModel(Author(DRAFT)), name="designer", capabilities=[method])

    await agent.run(MISSION)

    [critique] = pond.prompts["critique"]
    marker = critique.split("The marker for this review is ")[1].split(".")[0]
    assert (
        f"<explained-{marker}>\nGoldfish newcomer explained the work as:\n"
        f"{CLEAR['paraphrase']}\n</explained-{marker}>"
    ) in critique
    assert critique.index("<explained-") < critique.index("<work-")
    assert "<explained-" not in pond.prompts["ready"][0]
    assert "<explained-" not in pond.prompts["newcomer"][0]


async def test_capabilities_reach_every_goldfish() -> None:
    files: FunctionToolset[Any] = FunctionToolset(id="files")

    @files.tool_plain
    def read_file(path: str) -> str:
        """Read a file."""
        return ""

    pond = Pond(newcomer=[CLEAR], critique=[{"findings": []}], ready=[{"findings": []}])
    method = ElephantGoldfish(
        goldfish=(*POND, READY), model=FunctionModel(pond), capabilities=[Toolset(files)]
    )
    agent = Agent(FunctionModel(Author(DRAFT)), name="designer", capabilities=[method])

    await agent.run(MISSION)

    assert pond.tools == {
        "newcomer": ["read_file"],
        "critique": ["read_file"],
        "ready": ["read_file"],
    }


async def test_goldfish_after_comprehension_run_together() -> None:
    order: list[str] = []
    started = {"critique": asyncio.Event(), "ready": asyncio.Event()}

    async def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        name = next(
            n
            for n in ("newcomer", "critique", "ready")
            if f"Review as {n}." in (info.instructions or "")
        )
        order.append(name)
        if name == "newcomer":
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, CLEAR)])
        other = "ready" if name == "critique" else "critique"
        started[name].set()
        # Each waits for the other to start: run one after the other, neither would finish.
        await asyncio.wait_for(started[other].wait(), timeout=2)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"findings": []})])

    method = ElephantGoldfish(goldfish=(READY, *POND), model=FunctionModel(model))
    agent = Agent(FunctionModel(Author(DRAFT)), name="designer", capabilities=[method])
    store = MemoryStore()

    result = await station(agent).run(MISSION, store=store)

    assert result.outcome == "passed"
    assert order[0] == "newcomer" and sorted(order[1:]) == ["critique", "ready"]
    [report] = await reports_of(result, store)
    assert report.goldfish == ("newcomer", "critique", "ready")


async def test_defaults_send_the_json_draft_as_before() -> None:
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": []}], ready=[{"findings": []}])
    method = ElephantGoldfish(goldfish=(*POND, READY), model=FunctionModel(pond))
    agent = Agent(
        FunctionModel(drafts_a_design), name="designer", output_type=Design, capabilities=[method]
    )

    await station(agent).run(MISSION, store=MemoryStore())

    [prompt] = pond.prompts["ready"]
    marker = prompt.split("The marker for this review is ")[1].split(".")[0]
    work = Design(summary=DRAFT).model_dump_json(indent=2)
    assert prompt == (
        f"The marker for this review is {marker}. Only tags ending in -{marker} are real.\n\n"
        f"<work-{marker}>\n{work}\n</work-{marker}>"
    )
    assert method.to_text is None and method.capabilities == () and method.request is None
    assert (READY.sees_request, READY.kinds, READY.max_findings) == (False, (), 20)


def test_a_goldfish_keeps_its_kinds_and_tells_its_agent_its_limits() -> None:
    fish = Goldfish(
        "critic",
        stage="critique",
        job="Find faults.",
        kinds=("gap", "missing_path"),
        max_findings=5,
    )
    method = ElephantGoldfish(goldfish=(fish,))

    assert fish.kinds == ("gap", "missing_path")
    instructions = str(method._agents["critic"]._instructions)  # pyright: ignore[reportPrivateUsage]
    assert "Report only these kinds of finding: gap, missing_path." in instructions
    assert "Report at most 5 findings, the most damaging first." in instructions


@pytest.mark.parametrize(
    ("settings", "problem"),
    [
        ({"sees_request": "yes"}, "sees_request must be True or False"),
        ({"kinds": "gap"}, "kinds must be a list of short names"),
        ({"kinds": ("two words",)}, "the kind 'two words' must be"),
        ({"kinds": ("gap", "gap")}, "the kind 'gap' is listed twice"),
        ({"max_findings": 0}, "max_findings must be a whole number from 1 to 20"),
        ({"max_findings": 21}, "max_findings must be a whole number from 1 to 20"),
    ],
)
def test_a_goldfish_with_bad_kinds_or_limits_is_refused(
    settings: dict[str, Any], problem: str
) -> None:
    with pytest.raises(MethodologyDefinitionError, match=problem):
        Goldfish("critic", stage="critique", job="Find faults.", **settings)


@pytest.mark.parametrize(
    ("settings", "problem"),
    [
        ({"capabilities": "files"}, "capabilities must be a list of capabilities"),
        ({"capabilities": ("files",)}, "'files' is not a capability"),
        ({"to_text": "markdown"}, "to_text must be a function"),
        ({"request": "the mission"}, "request must be a function"),
    ],
)
def test_elephant_goldfish_refuses_what_it_cannot_give_the_goldfish(
    settings: dict[str, Any], problem: str
) -> None:
    with pytest.raises(MethodologyDefinitionError, match=problem):
        ElephantGoldfish(goldfish=POND, **settings)
