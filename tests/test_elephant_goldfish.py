"""Elephant Goldfish: fresh goldfish review the author's draft inside the author's own run."""

from __future__ import annotations

import asyncio
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.capabilities import LocalWorkspace, Toolset
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.usage import UsageLimits
from pydantic_ai.workspaces import LocalWorkspaceBackend, ReadOnlyWorkspace, Workspace
from pydantic_ai_harness.skills import Skills
from pydantic_ai_harness.skills._loader import parse_skill

from pydantic_ai_factory import (
    AskPerson,
    Factory,
    MemoryStore,
    MethodologyDefinitionError,
    StationContext,
    station,
)
from pydantic_ai_factory.methodologies import (
    ElephantGoldfish,
    ExplainBack,
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
EXPLAINED = ExplainBack(
    problem="Keeping track of tasks.",
    who="One person with a list of tasks.",
    done_when=["Tasks can be added and ticked off."],
    approach="A page that keeps a list of tasks you can tick off.",
)
PARAPHRASE = EXPLAINED.to_text()
CLEAR: dict[str, Any] = {"explain_back": EXPLAINED.model_dump(), "findings": []}

ACCESSIBLE = Standard("accessible", "Everything works with a keyboard and a screen reader.")
POND = (
    Goldfish("newcomer", stage="comprehension", job="Review as newcomer."),
    Goldfish("critique", stage="critique", job="Review as critique.", standard=ACCESSIBLE),
)


class Author:
    """The author's model: replies in turn, and keeps what it was sent and offered."""

    def __init__(self, *replies: str | dict[str, Any] | ModelResponse) -> None:
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
        if isinstance(reply, dict):  # a structured draft, sent through the output tool
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, reply)])
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


def ask(text: str) -> ModelResponse:
    """A round of one question, as the author asks it with ``ask_person``."""
    question = {"text": text, "why_it_matters": "It changes what the page saves."}
    return call("ask_person", questions=[question])


def designer(
    author: Author, pond: Pond | None = None, **settings: Any
) -> tuple[Agent[None, str], ElephantGoldfish]:
    """An author under Elephant Goldfish that may ask a person, as the software designer does."""
    method = ElephantGoldfish(
        goldfish=POND, model=FunctionModel(pond) if pond else None, **settings
    )
    agent = Agent(FunctionModel(author), name="designer", capabilities=[method, AskPerson()])
    return agent, method


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
    assert [str(m) for m in result.decision.methodologies] == ["elephant-goldfish@2"]
    assert author.calls == 1
    [report] = await reports_of(result, store)
    assert (report.round, report.goldfish, report.findings) == (1, ("newcomer", "critique"), ())
    assert report.results[0].paraphrase == PARAPHRASE
    stored = await store.get(result.evidence[0], GoldfishReport)
    assert [str(m) for m in stored.methodologies] == ["elephant-goldfish@2"]


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
    assert PARAPHRASE in feedback
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
    assert run.waiting.requests is not None
    assert pond.prompts["critique"] == []
    asked = run.waiting.requests.calls[0]
    done = await software.resume(
        run, run.waiting.requests.build_results(calls={asked.tool_call_id: "Yes."}), store=store
    )

    assert (done.outcome, done.output) == ("passed", FIXED)
    assert "ask_person" in author.tools[0]


def both(*responses: ModelResponse) -> ModelResponse:
    return ModelResponse(parts=[part for response in responses for part in response.parts])


async def answer_questions(software: Factory, run: Any, store: MemoryStore) -> Any:
    while run.outcome == "waiting" and run.waiting.kind == "tool-calls":
        asked = run.waiting.requests.calls[0]
        answer = run.waiting.requests.build_results(calls={asked.tool_call_id: "Yes."})
        run = await software.resume(run, answer, store=store)
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


async def test_elephant_goldfish_adds_no_ask_tool() -> None:
    author = Author(DRAFT)
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": []}])
    method = ElephantGoldfish(goldfish=POND, model=FunctionModel(pond))
    agent = Agent(FunctionModel(author), name="designer", capabilities=[method])

    await station(agent).run(MISSION, store=MemoryStore())

    # load_capability is how the author loads the method's skills; it asks nobody.
    assert sorted(author.tools[0]) == ["contest", "escalate", "load_capability"]
    assert "ask_person" not in method.get_instructions()


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
    assert run.waiting is not None and run.waiting.requests is not None
    asked = run.waiting.requests.calls[0]

    done = await software.resume(
        run, run.waiting.requests.build_results(calls={asked.tool_call_id: "Yes."}), store=store
    )

    assert done.outcome == "passed"
    assert author.calls == 2
    last = done.steps[-1]
    assert [r.round for r in await reports_of(last, store)] == [1, 2]
    assert last.calls[0].evidence == last.evidence


