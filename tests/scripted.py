"""Scripted models for the documentation samples.

The samples name a real model, as a reader would write them. The test suite never calls
one: while a sample runs, every agent built with :data:`SAMPLE_MODEL` gets a scripted
model instead, which gives the replies listed for that page and that agent's name. So
the samples run with no key and always print the same thing.

Goldfish are agents too. Elephant Goldfish builds each one with no model of its own,
named ``goldfish-<name>``, and runs it on the author's model. While a sample runs, an
agent built with no model whose name is in the page's script gets its own scripted model
instead, so each goldfish gives its own replies, as a dict of its findings.
"""

from __future__ import annotations

from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

SAMPLE_MODEL = "anthropic:claude-opus-5-5"

Reply = (
    str | dict[str, Any] | ModelResponse | Callable[[list[ModelMessage], AgentInfo], ModelResponse]
)
"""Text, a structured output's arguments, a whole response, or a function that builds one."""

Script = Mapping[str, Sequence[Reply]]
"""The replies each agent gives in turn, by the agent's name. The last one repeats."""

FALLBACK = "Done."


def scripted_model(replies: Sequence[Reply]) -> FunctionModel:
    """A model that gives ``replies`` in turn, then repeats the last one."""
    queue = list(replies) or [FALLBACK]

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, ModelResponse):
            return reply
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
        name = kwargs.get("name") or ""
        if model == SAMPLE_MODEL or (model is None and name in script):
            model = scripted_model(script.get(name, ()))
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
SCENARIO = "Given an empty list, when I press Enter on a blank task, then nothing is added."
PARAPHRASE = "A one-page to-do list: you type tasks in, and tick them off when they are done."
BLANK_TASK = {
    "severity": "Major",
    "issue": "It does not say what happens when you press Enter on a blank task.",
}


def call(tool: str, **args: Any) -> ModelResponse:
    """A response that calls ``tool`` with ``args``."""
    return ModelResponse(parts=[ToolCallPart(tool, args, f"{tool}-1")])


SCRIPTS: dict[str, Script] = {
    "docs/factories.md": {
        "designer": [DESIGN, DESIGN_WITH_BLANK],
        "builder": ["Built index.html."],
    },
    "docs/stations.md": {"builder": ["Built index.html."], "reviewer": [REVIEW]},
    "docs/agents.md": {"builder": ["Built index.html."], "reviewer": [REVIEW]},
    "docs/gates.md": {"designer": [DESIGN, DESIGN_WITH_BLANK]},
    "docs/approvals.md": {
        "designer": [DESIGN, DESIGN_WITH_BLANK],
        "builder": [call("publish_page", path="index.html"), "Published index.html."],
    },
    "docs/stores.md": {"designer": [DESIGN]},
    "docs/missions.md": {"designer": [DESIGN], "builder": ["Built index.html."]},
    "docs/elephant-goldfish.md": {
        "designer": [DESIGN, DESIGN_WITH_BLANK],
        "goldfish-newcomer": [{"paraphrase": PARAPHRASE, "findings": []}],
        "goldfish-access": [{"findings": []}],
        "goldfish-builder": [{"findings": [BLANK_TASK]}, {"findings": []}],
    },
    "docs/methodologies.md": {
        "goldfish-newcomer": [{"paraphrase": PARAPHRASE, "findings": []}],
        "goldfish-builder": [{"findings": [BLANK_TASK]}, {"findings": []}],
        "designer": [
            {"summary": DESIGN, "acceptance_criteria": ["Blank tasks are not added."]},
            {
                "summary": DESIGN_WITH_BLANK,
                "acceptance_criteria": [SCENARIO],
            },
        ],
    },
}
