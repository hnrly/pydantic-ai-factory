"""A scripted model, so the software factory can be tried with no model key.

It is not clever. The designer turns the mission into a design, and its first draft says
nothing about blank input. The critique goldfish objects to exactly that, so one run
shows a draft, a Major finding, a revision in the same conversation and a clean round.
A person's request for changes is written into the next draft as it was given.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryFeedbackPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

BLANK_RULE = "Blank input is ignored: pressing Enter on an empty field adds nothing."
BLANK_ISSUE = "The design does not say what happens on blank input."
PERSON = "A person asked for changes to your last output."

_CLAUSE = re.compile(r"\s+(?:where|that|which|with|so|for)\s+|[,.;:!?]", re.IGNORECASE)
_ARTICLE = re.compile(r"^(?:a|an|the)\s+", re.IGNORECASE)
_KIND = re.compile(r"\s+(?:web\s+page|web\s+app|page|app|application|site)$", re.IGNORECASE)


def _texts(messages: list[ModelMessage]) -> list[str]:
    """Every prompt, tool result and retry prompt the model was sent, in order."""
    return [
        str(part.content)
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart | ToolReturnPart | RetryFeedbackPart)
    ]


def _sentences(text: str) -> list[str]:
    flat = " ".join(text.split())
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", flat) if part.strip()]


def title_of(mission: str) -> str:
    """A short name for the software, from the first clause of the mission."""
    clause = _CLAUSE.split(" ".join(mission.split()), maxsplit=1)[0]
    name = _KIND.sub("", _ARTICLE.sub("", clause)).strip()
    words = name.split()[:6] or ["Design"]
    title = " ".join(words)
    return title[:1].upper() + title[1:]


def _design(mission: str, *, revised: bool, notes: list[str]) -> dict[str, Any]:
    """A design that passes the gate: every criterion proved, every change in a step."""
    objective = mission.split("\n\n", 1)[0]
    sentences = _sentences(objective) or ["Do what the mission asks."]
    behaviour = [*sentences, *([BLANK_RULE] if revised else []), *notes]
    criteria = [f"A person can check this works: {line}" for line in behaviour]
    return {
        "title": title_of(objective),
        "summary": sentences[0],
        "problem": "The mission asks for this, and nothing does it yet.",
        "goals": sentences,
        "non_goals": ["Anything the mission does not ask for."],
        "behaviour": behaviour,
        "acceptance_criteria": criteria,
        "architecture": "One page, index.html, that opens in a browser with nothing to install.",
        "decisions": [
            {
                "topic": "Scope",
                "choice": "Only what the mission asks for.",
                "reason": "The mission says what is wanted, and nothing more is needed.",
                "source": "request",
            }
        ],
        "alternatives": [
            {
                "title": "A server with a database",
                "pros": "Data could be shared between devices.",
                "cons": "Something to install and run.",
                "rejected_because": "The mission does not ask for it.",
                "rejected_by": "drafting",
            }
        ],
        "changes": [
            {
                "path": "index.html",
                "action": "create",
                "what": "The whole page.",
                "why": "It is all the mission needs.",
            }
        ],
        "steps": [
            {
                "title": "Write the page",
                "changes": ["index.html"],
                "depends_on": [],
                "done_when": "Every acceptance criterion holds in a browser.",
            }
        ],
        "verification": [
            {
                "kind": "manual",
                "check": "Open index.html in a browser and try each criterion",
                "proves": list(range(1, len(criteria) + 1)),
            }
        ],
    }


def _designer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    said = _texts(messages)
    mission = said[0] if said else ""
    revised = any("Goldfish review" in text for text in said[1:])
    notes = [
        line.split(": ", 1)[-1]
        for text in said
        if text.startswith(PERSON)
        for line in text.splitlines()[1:]
        if line.strip()
    ]
    design = _design(mission, revised=revised, notes=notes)
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, design)])


def _goldfish(messages: list[ModelMessage], info: AgentInfo, fields: set[str]) -> ModelResponse:
    work = (_texts(messages) or [""])[0]
    findings: list[dict[str, str]] = []
    instructions = info.instructions or ""
    if "Stage: critique" in instructions and "blank" not in work.lower():
        findings.append({"severity": "Major", "kind": "gap", "issue": BLANK_ISSUE})
    reply: dict[str, Any] = {"findings": findings}
    if "explain_back" in fields:
        reply["explain_back"] = {
            "problem": "What the mission asks for.",
            "who": "The person who wrote the mission.",
            "approach": "A design for what the mission asks, explained back in brief.",
        }
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, reply)])


def _reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    schema = info.output_tools[0].parameters_json_schema if info.output_tools else {}
    fields = set(schema.get("properties", {}))
    if "findings" in fields:
        return _goldfish(messages, info, fields)
    return _designer(messages, info)


def offline_model() -> FunctionModel:
    """Return the scripted model. It makes no network calls and needs no key."""
    return FunctionModel(_reply, model_name="offline")