async def test_by_default_the_goldfish_use_the_authors_model() -> None:
    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if "goldfish" in (info.instructions or "") and info.output_tools:
            args = (
                CLEAR if "explain_back" in str(info.output_tools[0].parameters_json_schema) else {}
            )
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
        ({"goldfish": "newcomer"}, "must be a list of Goldfish"),
        ({"goldfish": (POND[0], POND[0])}, "two goldfish are named 'newcomer'"),
        ({"goldfish": POND, "rounds": 0}, "rounds must be a whole number, 1 or more"),
        ({"goldfish": POND, "clean_rounds": 0}, "clean_rounds must be a whole number, 1 or more"),
        ({"goldfish": POND, "clean_rounds": 3}, r"clean_rounds \(3\) cannot be more than rounds"),
        ({"goldfish": POND, "settle_contests": "yes"}, "settle_contests must be True or False"),
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
        f"{PARAPHRASE}\n</explained-{marker}>"
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


# Findings that can be checked and settled.

SUMMARY = {"summary": DRAFT}
UNSAID: dict[str, Any] = {
    "severity": "Major",
    "kind": "gap",
    "where": "summary",
    "issue": "The summary does not say who the page is for.",
}
CITED = "It is for one person, and the summary says so in its first word."


def critic(**settings: Any) -> Goldfish:
    return Goldfish("critique", stage="critique", job="Review as critique.", **settings)


def reviewed_design(
    author: Author, pond: Pond, *extra: Any, **settings: Any
) -> tuple[Agent[None, Design], ElephantGoldfish]:
    """An author that drafts a structured design under Elephant Goldfish."""
    method = ElephantGoldfish(goldfish=POND, model=FunctionModel(pond), **settings)
    agent = Agent(
        FunctionModel(author),
        name="designer",
        output_type=Design,
        capabilities=[method, AskPerson(), *extra],
    )
    return agent, method


def replies_to(messages: list[ModelMessage], tool: str) -> list[str]:
    """What a tool told the author."""
    return [
        str(part.content)
        for message in messages
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == tool
    ]


async def test_a_quote_not_in_the_draft_is_retried() -> None:
    misquoted = {**BLANK, "quote": "Press Return to save"}
    quoted = {**BLANK, "quote": "press  Enter\nto add it"}  # whitespace is collapsed
    author = Author(DRAFT, FIXED)
    pond = Pond(
        newcomer=[CLEAR],
        critique=[{"findings": [misquoted]}, {"findings": [quoted]}, {"findings": []}],
    )
    agent, _ = designer(author, pond)
    store = MemoryStore()

    result = await station(agent).run(MISSION, store=store)

    assert result.outcome == "passed"
    assert len(pond.prompts["critique"]) == 3  # round 1 asked twice, round 2 once
    first = (await reports_of(result, store))[0].findings[0]
    assert (first.quote, first.issue) == ("press  Enter\nto add it", BLANK["issue"])
    [feedback] = retries(author.seen[1])
    assert 'Quote: "press  Enter\nto add it"' in feedback


async def test_a_quote_still_wrong_is_dropped_with_a_note() -> None:
    author = Author(DRAFT)
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": [{**NIT, "quote": "a tick box"}]}])
    agent, _ = designer(author, pond)
    store = MemoryStore()

    result = await station(agent).run(MISSION, store=store)

    assert result.outcome == "passed"
    assert len(pond.prompts["critique"]) == 3  # the first try and two retries
    [report] = await reports_of(result, store)
    [finding] = report.findings
    assert finding.quote is None
    assert finding.issue == "Say 'tick box', not 'box'. (quote not found in the design)"


