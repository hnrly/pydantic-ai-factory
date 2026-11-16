"""AskPerson: a capability that lets an agent ask a person a question and wait for the answer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import CallDeferred
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.toolsets import FunctionToolset

TOOL_NAME = "ask_person"
ENOUGH = (
    "You have asked as many questions as you may. Go on with your best judgement, "
    "and say what you assumed."
)


@dataclass
class AskPerson(AbstractCapability[Any]):
    """Give an agent an ``ask_person`` tool, so it can ask a person a question.

    Each question ends the agent's run as a deferred tool call. Inside a station that
    makes the run wait, with ``run.waiting.kind`` of ``"tool-calls"``, until the person
    answers through Pydantic AI's ``DeferredToolResults``. ``max_questions`` caps how many
    questions one conversation may ask; after that the tool tells the agent to carry on.

    It is loaded eagerly, not with ``defer_loading``: an agent asks its questions before
    it starts work, so the tool must be visible from the first request.
    """

    max_questions: int = 3

    def __post_init__(self) -> None:
        if self.id is None:
            self.id = TOOL_NAME

    def get_toolset(self) -> FunctionToolset[Any] | None:
        if self.max_questions < 1:
            return None
        cap = self.max_questions
        toolset: FunctionToolset[Any] = FunctionToolset()

        @toolset.tool(name=TOOL_NAME)
        def ask_person(ctx: RunContext[Any], question: str) -> str:
            """Ask the person a question the mission leaves open. Ask one thing at a time."""
            asked = sum(
                1
                for message in ctx.messages
                for part in message.parts
                if isinstance(part, ToolCallPart) and part.tool_name == TOOL_NAME
            )
            if asked > cap:
                return ENOUGH
            raise CallDeferred({"question": question}, kind="question")

        return toolset
