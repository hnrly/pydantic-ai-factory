"""The scripted designer: a scripted model the two factory arms run on offline.

It is not a system under test. It does what the core's correct scripted system does,
through the factory's own tools: it reads the files that hold the case's repo facts, asks
the direct question of every critical stakeholder fact in one round, and returns a design
that states every reply. Like the scripted systems, it is built from the case's fact sheet.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

from pydantic_ai_factory.evals import MAX_QUESTIONS_PER_CALL, Fact, FactSheet, values_in

DESIGNER_MODEL_NAME = "scripted-designer"
"""The name the scripted designer reports. No price list knows it, so its cost is unknown."""

READ_TOOL = "read_file"
ASK_TOOL = "ask_user_question"

Respond = Callable[[list[ModelMessage], AgentInfo], ModelResponse]


def scripted_designer(sheet: FactSheet, *, overlay: Path | None = None) -> FunctionModel:
    """A scripted model for the two factory arms offline, built from the case's fact sheet.

    It answers by the output schema of each request:

    * a goldfish's request, whose schema has a ``findings`` field, gets no findings, and an
      explanation back when the schema asks for one. Two such rounds end the review;
    * the designer's request first reads, with ``read_file``, the file of ``overlay``
      that holds each repo fact, once. Then it asks the direct question of every critical
      stakeholder fact with one ``ask_user_question`` call, as one round. Then it returns a
      design whose behaviour lists every reply word for word, the statement of each repo
      fact whose value the read found, and every checklist item. When a reply states a
      fact nobody knows, the design lists that fact's direct question as an open question,
      on every draft, so the run ends when rework runs out.

    It answers streamed requests too, as a designer grounded in a workspace makes them. It
    imports nothing from the factory.
    """
    return function_model(designer_replies(sheet, overlay=overlay), name=DESIGNER_MODEL_NAME)


def designer_replies(sheet: FactSheet, *, overlay: Path | None = None) -> Respond:
    """The function behind :func:`scripted_designer`, for a test to build on."""
    reads = _overlay_files(sheet, overlay)
    taken = set(reads.values())
    page = "index.html" if "index.html" not in taken else f"{sheet.case_name}.html"

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        schema = info.output_tools[0].parameters_json_schema if info.output_tools else {}
        fields = set(schema.get("properties", {}))
        if "findings" in fields:
            return _no_findings(info, fields)
        return _designer_turn(sheet, reads, page, messages, info)

    return respond


def function_model(respond: Respond, *, name: str) -> FunctionModel:
    """A ``FunctionModel`` that gives ``respond``'s replies, streamed or not, as ``name``."""
    return FunctionModel(respond, stream_function=_streamed(respond), model_name=name)


def _overlay_files(sheet: FactSheet, overlay: Path | None) -> dict[str, str]:
    """The overlay file that holds each repo fact's value, by fact id, as a relative path."""
    found: dict[str, str] = {}
    if overlay is None:
        return found
    repo = [fact for fact in sheet.facts if fact.source == "environment"]
    for file in sorted(overlay.rglob("*")):
        if not file.is_file():
            continue
        held = values_in(file.read_text(encoding="utf-8", errors="replace"), sheet)
        for fact in repo:
            if fact.id in held and fact.id not in found:
                found[fact.id] = PurePosixPath(*file.relative_to(overlay).parts).as_posix()
    return found


def _no_findings(info: AgentInfo, fields: set[str]) -> ModelResponse:
    reply: dict[str, Any] = {"findings": []}
    if "explain_back" in fields:
        reply["explain_back"] = {
            "problem": "What the request asks for.",
            "who": "The person who made the request.",
            "approach": "A design for what the request asks, explained back in one line.",
        }
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, reply)])


