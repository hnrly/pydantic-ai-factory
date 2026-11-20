"""AskPerson: a capability that lets an agent ask a person a round of questions and wait."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_ai import RunContext, ToolReturn
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import CallDeferred
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.toolsets import FunctionToolset

from ..contracts import DefinitionError

TOOL_NAME = "ask_person"
ENOUGH = (
    "You have asked as many questions as you may. Go on with your best judgement, "
    "and say what you assumed."
)
MAX_OPTIONS = 3
_REFUSED = "refused"
"""Set in a refused call's return metadata, so the refused round is never counted."""


class Question(BaseModel):
    """One question an agent asks a person, with the choices it sees, best first."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1, description="The question. Ask one thing only.")
    why_it_matters: str = Field(
        min_length=1, description="What the work does differently with each answer."
    )
    options: list[str] | None = Field(
        default=None,
        max_length=MAX_OPTIONS,
        description="Up to three choices, the recommended one first. None for an open question.",
    )
    recommended: int | None = Field(
        default=None,
        description="The index of the recommended option. It must be 0: put it first.",
    )

    @model_validator(mode="after")
    def _recommended_comes_first(self) -> Question:
        if self.recommended is None:
            return self
        if not self.options:
            raise ValueError("recommended needs options to recommend one of")
        if self.recommended != 0:
            raise ValueError("put the recommended option first, and give recommended as 0")
        return self


def render_round(questions: Sequence[Question]) -> str:
    """A round as numbered text, with each question's options and the recommended one marked."""
    lines: list[str] = []
    for number, question in enumerate(questions, start=1):
        lines.append(f"{number}. {question.text}")
        lines.append(f"   Why it matters: {question.why_it_matters}")
        for index, option in enumerate(question.options or ()):
            mark = " (recommended)" if index == question.recommended else ""
            lines.append(f"   {chr(ord('a') + index)}) {option}{mark}")
    return "\n".join(lines)


def _asked(part: ToolCallPart) -> int:
    """How many questions one ``ask_person`` call asks, read from its raw arguments."""
    try:
        questions = part.args_as_dict().get("questions")
    except ValueError:
        return 0
    return len(questions) if isinstance(questions, list) else 0  # pyright: ignore[reportUnknownArgumentType]


def _rounds(messages: Sequence[ModelMessage], call_id: str | None) -> tuple[int, int]:
    """The rounds asked before this call's response, and the questions in this response.

    A round is one model response with an ``ask_person`` call that was answered: one that
    waited for the person, or was told to carry on. A refused call never counts. Calls in
    the same response are one round, so they never count against each other.
    """
    responses = [m for m in messages if isinstance(m, ModelResponse)]
    current = next(
        (r for r in reversed(responses) if any(c.tool_call_id == call_id for c in r.tool_calls)),
        responses[-1] if responses else None,
    )
    answered = {
        part.tool_call_id
        for message in messages
        for part in message.parts
        if isinstance(part, ToolReturnPart)
        and part.tool_name == TOOL_NAME
        and not (isinstance(part.metadata, dict) and part.metadata.get(_REFUSED))  # pyright: ignore[reportUnknownMemberType]
    }
    earlier = 0
    for response in responses:
        if response is current:
            break
        if any(
            c.tool_name == TOOL_NAME and c.tool_call_id in answered for c in response.tool_calls
        ):
            earlier += 1
    calls = [] if current is None else current.tool_calls
    return earlier, sum(_asked(c) for c in calls if c.tool_name == TOOL_NAME)


@dataclass
class AskPerson(AbstractCapability[Any]):
    """Give an agent an ``ask_person`` tool, so it can ask a person a round of questions.

    A round is one model response that calls ``ask_person``: up to ``max_per_round``
    questions, each with up to three options and the recommended one first. The round
    ends the agent's run as deferred tool calls. Inside a station that makes the run wait
    once, with ``run.waiting.kind`` of ``"tool-calls"``, until the person answers through
    Pydantic AI's ``DeferredToolResults``. Each call's metadata carries ``questions``,
    the list as dictionaries, and ``question``, the round as numbered text.

    A round with more than ``max_per_round`` questions, in one call or across the calls
    in one response, is refused with a message, and the agent asks again; a refused round
    does not count.
    After ``max_rounds`` rounds the tool tells the agent to carry on with its best
    judgement. ``max_rounds=0`` takes the tool away.

    It is loaded eagerly, not with ``defer_loading``: an agent asks its questions before
    it starts work, so the tool must be visible from the first request.
    """

    max_rounds: int = 3
    max_per_round: int = 4

    def __post_init__(self) -> None:
        problems = [
            f"{label} must be a whole number, {least} or more"
            for label, value, least in (
                ("max_rounds", self.max_rounds, 0),
                ("max_per_round", self.max_per_round, 1),
            )
            if isinstance(value, bool) or not isinstance(value, int) or value < least  # pyright: ignore[reportUnnecessaryIsInstance]
        ]
        if problems:
            raise DefinitionError(f"capability {TOOL_NAME!r}", problems)
        if self.id is None:
            self.id = TOOL_NAME

    def get_toolset(self) -> FunctionToolset[Any] | None:
        if self.max_rounds < 1:
            return None
        rounds, per_round = self.max_rounds, self.max_per_round
        toolset: FunctionToolset[Any] = FunctionToolset()

        @toolset.tool(
            name=TOOL_NAME,
            description=(
                "Ask the person what the mission leaves open and the work depends on. Ask "
                f"every question you have in one call: a round of up to {per_round} "
                "questions. Give each one why it matters and up to three options, the one "
                "you recommend first. You may ask "
                f"{rounds} round{'s' if rounds != 1 else ''} in all."
            ),
        )
        def ask_person(ctx: RunContext[Any], questions: list[Question]) -> str | ToolReturn:
            earlier, asked = _rounds(ctx.messages, ctx.tool_call_id)
            if earlier >= rounds:
                return ENOUGH
            if not questions or asked > per_round:
                # Refused, not retried: each call in the response is refused, and a retry
                # each would spend the tool's retries on one mistake.
                return ToolReturn(
                    f"Not asked. A round holds 1 to {per_round} questions, and this one "
                    f"asked {asked}. Ask again, in one call, with the {per_round} that "
                    "matter most.",
                    metadata={_REFUSED: True},
                )
            raise CallDeferred(
                {
                    "questions": [question.model_dump() for question in questions],
                    "question": render_round(questions),
                },
                kind="question",
            )

        return toolset