async def test_a_kind_the_goldfish_does_not_have_is_retried() -> None:
    pond = Pond(
        newcomer=[CLEAR],
        critique=[{"findings": [{**NIT, "kind": "typo"}]}, {"findings": [{**NIT, "kind": "gap"}]}],
    )
    method = ElephantGoldfish(
        goldfish=(POND[0], critic(kinds=("gap", "missing_path"))), model=FunctionModel(pond)
    )
    agent = Agent(FunctionModel(Author(DRAFT)), name="designer", capabilities=[method])
    store = MemoryStore()

    result = await station(agent).run(MISSION, store=store)

    assert result.outcome == "passed"
    assert len(pond.prompts["critique"]) == 2
    [report] = await reports_of(result, store)
    assert [f.kind for f in report.findings] == ["gap"]


async def test_more_findings_than_max_findings_are_retried() -> None:
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": [NIT, NIT]}, {"findings": [NIT]}])
    method = ElephantGoldfish(goldfish=(POND[0], critic(max_findings=1)), model=FunctionModel(pond))
    agent = Agent(FunctionModel(Author(DRAFT)), name="designer", capabilities=[method])
    store = MemoryStore()

    result = await station(agent).run(MISSION, store=store)

    assert len(pond.prompts["critique"]) == 2
    [report] = await reports_of(result, store)
    assert len(report.findings) == 1


async def test_a_contest_citing_a_field_settles_when_asked() -> None:
    contest = call("contest", finding_id="r1-critique-1", evidence=CITED, cites="field:summary")
    author = Author(SUMMARY, contest, SUMMARY)
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": [UNSAID]}, {"findings": []}])
    agent, _ = reviewed_design(author, pond, settle_contests=True)
    store = MemoryStore()

    result = await station(agent).run(MISSION, store=store)

    assert result.outcome == "passed"
    assert replies_to(author.seen[2], "contest") == [
        "r1-critique-1 is settled: code checked field:summary. No person needs to decide it."
    ]
    first = (await reports_of(result, store))[0].findings[0]
    assert (first.answer, first.settled, first.note) == (
        "contested",
        True,
        f"{CITED} (cites field:summary)",
    )


async def test_a_contest_without_settle_contests_goes_to_a_person() -> None:
    contest = call("contest", finding_id="r1-critique-1", evidence=CITED, cites="field:summary")
    author = Author(SUMMARY, contest, SUMMARY)
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": [UNSAID]}, {"findings": []}])
    agent, _ = reviewed_design(author, pond)

    result = await station(agent).run(MISSION, store=MemoryStore())

    assert result.waiting is not None and result.waiting.kind == "escalation"
    assert result.waiting.reasons == (
        f"r1-critique-1 contested: {UNSAID['issue']} ({CITED} (cites field:summary))",
    )
    assert replies_to(author.seen[2], "contest") == [
        "r1-critique-1 is contested. A person will decide it."
    ]


async def test_a_contest_citing_a_missing_field_does_not_settle() -> None:
    contest = call("contest", finding_id="r1-critique-1", evidence=CITED, cites="field:audience")
    author = Author(SUMMARY, contest, SUMMARY)
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": [UNSAID]}, {"findings": []}])
    agent, _ = reviewed_design(author, pond, settle_contests=True)

    result = await station(agent).run(MISSION, store=MemoryStore())

    assert result.waiting is not None and result.waiting.kind == "escalation"
    assert replies_to(author.seen[2], "contest") == [
        "r1-critique-1 is contested. A person will decide it, because the output has no "
        "field 'audience'."
    ]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "tasks.py").write_text("def add_task(text): ...\n")
    return tmp_path


async def test_a_contest_citing_a_missing_path_does_not_settle(repo: Path) -> None:
    contest = call("contest", finding_id="r1-critique-1", evidence=CITED, cites="app/gone.py:add")
    author = Author(SUMMARY, contest, SUMMARY)
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": [UNSAID]}, {"findings": []}])
    agent, _ = reviewed_design(author, pond, LocalWorkspace(repo), settle_contests=True)

    result = await station(agent).run(MISSION, store=MemoryStore())

    assert result.waiting is not None and result.waiting.kind == "escalation"
    assert replies_to(author.seen[2], "contest") == [
        "r1-critique-1 is contested. A person will decide it, because the path "
        "'app/gone.py' does not exist in the workspace."
    ]


