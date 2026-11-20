"""The checklist judge keeps a present answer only with a quote found in the document.

The judge runs on a scripted model or on one that gives fixed answers. No real model is
called.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from evals.checklist import (
    JUDGE_NAME,
    JudgeAnswer,
    coverage,
    judge_agent,
    judge_item,
    scripted_judge,
)
from evals.contract import DesignPackage
from evals.facts import ChecklistItem, FactSheet
from evals.version import JUDGE_MODEL, JUDGE_PROMPT

pytestmark = pytest.mark.anyio

HOLDS = ChecklistItem(id="holds", item="Reuses the existing holds table.")
MIGRATION = ChecklistItem(id="migration", item="Adds a database migration.")
CLASH = ChecklistItem(id="clash", item="Says what happens when two people choose one seat.")

SHEET = FactSheet.model_validate(
    {
        "case_name": "seats",
        "challenge_class": "critical-ambiguity",
        "facts": [
            {
                "id": "hold",
                "value": "47 minutes",
                "criticality": "critical",
                "obtainable": True,
                "held_by": "product owner",
                "statement": "Seats are held for 47 minutes.",
                "direct_question": "How long is a seat held?",
            }
        ],
        "canary": "CANARY-7f3a9c",
        "fallback_reply": "I don't have anything on that.",
        "approval_reply": "I can't sign anything off here.",
        "checklist": [HOLDS.model_dump(), MIGRATION.model_dump(), CLASH.model_dump()],
    }
)

DOCUMENT = (
    "# Seat selection\n\n"
    "We store a chosen seat in the existing holds table, held for 47 minutes.\n"
    "A new migration adds a seat column to bookings.\n"
)
PHRASES = {
    HOLDS.item: "the existing holds table",
    MIGRATION.item: "A new migration adds a seat column",
    CLASH.item: "the second person is told the seat is gone",
}


def judge_on(model: FunctionModel) -> Agent[None, JudgeAnswer]:
    """A judge with the checklist judge's name, prompt and output, on ``model``."""
    return Agent(model, name=JUDGE_NAME, instructions=JUDGE_PROMPT, output_type=JudgeAnswer)


def fixed(*answers: dict[str, Any], prompts: list[str] | None = None) -> Agent[None, JudgeAnswer]:
    """A judge that gives ``answers`` in turn, and notes each input in ``prompts``."""
    queue = list(answers)

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if prompts is not None:
            prompts.extend(
                str(part.content)
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, UserPromptPart)
            )
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, queue.pop(0))])

    return judge_on(FunctionModel(respond))


def never_called() -> Agent[None, JudgeAnswer]:
    """A judge that fails the test if it is asked anything."""

    def refuse(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise AssertionError("the judge was called")

    return judge_on(FunctionModel(refuse))


def test_the_judge_is_built_on_the_pinned_model_with_the_confirmed_prompt() -> None:
    agent = judge_agent()
    assert agent.name == "checklist-judge"
    assert JUDGE_MODEL == "anthropic:claude-sonnet-5-5"
    assert JUDGE_PROMPT.startswith("You decide whether a design document takes one direction.")


async def test_the_pinned_judge_answers_on_an_overridden_model() -> None:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        answer = {"present": True, "quote": "the existing holds table"}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, answer)])

    agent = judge_agent()
    with agent.override(model=FunctionModel(respond)):
        verdict = await judge_item(HOLDS, DOCUMENT, agent)
    assert verdict.present
    assert verdict.quote == "the existing holds table"


async def test_an_item_with_a_quote_found_in_the_document_is_present() -> None:
    verdict = await judge_item(HOLDS, DOCUMENT, scripted_judge(PHRASES))
    assert verdict.present
    assert verdict.quote == "the existing holds table"
    assert verdict.note is None


async def test_a_present_answer_whose_quote_is_not_in_the_document_is_absent() -> None:
    judge = fixed({"present": True, "quote": "the holds table is reused"})
    verdict = await judge_item(HOLDS, DOCUMENT, judge)
    assert not verdict.present
    assert verdict.note == "the judge answered present, but its quote is not in the document"


async def test_a_present_answer_with_an_empty_quote_is_absent() -> None:
    verdict = await judge_item(HOLDS, DOCUMENT, fixed({"present": True, "quote": " "}))
    assert not verdict.present


async def test_the_judges_input_holds_one_item_and_the_document_and_never_the_arm() -> None:
    prompts: list[str] = []
    judge = fixed({"present": False}, prompts=prompts)
    package = DesignPackage.of(DOCUMENT, "delivered")
    one_item = SHEET.model_copy(update={"checklist": (HOLDS,)})

    await coverage(package, one_item, judge)

    assert len(prompts) == 1
    prompt = prompts[0]
    assert HOLDS.item in prompt
    assert DOCUMENT in prompt
    for hidden in ("factory", MIGRATION.item, CLASH.item, SHEET.canary, "product owner"):
        assert hidden not in prompt


async def test_each_item_is_sent_to_the_judge_in_a_call_of_its_own() -> None:
    prompts: list[str] = []
    judge = fixed(*[{"present": False}] * 3, prompts=prompts)
    await coverage(DesignPackage.of(DOCUMENT, "delivered"), SHEET, judge)

    assert len(prompts) == 3
    for prompt, item in zip(prompts, SHEET.checklist, strict=True):
        assert [i.item for i in SHEET.checklist if i.item in prompt] == [item.item]


async def test_coverage_is_the_share_of_items_found_present() -> None:
    result = await coverage(DesignPackage.of(DOCUMENT, "delivered"), SHEET, scripted_judge(PHRASES))

    assert result is not None
    assert [v.present for v in result.verdicts] == [True, True, False]
    assert result.share == pytest.approx(2 / 3)


async def test_a_blocked_design_is_judged_the_same_way() -> None:
    judge = scripted_judge(PHRASES)
    delivered = await coverage(DesignPackage.of(DOCUMENT, "delivered"), SHEET, judge)
    blocked = await coverage(DesignPackage.of(DOCUMENT, "blocked"), SHEET, judge)
    assert delivered == blocked


async def test_a_run_with_no_delivery_has_no_coverage() -> None:
    assert await coverage(None, SHEET, never_called()) is None


async def test_a_stored_verdict_is_reused_on_regrading() -> None:
    package = DesignPackage.of(DOCUMENT, "delivered")
    first = await coverage(package, SHEET, scripted_judge(PHRASES))
    assert first is not None

    again = await coverage(package, SHEET, never_called(), stored=first.verdicts)
    assert again == first


async def test_a_stored_verdict_for_another_document_is_not_reused() -> None:
    first = await coverage(DesignPackage.of(DOCUMENT, "delivered"), SHEET, scripted_judge(PHRASES))
    assert first is not None

    other = DesignPackage.of("A page with no seats.", "delivered")
    again = await coverage(other, SHEET, scripted_judge(PHRASES), stored=first.verdicts)
    assert again is not None
    assert again.share == 0