def _designer_turn(
    sheet: FactSheet,
    reads: dict[str, str],
    page: str,
    messages: list[ModelMessage],
    info: AgentInfo,
) -> ModelResponse:
    returns = [
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    read = {p.tool_call_id: str(p.content) for p in returns if p.tool_name == READ_TOOL}
    if reads and not read:
        calls = [
            ToolCallPart(READ_TOOL, {"path": path}, f"read-{fact_id}")
            for fact_id, path in reads.items()
        ]
        return ModelResponse(parts=calls)
    critical = [f for f in sheet.facts if f.criticality == "critical" and f.source == "stakeholder"]
    answered = [p for p in returns if p.tool_name == ASK_TOOL]
    if critical and not answered:
        questions = [
            {
                "header": f"Q{number}",
                "question": fact.direct_question,
                "options": [{"label": "Tell me (Recommended)"}, {"label": "Leave it open"}],
                "multi_select": False,
            }
            for number, fact in enumerate(critical[:MAX_QUESTIONS_PER_CALL], start=1)
        ]
        return ModelResponse(parts=[ToolCallPart(ASK_TOOL, {"questions": questions}, "ask-1")])
    replies = [str(reply) for part in answered for reply in _listed(part.content)]
    found = [
        fact.statement
        for fact in sheet.facts
        if fact.id in reads and fact.id in values_in(read.get(f"read-{fact.id}", ""), sheet)
    ]
    unknown = [
        fact.direct_question
        for fact in sheet.facts
        if not fact.obtainable and any(_says(fact, reply) for reply in replies)
    ]
    design = _design(_mission(messages), [*replies, *found], sheet, page, unknown)
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, design)])


def _listed(content: Any) -> list[Any]:
    """The replies in a tool return: a list, or ``ask_user_result``'s ``{header: [reply]}``."""
    if isinstance(content, dict):
        return [reply for value in content.values() for reply in _listed(value)]  # pyright: ignore[reportUnknownVariableType]
    return list(content) if isinstance(content, list | tuple) else [content]  # pyright: ignore[reportUnknownArgumentType]


def _mission(messages: list[ModelMessage]) -> str:
    """The first prompt the designer was sent: the mission."""
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    return part.content
    return ""


def _design(
    mission: str, said: list[str], sheet: FactSheet, page: str, unknown: list[str]
) -> dict[str, Any]:
    """A design that passes the factory's gate unless it lists an open question."""
    behaviour = [
        " ".join(mission.split()) or "Do what the request asks.",
        *said,
        *(item.item for item in sheet.checklist),
    ]
    criteria = [f"A person can check this works: {line}" for line in behaviour]
    return {
        "title": "Scripted design",
        "summary": behaviour[0],
        "problem": "The request asks for this, and nothing does it yet.",
        "goals": [behaviour[0]],
        "non_goals": ["Anything the request does not ask for."],
        "open_questions": unknown,
        "behaviour": behaviour,
        "acceptance_criteria": criteria,
        "architecture": f"One page, {page}, that opens in a browser with nothing to install.",
        "decisions": [
            {
                "topic": "Scope",
                "choice": "Only what the request asks for.",
                "reason": "The request says what is wanted, and nothing more is needed.",
                "source": "request",
            }
        ],
        "alternatives": [
            {
                "title": "A server with a database",
                "pros": "Data could be shared between devices.",
                "cons": "Something to install and run.",
                "rejected_because": "The request does not ask for it.",
                "rejected_by": "drafting",
            }
        ],
        "changes": [
            {"path": page, "action": "create", "what": "The whole page.", "why": "It is all."}
        ],
        "steps": [
            {
                "title": "Write the page",
                "changes": [page],
                "depends_on": [],
                "done_when": "Every acceptance criterion holds in a browser.",
            }
        ],
        "verification": [
            {
                "kind": "manual",
                "check": f"Open {page} in a browser and try each criterion",
                "proves": list(range(1, len(criteria) + 1)),
            }
        ],
    }


def _streamed(
    respond: Respond,
) -> Callable[[list[ModelMessage], AgentInfo], AsyncIterator[str | DeltaToolCalls]]:
    """The same replies, streamed in one piece each."""

    async def stream(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        for index, part in enumerate(respond(messages, info).parts):
            if isinstance(part, TextPart):
                yield part.content
            elif isinstance(part, ToolCallPart):
                yield {
                    index: DeltaToolCall(
                        name=part.tool_name,
                        json_args=part.args_as_json_str(),
                        tool_call_id=part.tool_call_id,
                    )
                }

    return stream


def _says(fact: Fact, reply: str) -> bool:
    return fact.statement in reply