async def test_a_contest_citing_a_path_that_exists_settles_when_asked(repo: Path) -> None:
    cites = "app/tasks.py:add_task"
    contest = call("contest", finding_id="r1-critique-1", evidence=CITED, cites=cites)
    author = Author(SUMMARY, contest, SUMMARY)
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": [UNSAID]}, {"findings": []}])
    agent, _ = reviewed_design(author, pond, LocalWorkspace(repo), settle_contests=True)

    result = await station(agent).run(MISSION, store=MemoryStore())

    assert result.outcome == "passed"


async def test_a_path_cannot_be_checked_without_a_workspace() -> None:
    cites = "app/tasks.py:add_task"
    contest = call("contest", finding_id="r1-critique-1", evidence=CITED, cites=cites)
    author = Author(SUMMARY, contest, SUMMARY)
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": [UNSAID]}, {"findings": []}])
    agent, _ = reviewed_design(author, pond, settle_contests=True)

    result = await station(agent).run(MISSION, store=MemoryStore())

    assert result.waiting is not None and result.waiting.kind == "escalation"
    assert "no workspace is attached" in replies_to(author.seen[2], "contest")[0]


async def test_a_repeat_of_a_settled_finding_settles() -> None:
    contest = call("contest", finding_id="r1-critique-1", evidence=CITED, cites="field:summary")
    author = Author(SUMMARY, contest, SUMMARY, SUMMARY)
    pond = Pond(
        newcomer=[CLEAR],
        critique=[{"findings": [UNSAID]}, {"findings": [UNSAID, BLANK]}, {"findings": []}],
    )
    agent, _ = reviewed_design(author, pond, settle_contests=True, rounds=3)
    store = MemoryStore()

    result = await station(agent).run(MISSION, store=store)

    assert result.outcome == "passed"
    second = (await reports_of(result, store))[1]
    repeat, other = second.findings
    assert (repeat.settled, repeat.same_as, repeat.answer) == (True, "r1-critique-1", "open")
    assert second.open == (other,)
    sent = [str(part) for m in author.seen[3] for part in m.parts]
    [feedback] = [text for text in sent if "Goldfish review, round 2" in text]
    assert "r2-critique-2" in feedback and "r2-critique-1" not in feedback


@pytest.mark.parametrize("together", [False, True], ids=["after", "together"])
async def test_a_settled_contest_stays_settled_after_a_question(together: bool) -> None:
    contest = call("contest", finding_id="r1-critique-1", evidence=CITED, cites="field:summary")
    question = ask("Should ticked tasks survive a reload?")
    replies = [both(contest, question)] if together else [contest, question]
    author = Author(SUMMARY, *replies, SUMMARY)
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": [UNSAID]}, {"findings": []}])
    agent, _ = reviewed_design(author, pond, settle_contests=True)
    software = Factory("software", [station(agent)])
    store = MemoryStore()

    done = await answer_questions(software, await software.run(MISSION, store=store), store)

    assert done.outcome == "passed"


async def test_two_clean_rounds_are_needed_when_asked() -> None:
    author = Author(DRAFT, FIXED)
    pond = Pond(
        newcomer=[CLEAR],
        critique=[{"findings": []}, {"findings": [BLANK]}, {"findings": []}, {"findings": []}],
    )
    agent, _ = designer(author, pond, rounds=4, clean_rounds=2)
    store = MemoryStore()

    result = await station(agent).run(MISSION, store=store)

    assert result.outcome == "passed"
    assert result.decision is not None
    assert result.decision.reasons == ("round 4 was clean: 2 goldfish found nothing blocking",)
    assert author.calls == 2  # a clean round alone did not end the review
    reports = await reports_of(result, store)
    assert [(r.round, r.clean) for r in reports] == [(1, True), (2, False), (3, True), (4, True)]


async def test_the_second_clean_round_reads_the_same_draft() -> None:
    author = Author(DRAFT)
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": []}])
    agent, _ = designer(author, pond, clean_rounds=2)
    store = MemoryStore()

    result = await station(agent).run(MISSION, store=store)

    assert result.outcome == "passed"
    assert author.calls == 1  # the author did not take a turn between the clean rounds
    first, second = await reports_of(result, store)
    assert (first.round, second.round) == (1, 2)
    assert first.draft_digest == second.draft_digest
    assert [DRAFT in prompt for prompt in pond.prompts["critique"]] == [True, True]


