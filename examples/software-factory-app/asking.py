"""The asking model: a scripted model whose designer asks the person one question first.

The software factory's own scripted model never asks, because its missions say enough.
This one lets a person see an agent's question with no model key. As the designer, it
calls the ``ask_person`` tool once with a fixed question. Every other request, and every
request after the answer, goes to the software package's scripted model.
"""

from __future__ import annotations

import inspect

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai_factory_software import offline_model

ASK = "ask_person"
QUESTION = "Should ticked tasks stay on the list after the page is reloaded?"


def _asked(messages: list[ModelMessage]) -> bool:
    return any(
        isinstance(part, ToolReturnPart) and part.tool_name == ASK
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
    )


def asking_model() -> FunctionModel:
    """Return the asking model. It makes no network calls and needs no key."""
    scripted = offline_model().function
    if scripted is None:  # the software package always gives its model a function
        raise RuntimeError("the scripted model has no function to hand over to")

    async def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        can_ask = any(tool.name == ASK for tool in info.function_tools)
        if can_ask and not _asked(messages):
            return ModelResponse(parts=[ToolCallPart(ASK, {"question": QUESTION}, "ask-1")])
        response = scripted(messages, info)
        return await response if inspect.isawaitable(response) else response

    return FunctionModel(reply, model_name="asking")
