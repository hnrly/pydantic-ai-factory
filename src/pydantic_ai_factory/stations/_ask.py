"""AskPerson: let an agent ask a person a round of questions, built on the harness's AskUser."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from pydantic import ValidationError
from pydantic_ai import RunContext, ToolReturn
from pydantic_ai.capabilities import AbstractCapability, WrapperCapability
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import AgentToolset
from pydantic_ai_harness.ask_user import (
    TOOL_NAME,
    AskUser,
    AskUserAnswer,
    AskUserRequest,
    AskUserResponse,
    AskUserResult,
    ask_user_result,
    check_response,
)
from pydantic_ai_harness.ask_user import (
    Question as AskedQuestion,
)

from ..contracts import DefinitionError

DONE_ASKING = (
    "You have asked the person every round of questions you may. Do not ask again: carry on "
    "with your best judgement, and say what you assumed."
)
"""The instructions once ``AskPerson`` has taken its tool away."""


ASK_PERSON_ID = "ask_person"
"""The id every ``AskPerson`` has, so an agent ends up with one."""


def _ask_user() -> AbstractCapability[Any]:
    return AskUser(answerer=None)


def ask_request(call: ToolCallPart, metadata: Mapping[str, Any] | None) -> AskUserRequest | None:
    """The request behind an ``ask_user_question`` call, or ``None`` for any other tool.

    The questions come from the call's deferred metadata, where ``AskUser`` keeps them as
    it received them, or else from the call's arguments.
    """
    if call.tool_name != TOOL_NAME:
        return None
    questions = (metadata or {}).get("questions")
    if questions is None:
        return AskUserRequest.from_tool_call(call)
    request_id = (metadata or {}).get("request_id")
    return AskUserRequest(
        questions=tuple(AskedQuestion.model_validate(question) for question in questions),
        id=request_id if isinstance(request_id, str) else call.tool_call_id,
    )


def answer_of(question: AskedQuestion, value: object) -> AskUserAnswer:
    """One person's answer to one question: a label, a list of labels, or their own words.

    A string that is one of the question's labels picks it; any other string is the
    person's own answer. A list or tuple picks each label in it.
    """
    labels = {option.label for option in question.options}
    if isinstance(value, str):
        if value in labels:
            return AskUserAnswer(header=question.header, selected=(value,))
        return AskUserAnswer(header=question.header, custom_answer=value)
    if isinstance(value, list | tuple) and all(isinstance(item, str) for item in value):  # pyright: ignore[reportUnknownVariableType]
        return AskUserAnswer(header=question.header, selected=tuple(value))  # pyright: ignore[reportUnknownArgumentType]
    raise ValueError(
        f"answer {question.header!r} with a label, a list of labels or your own words, "
        f"not {value!r}"
    )


def ask_result(request: AskUserRequest, answers: Mapping[str, object]) -> AskUserResult:
    """The ``ask_user_question`` result for ``answers``, keyed by question header.

    Raises ``ValueError`` when the answers do not fit the request: a question left out, a
    label it does not offer, or more than one label for a single-select question.
    """
    questions = {question.header: question for question in request.questions}
    if unknown := sorted(set(answers) - set(questions)):
        raise ValueError(f"no question has the header {', '.join(map(repr, unknown))}")
    response = AskUserResponse(
        answers=tuple(answer_of(questions[header], value) for header, value in answers.items())
    )
    return ask_user_result(request, response)


def result_problem(request: AskUserRequest, result: object) -> str | None:
    """Why ``result`` is not a full answer to ``request``, or ``None`` when it is.

    ``result`` is what goes in ``DeferredToolResults.calls``: the answer dictionary
    ``ask_user_result`` makes, ``{header: [label, ...]}``, or ``[own words]`` for a
    question answered in the person's own words.
    """
    value = result.return_value if isinstance(result, ToolReturn) else result
    if not isinstance(value, Mapping):
        return f"the answer to {request.id!r} is not keyed by question header"
    questions = {question.header: question for question in request.questions}
    answers: list[AskUserAnswer] = []
    for header, picked in value.items():  # pyright: ignore[reportUnknownVariableType]
        question = questions.get(header)  # pyright: ignore[reportUnknownArgumentType]
        if question is None:
            return f"no question has the header {header!r}"
        if not isinstance(picked, list) or not all(isinstance(item, str) for item in picked):  # pyright: ignore[reportUnknownVariableType]
            return f"the answer to {header!r} is not a list of labels"
        labels = {option.label for option in question.options}
        if len(picked) == 1 and picked[0] not in labels:  # pyright: ignore[reportUnknownArgumentType]
            answers.append(AskUserAnswer(header=header, custom_answer=picked[0]))  # pyright: ignore[reportUnknownArgumentType]
        else:
            answers.append(AskUserAnswer(header=header, selected=tuple(picked)))  # pyright: ignore[reportUnknownArgumentType]
    try:
        check_response(request, AskUserResponse(answers=tuple(answers)))
    except (ValueError, ValidationError) as error:
        return str(error)
    return None


def rounds_asked(messages: Sequence[ModelMessage]) -> int:
    """How many model responses asked questions that have since been answered.

    A round is one model response with an ``ask_user_question`` call. It counts once
    the person has answered it, however many calls or questions the response held.
    """
    answered = {
        part.tool_call_id
        for message in messages
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == TOOL_NAME
    }
    return sum(
        1
        for message in messages
        if isinstance(message, ModelResponse)
        and any(
            call.tool_name == TOOL_NAME and call.tool_call_id in answered
            for call in message.tool_calls
        )
    )


@dataclass
class AskPerson(WrapperCapability[Any]):
    """Let an agent ask a person a round of questions, and wait for the answers.

    It is built on Pydantic AI Harness's ``AskUser``, with no answerer: each
    ``ask_user_question`` call ends the agent's run as a deferred tool call. Inside a
    station that makes the run wait once, with ``run.waiting.kind`` of ``"tool-calls"``,
    until a person answers each question with ``run.reply``.

    On top of ``AskUser`` it adds two things. Its instructions ask for every question in
    one call, with why each one matters and the recommended option first. And it caps
    the rounds: once ``max_rounds`` responses that asked have been answered, the tool is
    taken away. ``max_rounds=0`` gives the agent no way to ask.

    It is loaded eagerly, not with ``defer_loading``: an agent asks its questions before
    it starts work, so the tool must be visible from the first request.
    """

    wrapped: AbstractCapability[Any] = field(default_factory=_ask_user, repr=False)
    max_rounds: int = 3
    id: str | None = field(default=ASK_PERSON_ID, kw_only=True)
    """One id, so an agent has one ``AskPerson`` however many places bring one."""
    bundled: bool = field(default=False, init=False, repr=False, compare=False)
    """True when a methodology brought it, such as Elephant Goldfish, not the agent's author."""

    def __post_init__(self) -> None:
        value = self.max_rounds
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:  # pyright: ignore[reportUnnecessaryIsInstance]
            raise DefinitionError(
                "capability 'AskPerson'", ["max_rounds must be a whole number, 0 or more"]
            )
        super().__post_init__()

    @classmethod
    def combine(cls, capabilities: Sequence[AbstractCapability[Any]]) -> AbstractCapability[Any]:
        """One ``AskPerson`` for the agent: one its author listed wins over a bundled one.

        Elephant Goldfish brings an ``AskPerson``; an author who also lists one has said how
        they want the agent to ask, so theirs is used. Two the author listed must agree.
        """
        asks = cast("list[AskPerson]", list(capabilities))
        chosen = [ask for ask in asks if not ask.bundled] or asks
        if len({ask.max_rounds for ask in chosen}) > 1:
            raise DefinitionError(
                "capability 'AskPerson'",
                ["the agent has two AskPerson with different max_rounds; give it one"],
            )
        return chosen[-1]

    def get_toolset(self) -> AgentToolset[Any] | None:
        return None if self.max_rounds == 0 else super().get_toolset()

    def get_instructions(self) -> Any:
        if self.max_rounds == 0:
            return None
        rounds = self.max_rounds
        own = (
            f"You may ask the person {rounds} round{'s' if rounds != 1 else ''} of questions "
            f"with `{TOOL_NAME}`. Ask only what the mission leaves open and the work depends "
            "on. Put every question you have in one call. In each question, say why it "
            "matters: what you would do differently with each answer. Offer the option you "
            'recommend first, with " (Recommended)" at the end of its label.'
        )
        theirs: Any = super().get_instructions()
        if theirs is not None and not isinstance(theirs, str):
            # Instructions that are not plain text cannot be withdrawn with the tool.
            if isinstance(theirs, Sequence):
                return [*cast("Sequence[Any]", theirs), own]
            return [theirs, own]
        asking = own if theirs is None else f"{theirs}\n\n{own}"

        def instructions(ctx: RunContext[Any]) -> str:
            # Once the tool is taken away, say so, rather than point at a tool that is gone.
            return DONE_ASKING if rounds_asked(ctx.messages) >= rounds else asking

        return instructions

    async def prepare_tools(
        self, ctx: RunContext[Any], tool_defs: list[ToolDefinition]
    ) -> list[ToolDefinition]:
        tool_defs = await super().prepare_tools(ctx, tool_defs)
        if rounds_asked(ctx.messages) < self.max_rounds:
            return tool_defs
        return [tool for tool in tool_defs if tool.name != TOOL_NAME]