async def test_defaults_keep_one_clean_round() -> None:
    author = Author(DRAFT)
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": []}])
    agent, method = designer(author, pond)
    store = MemoryStore()

    result = await station(agent).run(MISSION, store=store)

    assert (method.clean_rounds, method.settle_contests) == (1, False)
    assert result.outcome == "passed"
    assert len(await reports_of(result, store)) == 1
    assert len(pond.prompts["critique"]) == 1


# The method as skills.

METHOD = Path(__file__).resolve().parents[1] / (
    "src/pydantic_ai_factory/methodologies/_elephant_goldfish/skills"
)
AUTHOR_SKILLS = ("design-interview", "design-resolve")
LOADS_ITS_SKILLS = (
    "Load design-interview before you ask a person, and design-resolve when review findings "
    "come back."
)


def catalogue(instructions: str) -> list[str]:
    """The skills the author was offered to load, by name, in the order listed."""
    return re.findall(r"^- ([a-z0-9-]+): ", instructions, re.MULTILINE)


def skill_texts() -> dict[str, str]:
    return {
        folder.name: (folder / "SKILL.md").read_text(encoding="utf-8")
        for folder in sorted(METHOD.iterdir())
        if (folder / "SKILL.md").is_file()
    }


def write_skill(root: Path, name: str, metadata: str, body: str) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A goldfish for tests.\n{metadata}---\n\n{body}\n",
        encoding="utf-8",
    )
    return folder


def test_default_goldfish_are_built_from_the_method_skills() -> None:
    method = ElephantGoldfish()

    assert method.goldfish == ElephantGoldfish.GOLDFISH
    comprehension, critique = ElephantGoldfish.GOLDFISH
    assert (comprehension.name, comprehension.stage, comprehension.kinds) == (
        "comprehension",
        "comprehension",
        ("unclear",),
    )
    assert not comprehension.sees_request
    assert (critique.name, critique.stage, critique.max_findings) == ("critique", "critique", 7)
    assert critique.sees_request
    assert critique.kinds == (
        "gap",
        "yagni",
        "failure_mode",
        "contradiction",
        "risk",
        "missing_alternative",
        "unjustified_decision",
    )
    assert [fish.standard.name for fish in method.goldfish if fish.standard] == [
        "clear",
        "faithful",
    ]
    texts = skill_texts()
    assert "Read the work as a capable reader" in comprehension.job
    assert "Read the work as a capable reader" in texts["goldfish-comprehension"]
    assert "sceptical expert" in critique.job
    # The standard and the credits are not part of the job.
    assert "## Standard" not in critique.job and "## Sources" not in critique.job
    assert critique.standard is not None and "single path fails" in critique.standard.text


async def test_the_author_is_offered_the_method_skills() -> None:
    author = Author(call("load_capability", id="design-resolve"), DRAFT)
    pond = Pond(newcomer=[CLEAR], critique=[{"findings": []}])
    agent, method = designer(author, pond)

    result = await agent.run(MISSION)

    assert result.output == DRAFT
    assert catalogue(author.instructions[0]) == list(AUTHOR_SKILLS)
    assert LOADS_ITS_SKILLS in method.get_instructions()
    assert "load_capability" in author.tools[0]
    sent = " ".join(
        str(getattr(part, "content", "")) for message in author.seen[-1] for part in message.parts
    )
    assert "Capability 'design-resolve' loaded" in sent
    assert "# Skill: design-resolve" in sent
    assert "Never contest a comprehension finding." in sent


async def test_no_goldfish_means_no_review() -> None:
    author = Author(DRAFT)
    method = ElephantGoldfish(goldfish=())
    agent = Agent(FunctionModel(author), name="designer", capabilities=[method, AskPerson()])
    store = MemoryStore()

    result = await station(agent).run(MISSION, store=store)

    assert result.outcome == "passed"
    assert result.decision is not None
    assert result.decision.reasons == (
        "no review was asked for: Elephant Goldfish has no goldfish",
    )
    assert await reports_of(result, store) == []
    assert author.calls == 1
    assert "contest" not in author.tools[0] and "escalate" not in author.tools[0]
    assert "Elephant Goldfish method" not in method.get_instructions()
    with pytest.raises(MethodologyDefinitionError, match="rounds must be a whole number"):
        ElephantGoldfish(goldfish=(), rounds=0)


