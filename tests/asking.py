"""Questions as an agent asks them with ``AskPerson``: one ``ask_user_question`` call."""

from __future__ import annotations

from typing import Any

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai_harness.ask_user import TOOL_NAME

ASK = TOOL_NAME
"""The tool ``AskPerson`` gives an agent, from Pydantic AI Harness's ``AskUser``."""

YES, NO = "Yes (Recommended)", "No"


def asked(
    question: str,
    header: str = "Q1",
    options: tuple[str, ...] = (YES, NO),
    *,
    multi_select: bool = False,
) -> dict[str, Any]:
    """One question, with ``options`` as its labels, the recommended one first."""
    return {
        "header": header,
        "question": question,
        "options": [{"label": label} for label in options],
        "multi_select": multi_select,
    }


def ask_call(*questions: str | dict[str, Any], call_id: str = "q1") -> ToolCallPart:
    """One ``ask_user_question`` call. A plain string becomes a yes-or-no question."""
    built = [
        asked(question, f"Q{number}") if isinstance(question, str) else question
        for number, question in enumerate(questions, start=1)
    ]
    return ToolCallPart(ASK, {"questions": built}, call_id)


def ask(*questions: str | dict[str, Any], call_id: str = "q1") -> ModelResponse:
    """A model response that asks ``questions`` in one call."""
    return ModelResponse(parts=[ask_call(*questions, call_id=call_id)])
