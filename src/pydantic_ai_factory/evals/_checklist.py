"""Checklist coverage: how much of a case's hidden checklist a delivery takes up.

The judge is Pydantic Evals' own: :func:`pydantic_evals.evaluators.llm_as_a_judge.judge_output`
reads one checklist item, as its rubric, and the delivered text, and answers pass or
fail with a reason. The rubric asks it to put, in that reason, the shortest exact passage
of the text that shows the item. Plain code keeps a pass only when that quote is an exact
run of the text. The judge reads one item in each call, so one item cannot colour
another, and it never sees which arm wrote the text, the events, the fact sheet or the
other items.

Each verdict is kept with the hash of the text it was given, so a run graded again reuses
it and calls no judge. Coverage sits beside the outcome. It never decides a run.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.settings import ModelSettings
from pydantic_evals.evaluators.llm_as_a_judge import judge_output

from ._contract import Delivery, sha256_of
from ._facts import ChecklistItem, FactSheet

QUOTE_RULE = (
    "When the statement is true, the reason is the shortest exact passage of the output "
    "that shows it, in double quotes, and nothing else."
)
"""What every rubric adds, so a pass carries a quote plain code can check."""

_QUOTED = re.compile(r"[\"“](.+?)[\"”]", re.DOTALL)


class ItemVerdict(BaseModel):
    """The verdict on one item, kept with the run so it can be regraded without the judge.

    ``note`` says why a pass was not kept.
    """

    model_config = ConfigDict(frozen=True)

    item_id: str
    sha256: str
    present: bool
    quote: str | None
    note: str | None = None


class Coverage(BaseModel):
    """Every item's verdict, and the share found present.

    ``share`` is ``None`` when the case has no checklist.
    """

    model_config = ConfigDict(frozen=True)

    verdicts: tuple[ItemVerdict, ...]
    share: float | None


@dataclass(frozen=True)
class ChecklistJudge:
    """Pydantic Evals' judge on a pinned model, with a suite's own instructions.

    ``instructions`` say what a present item looks like for this suite. Each call adds
    the item and :data:`QUOTE_RULE`, and asks :func:`judge_output` about the text.
    """

    model: Model | str
    instructions: str
    model_settings: ModelSettings | None = None

    def rubric(self, item: ChecklistItem) -> str:
        """The rubric for one item: the suite's instructions, the item and the quote rule."""
        return (
            f"{self.instructions.strip()}\n\nThe output commits to this: {item.item}\n\n"
            f"{QUOTE_RULE}"
        )

    async def judge(self, item: ChecklistItem, text: str) -> ItemVerdict:
        """Ask the judge about one item, and keep a pass only when its quote checks."""
        grading = await judge_output(
            text, self.rubric(item), model=self.model, model_settings=self.model_settings
        )
        digest = sha256_of(text)
        if not grading.pass_:
            return ItemVerdict(item_id=item.id, sha256=digest, present=False, quote=None)
        quote = quote_in(grading.reason)
        if quote is None or not quote.strip() or quote not in text:
            return ItemVerdict(
                item_id=item.id,
                sha256=digest,
                present=False,
                quote=quote,
                note="the judge passed it, but its quote is not in the text",
            )
        return ItemVerdict(item_id=item.id, sha256=digest, present=True, quote=quote)


def quote_in(reason: str) -> str | None:
    """The first passage in double quotes in a judge's reason, or the reason when none is."""
    found = _QUOTED.search(reason)
    return found.group(1) if found else reason or None


async def coverage(
    delivery: Delivery | None,
    sheet: FactSheet,
    judge: ChecklistJudge,
    stored: Sequence[ItemVerdict] = (),
) -> Coverage | None:
    """Judge every item of the case's checklist against the delivered text.

    A run with no delivery has no coverage. A stored verdict for the same item and the
    same text is reused, so a regraded run calls no judge and comes out the same. A
    blocked delivery is judged like any other.
    """
    if delivery is None:
        return None
    kept = {v.item_id: v for v in stored if v.sha256 == delivery.sha256}
    verdicts: list[ItemVerdict] = []
    for item in sheet.checklist:
        verdict = kept.get(item.id)
        if verdict is None:
            verdict = await judge.judge(item, delivery.text)
        verdicts.append(verdict)
    share = sum(v.present for v in verdicts) / len(verdicts) if verdicts else None
    return Coverage(verdicts=tuple(verdicts), share=share)


_SECTION = re.compile(r"<(Output|Rubric)>\n(.*?)\n</\1>", re.DOTALL)
_ITEM = re.compile(r"The output commits to this: (.*?)\n\n", re.DOTALL)


def scripted_judge(phrases: Mapping[str, str], *, instructions: str = "") -> ChecklistJudge:
    """A judge on a scripted model, so the harness runs offline.

    ``phrases`` maps an item's text to its key phrase. The item passes, quoting the
    phrase, when the text contains it.
    """

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompt = _last_prompt(messages)
        sections = dict(_SECTION.findall(prompt))
        item = _ITEM.search(sections.get("Rubric", ""))
        phrase = phrases.get(item.group(1)) if item else None
        text = sections.get("Output", "")
        if phrase and phrase in text:
            answer = {"reason": f'"{phrase}"', "pass": True, "score": 1.0}
        else:
            answer = {"reason": "absent", "pass": False, "score": 0.0}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, answer)])

    return ChecklistJudge(
        FunctionModel(respond, model_name="scripted-judge"),
        instructions or "Decide whether the output takes one direction.",
    )


def _last_prompt(messages: Sequence[ModelMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    return part.content
    return ""
