"""The port tools: the stakeholder and delivery ports, served as two MCP tools.

The tools are called in-process, through the tool protocol's in-memory client, so these
tests need no network. A system that runs as its own program reaches the same ports this
way; the Claude Code baseline's own tests serve them over HTTP.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from pydantic_ai_factory.evals import EventLog, FactSheet, RunPorts, Stakeholder, scripted_picker
from pydantic_ai_factory.evals._port_tools import (
    ASK_DESCRIPTION,
    DELIVER_DESCRIPTION,
    DELIVERED,
    port_server,
)

pytestmark = pytest.mark.anyio

QUESTION = "How long are ticked tasks kept?"
ANSWER = "Ticked tasks are kept for 47 days."
DESIGN = f"# To-do page\n\nA page of tasks.\n\n{ANSWER}\n"

TODO = FactSheet.model_validate(
    {
        "case_name": "todo",
        "challenge_class": "critical-ambiguity",
        "canary": "canary-7f3a9c1e5b2d",
        "fallback_reply": "I don't have anything to add on that.",
        "approval_reply": "I can't sign anything off here.",
        "scored": False,
        "facts": [
            {
                "id": "tick-retention",
                "value": "47 days",
                "criticality": "critical",
                "obtainable": True,
                "held_by": "product owner",
                "statement": "Ticked tasks are kept for 47 days.",
                "direct_question": "How long are ticked tasks kept?",
                "keywords": ["ticked", "kept", "retention"],
            },
            {
                "id": "title-length",
                "value": "83 characters",
                "criticality": "low",
                "obtainable": True,
                "held_by": "product owner",
                "statement": "A task title can be up to 83 characters.",
                "direct_question": "How long can a task title be?",
                "keywords": ["title", "length", "characters"],
            },
        ],
    }
)


def smoke() -> tuple[FactSheet, None]:
    return TODO, None


def ports_for(sheet: FactSheet, folder: Path) -> tuple[RunPorts, EventLog]:
    log = EventLog(folder / "events.jsonl", run="claude-code/todo/1")
    return RunPorts(Stakeholder(sheet, scripted_picker(sheet), log), log), log


def tool_texts(result: Any) -> list[str]:
    return [getattr(part, "text", "") for part in result.content]


async def test_the_two_tools_describe_themselves_in_neutral_words_and_nothing_more(
    tmp_path: Path,
) -> None:
    sheet, _ = smoke()
    ports, _ = ports_for(sheet, tmp_path)

    async with Client(port_server(ports)) as client:
        listed = await client.list_tools()

    described = {tool.name: tool.description for tool in listed.tools}
    assert described == {"ask_stakeholder": ASK_DESCRIPTION, "deliver": DELIVER_DESCRIPTION}
    assert ASK_DESCRIPTION == (
        "Ask the stakeholder one to four questions. Returns one reply for each question, "
        "in the same order."
    )
    assert DELIVER_DESCRIPTION == (
        "Deliver your work, as text, with a status of delivered or blocked. "
        "Call it once, when you are done."
    )


async def test_a_suite_may_word_what_the_deliver_tool_says(tmp_path: Path) -> None:
    sheet, _ = smoke()
    ports, _ = ports_for(sheet, tmp_path)

    async with Client(port_server(ports, deliver_description="Deliver the plan.")) as client:
        listed = await client.list_tools()

    assert {t.name: t.description for t in listed.tools}["deliver"] == "Deliver the plan."


async def test_three_questions_in_one_tool_call_get_three_replies_in_order_under_one_call_number(
    tmp_path: Path,
) -> None:
    sheet, _ = smoke()
    ports, log = ports_for(sheet, tmp_path)
    questions = [QUESTION, "How long can a task title be?", "What colour is the page?"]

    async with Client(port_server(ports)) as client:
        result = await client.call_tool("ask_stakeholder", {"questions": questions})

    assert not result.is_error
    assert result.structured_content == {
        "result": [ANSWER, "A task title can be up to 83 characters.", sheet.fallback_reply]
    }
    pairs = [(e.kind, e.data["call"], e.data["question"]) for e in log.events]
    assert pairs == [
        ("question_asked", 1, questions[0]),
        ("reply_sent", 1, questions[0]),
        ("question_asked", 1, questions[1]),
        ("reply_sent", 1, questions[1]),
        ("question_asked", 1, questions[2]),
        ("reply_sent", 1, questions[2]),
    ]


@pytest.mark.parametrize("count", [0, 5], ids=["none", "five"])
async def test_a_call_of_five_questions_or_of_none_gets_a_tool_error_and_logs_nothing(
    count: int, tmp_path: Path
) -> None:
    sheet, _ = smoke()
    ports, log = ports_for(sheet, tmp_path)

    async with Client(port_server(ports)) as client:
        result = await client.call_tool("ask_stakeholder", {"questions": [QUESTION] * count})

    assert result.is_error
    [text] = tool_texts(result)
    assert text.endswith(f"a stakeholder call carries one to 4 questions, not {count}")
    assert log.events == ()


async def test_each_tool_logs_the_same_events_as_the_in_process_port_call(
    tmp_path: Path,
) -> None:
    sheet, _ = smoke()
    by_tool, tool_log = ports_for(sheet, tmp_path / "tool")
    in_process, port_log = ports_for(sheet, tmp_path / "port")

    async with Client(port_server(by_tool)) as client:
        asked = await client.call_tool("ask_stakeholder", {"questions": [QUESTION]})
        first = await client.call_tool("deliver", {"document": DESIGN, "status": "delivered"})
        again = await client.call_tool("deliver", {"document": "Later.", "status": "blocked"})
    await in_process.ask_stakeholder([QUESTION])
    await in_process.deliver(DESIGN, "delivered")
    await in_process.deliver("Later.", "blocked")

    def logged(log: EventLog) -> list[tuple[str, dict[str, Any]]]:
        return [(event.kind, event.data) for event in log.events]

    assert logged(tool_log) == logged(port_log)
    assert asked.structured_content == {"result": [ANSWER]}
    assert tool_texts(first) == [DELIVERED] and tool_texts(again) == [DELIVERED]
    assert by_tool.delivery == in_process.delivery


async def test_a_status_that_is_neither_delivered_nor_blocked_gets_a_tool_error(
    tmp_path: Path,
) -> None:
    sheet, _ = smoke()
    ports, log = ports_for(sheet, tmp_path)

    async with Client(port_server(ports)) as client:
        result = await client.call_tool("deliver", {"document": DESIGN, "status": "approved"})

    assert result.is_error
    assert log.events == () and ports.delivery is None
