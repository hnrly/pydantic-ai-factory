"""Checklist coverage: how much of a case's hidden checklist a design takes up.

A pinned judge model reads one checklist item and the delivered document, and answers
present or absent, with a quote when present. Plain code keeps a present answer only when
its quote is an exact run of the document's text. The judge reads one item in each call,
so one item cannot colour another, and it never sees which arm wrote the document, the
events, the fact sheet or the other items.

Coverage sits beside the outcome. It never decides a run.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from evals.contract import DesignPackage, sha256_of
from evals.facts import ChecklistItem, FactSheet
from evals.version import JUDGE_MODEL, JUDGE_PROMPT

JUDGE_NAME = "checklist-judge"


class JudgeAnswer(BaseModel):
    """What the judge may return: a yes or no, and a quote. It cannot return prose."""

    present: bool
    quote: str | None = None


class ItemVerdict(BaseModel):
    """The verdict on one item, kept with the run so it can be regraded without the judge.

    ``note`` says why a present answer was not kept.
    """

    model_config = ConfigDict(frozen=True)

    item_id: str
    document_sha256: str
    present: bool
    quote: str | None
    note: str | None = None


class Coverage(BaseModel):
    """Every item's verdict, and the share found present.

    ``share`` is ``None`` when the case has no checklist, as the smoke case has not.
    """

    model_config = ConfigDict(frozen=True)

    verdicts: tuple[ItemVerdict, ...]
    share: float | None


def judge_agent() -> Agent[None, JudgeAnswer]:
    """The checklist judge, on the pinned judge model."""
    return Agent(
        JUDGE_MODEL,
        name=JUDGE_NAME,
        instructions=JUDGE_PROMPT,
        output_type=JudgeAnswer,
        defer_model_check=True,
    )


def judge_input(item: ChecklistItem, document: str) -> str:
    """The judge's input: one item and the document, each in tags with a random marker.

    The marker is fresh for each call and never appears in the document, so the document
    cannot close its own tag.
    """
    marker = secrets.token_hex(8)
    while marker in document or marker in item.item:
        marker = secrets.token_hex(8)
    return (
        f"<item-{marker}>\n{item.item}\n</item-{marker}>\n\n"
        f"<document-{marker}>\n{document}\n</document-{marker}>"
    )


async def judge_item(
    item: ChecklistItem, document: str, judge: Agent[None, JudgeAnswer]
) -> ItemVerdict:
    """Ask the judge about one item, and keep a present answer only when its quote checks."""
    result = await judge.run(judge_input(item, document))
    answer = result.output
    digest = sha256_of(document)
    if not answer.present:
        return ItemVerdict(item_id=item.id, document_sha256=digest, present=False, quote=None)
    quote = answer.quote or ""
    if not quote.strip() or quote not in document:
        return ItemVerdict(
            item_id=item.id,
            document_sha256=digest,
            present=False,
            quote=answer.quote,
            note="the judge answered present, but its quote is not in the document",
        )
    return ItemVerdict(item_id=item.id, document_sha256=digest, present=True, quote=quote)


async def coverage(
    package: DesignPackage | None,
    sheet: FactSheet,
    judge: Agent[None, JudgeAnswer],
    stored: Sequence[ItemVerdict] = (),
) -> Coverage | None:
    """Judge every item of the case's checklist against the delivered document.

    A run with no delivery has no coverage. A stored verdict for the same item and the
    same document is reused, so a regraded run calls no judge and comes out the same. A
    blocked design is judged like any other.
    """
    if package is None:
        return None
    kept = {v.item_id: v for v in stored if v.document_sha256 == package.document_sha256}
    verdicts: list[ItemVerdict] = []
    for item in sheet.checklist:
        verdict = kept.get(item.id)
        if verdict is None:
            verdict = await judge_item(item, package.document, judge)
        verdicts.append(verdict)
    share = sum(v.present for v in verdicts) / len(verdicts) if verdicts else None
    return Coverage(verdicts=tuple(verdicts), share=share)


_TAGGED = re.compile(r"<(item|document)-([0-9a-f]+)>\n(.*?)\n</\1-\2>", re.DOTALL)


def scripted_judge(phrases: Mapping[str, str]) -> Agent[None, JudgeAnswer]:
    """A judge on a scripted model, so the harness runs offline.

    ``phrases`` maps an item's text to its key phrase. The item is present, quoting the
    phrase, when the document contains it.
    """

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompt = _last_prompt(messages)
        parts = {kind: text for kind, _, text in _TAGGED.findall(prompt)}
        phrase = phrases.get(parts.get("item", ""))
        document = parts.get("document", "")
        if phrase and phrase in document:
            answer = {"present": True, "quote": phrase}
        else:
            answer = {"present": False, "quote": None}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, answer)])

    return Agent(
        FunctionModel(respond, model_name="scripted-judge"),
        name=JUDGE_NAME,
        instructions=JUDGE_PROMPT,
        output_type=JudgeAnswer,
    )


def _last_prompt(messages: Sequence[ModelMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    return part.content
    return ""