async def test_no_goldfish_still_offers_the_method_skills() -> None:
    author = Author(DRAFT)
    method = ElephantGoldfish(goldfish=())
    agent = Agent(FunctionModel(author), name="designer", capabilities=[method])

    await agent.run(MISSION)

    assert catalogue(author.instructions[0]) == list(AUTHOR_SKILLS)
    assert method.get_instructions() == LOADS_ITS_SKILLS
    assert author.tools[0] == ["load_capability"]


def test_goldfish_from_skill_reads_name_stage_and_kinds(tmp_path: Path) -> None:
    folder = write_skill(
        tmp_path,
        "goldfish-readiness",
        "metadata:\n  kinds: missing_path order_conflict\n  standard: ready\n  max_findings: '5'\n",
        "Say what a builder would ask.\n\n## Standard\n\nNothing is left to ask.\n\n"
        "## Sources\n\nWritten for this test.",
    )

    fish = Goldfish.from_skill(folder)
    renamed = Goldfish.from_skill(
        folder / "SKILL.md", name="ready", stage="critique", kinds=["gap"], sees_request=True
    )

    assert (fish.name, fish.stage, fish.kinds, fish.max_findings) == (
        "readiness",
        "readiness",
        ("missing_path", "order_conflict"),
        5,
    )
    assert fish.job == "Say what a builder would ask."
    assert fish.standard == Standard("ready", "Nothing is left to ask.")
    assert not fish.sees_request
    assert (renamed.name, renamed.stage, renamed.kinds, renamed.sees_request) == (
        "ready",
        "critique",
        ("gap",),
        True,
    )


def test_goldfish_from_skill_refuses_a_skill_with_no_stage(tmp_path: Path) -> None:
    folder = write_skill(tmp_path, "goldfish-proofread", "", "Check the spelling.")

    with pytest.raises(MethodologyDefinitionError, match="'proofread' is not a stage; pass stage="):
        Goldfish.from_skill(folder)
    assert Goldfish.from_skill(folder, stage="critique").name == "proofread"
    with pytest.raises(MethodologyDefinitionError, match="cannot read"):
        Goldfish.from_skill(tmp_path / "goldfish-missing")


async def test_every_method_skill_parses_and_names_its_sources() -> None:
    texts = skill_texts()

    assert sorted(texts) == [
        "design-interview",
        "design-resolve",
        "goldfish-comprehension",
        "goldfish-critique",
    ]
    for name, text in texts.items():
        skill = parse_skill(text, f"{name}/SKILL.md")
        assert skill.name == name
        assert 0 < len(skill.description) <= 1024
        sources = skill.body.split("## Sources", 1)[1]
        assert "Google" in sources, name
    # The author's catalogue, as Pydantic AI Harness reads it, holds the two author skills.
    folder = ReadOnlyWorkspace(Workspace(LocalWorkspaceBackend(METHOD)))
    loaded = await Skills(".", include=AUTHOR_SKILLS, workspace=folder).load()
    assert [skill.name for skill in loaded.skills] == list(AUTHOR_SKILLS)
    assert loaded.skipped == ()


SOFTWARE_WORDS = ("code", "codebase", "software", "engineer", "repository", "developer")


def test_method_skills_never_mention_code() -> None:
    for name, text in skill_texts().items():
        found = [
            word for word in SOFTWARE_WORDS if re.search(rf"\b{word}s?\b", text, re.IGNORECASE)
        ]
        assert found == [], f"{name} mentions {found}"


def test_the_wheel_includes_the_method_skills() -> None:
    """The wheel ships the whole package folder, so the skills go with the code.

    Hatchling leaves out only files git ignores, and nothing ignores a skill. The CI
    job "distributions" lists every SKILL.md in the built wheels.
    """
    root = Path(__file__).resolve().parents[1]
    build = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    wheel = build["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert wheel == {"packages": ["src/pydantic_ai_factory"]}
    for name in skill_texts():
        relative = (METHOD / name / "SKILL.md").relative_to(root)
        assert relative.parts[:2] == ("src", "pydantic_ai_factory")
    ignored = (root / ".gitignore").read_text(encoding="utf-8").split()
    assert not [line for line in ignored if "skill" in line.lower() and line != "skills-lock.json"]
