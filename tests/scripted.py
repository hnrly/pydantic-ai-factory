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

from collections.abc import AsyncIterator, Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

SAMPLE_MODEL = "anthropic:claude-opus-5-5"

Reply = (
    str | dict[str, Any] | ModelResponse | Callable[[list[ModelMessage], AgentInfo], ModelResponse]
)
"""Text, a structured output's arguments, a whole response, or a function that builds one."""

Script = Mapping[str, Sequence[Reply]]
"""The replies each agent gives in turn, by the agent's name. The last one repeats."""

FALLBACK = "Done."


def scripted_model(replies: Sequence[Reply]) -> FunctionModel:
    """A model that gives ``replies`` in turn, then repeats the last one.

    It answers streamed requests too, with each reply in one piece, because some
    capabilities, such as Pydantic AI Harness's ``Coder``, stream every run.
    """
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
            else:
                raise TypeError(f"a scripted reply cannot stream a {type(part).__name__}")

    return FunctionModel(respond, stream_function=stream, model_name="scripted")


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


def _said(messages: list[ModelMessage]) -> str:
    """Everything the model was sent so far: prompts, tool results and retry prompts."""
    return "\n".join(
        str(getattr(part, "content", ""))
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
    )


def _answered(messages: list[ModelMessage], tool: str) -> bool:
    return any(
        isinstance(part, ToolReturnPart) and part.tool_name == tool
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
    )


def _output(info: AgentInfo, args: dict[str, Any]) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])


PAGE = '<h1>To-do list</h1>\n<input placeholder="Add a task">\n<ul></ul>\n'
"""The page the getting-started builder writes. Short, so the printed diff stays short."""

TODO = {
    "title": "To-do list",
    "goal": "Keep track of tasks in a browser.",
    "behaviour": [
        "Type a task and press Enter to add it.",
        "Tick a task's box to mark it done.",
    ],
    "acceptance_criteria": [
        "A task typed and entered appears in the list.",
        "Ticked tasks stay ticked after a reload.",
    ],
    "exclusions": ["Accounts", "Sharing between devices"],
}
BLANK_RULE = "Pressing Enter on a blank task adds nothing."
DELETE_RULE = "Each task has a delete button that removes it."
ONE_FILE_RULE = "Everything is in one file, index.html."
STYLES_RULE = "Its styles sit in index.html too."
BLANK_ISSUE = "The design never says what happens to a blank task."
STYLES = "The design does not say where the styles go."
ONE_FILE = "The mission asks for one file, so there is no separate stylesheet."


def _todo(*rules: str) -> dict[str, Any]:
    return {**TODO, "behaviour": [*TODO["behaviour"], *rules]}


def software_designer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """The software factory's designer, as the pages on it tell the story.

    A mission that keeps to one file draws a readiness finding, which the designer
    contests. Any other mission's first draft misses blank tasks; the revision adds them,
    and a person's request for changes adds a delete button.
    """
    said = _said(messages)
    if "one file" in said.split("\n", 1)[0].lower():
        if _answered(messages, "contest"):
            return _output(info, _todo(BLANK_RULE, ONE_FILE_RULE, STYLES_RULE))
        if "r1-readiness-1" in said:
            return call("contest", finding_id="r1-readiness-1", evidence=ONE_FILE)
        return _output(info, _todo(BLANK_RULE, ONE_FILE_RULE))
    if "A person asked for changes" in said:
        return _output(info, _todo(BLANK_RULE, DELETE_RULE))
    if "Goldfish review" in said:
        return _output(info, _todo(BLANK_RULE))
    return _output(info, TODO)


def _work(messages: list[ModelMessage]) -> str:
    return _said(messages[:1]).lower()


def critique(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Finds the missing blank-task rule, and nothing else."""
    found = [] if "blank" in _work(messages) else [{"severity": "Major", "issue": BLANK_ISSUE}]
    return _output(info, {"findings": found})


def readiness(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Asks where the styles go in a one-file page that does not say."""
    work = _work(messages)
    found = "one file" in work and "styles" not in work
    return _output(info, {"findings": [{"severity": "Major", "issue": STYLES}] if found else []})


SOFTWARE: Script = {
    "designer": [software_designer],
    "goldfish-comprehension": [
        {"paraphrase": "A to-do list in the browser: add tasks, tick them off.", "findings": []}
    ],
    "goldfish-critique": [critique],
    "goldfish-readiness": [readiness],
}
"""The software factory's agents, for every page that runs it."""


QUESTION = "Should ticked tasks stay after a reload?"


def asking_designer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Asks one question first when it can ask a person, then writes the design."""
    can_ask = any(tool.name == "ask_person" for tool in info.function_tools)
    if can_ask and not _answered(messages, "ask_person"):
        return call("ask_person", question=QUESTION)
    return ModelResponse(parts=[TextPart(DESIGN)])


SCRIPTS: dict[str, Script] = {
    "docs/getting-started.md": {
        "designer": [DESIGN],
        "builder": [call("write_file", path="index.html", content=PAGE), "Built index.html."],
        "reviewer": [
            call("read_file", path="index.html"),
            "The page matches the design: tasks can be added and ticked off. Nothing is wrong.",
        ],
    },
    "docs/factories.md": {
        "designer": [DESIGN, DESIGN_WITH_BLANK],
        "builder": ["Built index.html."],
    },
    "docs/stations.md": {
        "builder": ["Built index.html."],
        "reviewer": [REVIEW],
        "planner": [
            {"title": "To-do list", "steps": ["Write index.html", "Add a box for new tasks"]}
        ],
    },
    "docs/agents.md": {"builder": ["Built index.html."], "reviewer": [REVIEW]},
    "docs/gates.md": {"designer": [DESIGN, DESIGN_WITH_BLANK]},
    "docs/approvals.md": {
        "designer": [DESIGN, DESIGN_WITH_BLANK],
        "builder": [call("publish_page", path="index.html"), "Published index.html."],
    },
    "docs/stores.md": {"designer": [DESIGN]},
    "docs/operating.md": {"designer": [asking_designer], "builder": ["Built index.html."]},
    "docs/deploying.md": {"designer": [DESIGN], "builder": ["Built index.html."]},
    "docs/board.md": {
        "drafter": ["Tickets fell from 40 to 31 this week."],
        "checker": ["The figures add up."],
    },
    "docs/missions.md": {"designer": [DESIGN], "builder": ["Built index.html."]},
    "docs/elephant-goldfish.md": {
        "designer": [DESIGN, DESIGN_WITH_BLANK],
        "goldfish-comprehension": [{"paraphrase": PARAPHRASE, "findings": []}],
        "goldfish-critique": [{"findings": [BLANK_TASK]}, {"findings": []}],
        "goldfish-readiness": [{"findings": []}],
    },
    "docs/methodologies.md": {
        "goldfish-comprehension": [{"paraphrase": PARAPHRASE, "findings": []}],
        "goldfish-critique": [{"findings": [BLANK_TASK]}, {"findings": []}],
        "goldfish-readiness": [{"findings": []}],
        "designer": [
            {"summary": DESIGN, "acceptance_criteria": ["Blank tasks are not added."]},
            {
                "summary": DESIGN_WITH_BLANK,
                "acceptance_criteria": [SCENARIO],
            },
        ],
    },
    "docs/software-factory.md": SOFTWARE,
    "docs/design-station.md": SOFTWARE,
    "docs/measuring.md": SOFTWARE,
    "README.md": SOFTWARE,
    "packages/software/README.md": SOFTWARE,
}
