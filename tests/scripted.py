"""Scripted models for the documentation samples.

The samples name a real model, as a reader would write them. The test suite never calls
one: while a sample runs, every agent built with :data:`SAMPLE_MODEL` gets a scripted
model instead, which gives the replies listed for that page and that agent's name. So
the samples run with no key and always print the same thing.
"""

from __future__ import annotations

from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

SAMPLE_MODEL = "anthropic:claude-opus-5-5"

Reply = str | dict[str, Any] | Callable[[list[ModelMessage], AgentInfo], ModelResponse]
"""Text, the arguments of a structured output, or a function that builds the response."""

Script = Mapping[str, Sequence[Reply]]
"""The replies each agent gives in turn, by the agent's name. The last one repeats."""

FALLBACK = "Done."


def scripted_model(replies: Sequence[Reply]) -> FunctionModel:
    """A model that gives ``replies`` in turn, then repeats the last one."""
    queue = list(replies) or [FALLBACK]

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if callable(reply):
            return reply(messages, info)
        if isinstance(reply, dict) or not info.allow_text_output:
            args = reply if isinstance(reply, dict) else {"response": reply}
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])
        return ModelResponse(parts=[TextPart(reply)])

    return FunctionModel(respond, model_name="scripted")


@contextmanager
def sample_models(script: Script) -> Generator[None]:
    """While open, an agent built with :data:`SAMPLE_MODEL` gets a scripted model."""
    original = Agent.__init__

    def init(self: Agent[Any, Any], model: Any = None, *args: Any, **kwargs: Any) -> None:
        if model == SAMPLE_MODEL:
            model = scripted_model(script.get(kwargs.get("name") or "", ()))
        original(self, model, *args, **kwargs)

    Agent.__init__ = init  # pyright: ignore[reportAttributeAccessIssue]
    try:
        yield
    finally:
        Agent.__init__ = original  # pyright: ignore[reportAttributeAccessIssue]


DESIGN = (
    "One page, index.html. Type a task and press Enter to add it; tick its box to mark it done."
)
DESIGN_WITH_BLANK = DESIGN + " Pressing Enter on a blank task adds nothing."
REVIEW = "The page matches the design. Nothing is wrong."

SCRIPTS: dict[str, Script] = {
    "docs/factories.md": {
        "designer": [DESIGN, DESIGN_WITH_BLANK],
        "builder": ["Built index.html."],
    },
    "docs/stations.md": {"builder": ["Built index.html."], "reviewer": [REVIEW]},
    "docs/agents.md": {"builder": ["Built index.html."], "reviewer": [REVIEW]},
    "docs/gates.md": {"designer": [DESIGN, DESIGN_WITH_BLANK]},
}
