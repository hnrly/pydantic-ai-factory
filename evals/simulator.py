"""The simulated stakeholder: one person for each case, who answers from the fact sheet.

Every reply it can give is written in the case's fact sheet. No model writes a word a
system sees. A reply is made in three steps:

1. **Pick.** A small pinned model reads one question and the list of facts, and returns
   only the ids of the facts the question asks about, and whether it asks for approval.
2. **Send.** Plain code joins the statements of the picked facts, unchanged and in sheet
   order. With nothing picked it sends the sheet's fallback reply. A request for approval
   gets the sheet's approval reply and releases nothing.
3. **Record.** Plain code logs the question, the ids picked, the facts released and the
   reply.

The picker sees one question at a time, never the others in its call, so a question's
reply does not depend on what it was asked beside. The same question asked again in a run
returns the stored reply without calling the picker.

This module is on the grader side. It never runs inside a program a system started.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Sequence

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

from evals.contract import Reply, check_questions
from evals.events import EventLog
from evals.facts import FactSheet
from evals.version import PICKER_MODEL, PICKER_PROMPT

PICKER_NAME = "picker"

APPROVAL_PHRASES = ("approve", "sign off", "sign-off")
"""What makes the scripted picker say a question asks for approval, ignoring case."""


class Pick(BaseModel):
    """What the picker may return: fact ids, and whether the question asks for approval.

    It cannot return prose.
    """

    fact_ids: list[str]
    asks_for_approval: bool


class _Answer(BaseModel):
    """One question's pick and reply, stored so the same text gets the same reply."""

    model_config = ConfigDict(frozen=True)

    picked: tuple[str, ...]
    released: tuple[str, ...]
    reply: str
    asked_for_approval: bool


class Stakeholder:
    """The stakeholder of one run. Build a fresh one for each run.

    ``picker`` chooses the facts; this class writes the replies and the log.
    """

    def __init__(self, sheet: FactSheet, picker: Agent[None, Pick], log: EventLog) -> None:
        self._sheet = sheet
        self._picker = picker
        self._log = log
        self._answers: dict[str, _Answer] = {}

    async def ask(self, questions: Sequence[str]) -> list[Reply]:
        """Answer one stakeholder call: one reply for each question, in order.

        Raises ``ValueError`` for a call with no questions or too many.
        """
        check_questions(questions)
        call = self._log.next_call()
        replies: list[Reply] = []
        for question in questions:
            self._log.append("question_asked", call=call, question=question)
            answer = self._answers.get(question)
            if answer is None:
                answer = self._answer(await self._pick(question))
                self._answers[question] = answer
            self._log.append(
                "reply_sent",
                call=call,
                question=question,
                picked=list(answer.picked),
                released=list(answer.released),
                reply=answer.reply,
                asked_for_approval=answer.asked_for_approval,
            )
            replies.append(Reply(text=answer.reply))
        return replies

    async def _pick(self, question: str) -> Pick:
        result = await self._picker.run(picker_input(question, self._sheet))
        return result.output

    def _answer(self, pick: Pick) -> _Answer:
        wanted = set(pick.fact_ids)
        picked = [fact for fact in self._sheet.facts if fact.id in wanted]
        ids = tuple(fact.id for fact in picked)
        if pick.asks_for_approval:
            return _Answer(
                picked=ids,
                released=(),
                reply=self._sheet.approval_reply,
                asked_for_approval=True,
            )
        if not picked:
            return _Answer(
                picked=(),
                released=(),
                reply=self._sheet.fallback_reply,
                asked_for_approval=False,
            )
        return _Answer(
            picked=ids,
            released=tuple(fact.id for fact in picked if fact.obtainable),
            reply=" ".join(fact.statement for fact in picked),
            asked_for_approval=False,
        )


def picker_input(question: str, sheet: FactSheet) -> str:
    """The picker's input: one question and each fact's id and statement, nothing more.

    Each part sits in tags with a random marker, fresh for each call, that appears in
    neither, so the question cannot close its own tag.
    """
    facts = "\n".join(f"{fact.id}: {fact.statement}" for fact in sheet.facts)
    marker = secrets.token_hex(8)
    while marker in question or marker in facts:
        marker = secrets.token_hex(8)
    return (
        f"<question-{marker}>\n{question}\n</question-{marker}>\n\n"
        f"<facts-{marker}>\n{facts}\n</facts-{marker}>"
    )


def haiku_picker() -> Agent[None, Pick]:
    """The picker every run on a real model uses: the pinned picking model."""
    return Agent(
        PICKER_MODEL,
        name=PICKER_NAME,
        instructions=PICKER_PROMPT,
        output_type=Pick,
        defer_model_check=True,
    )


_QUESTION = re.compile(r"<question-([0-9a-f]+)>\n(.*?)\n</question-\1>", re.DOTALL)


def scripted_picker(sheet: FactSheet) -> Agent[None, Pick]:
    """A picker on a scripted model, used by every offline run.

    It picks a fact when the question holds one of the fact's key words as a whole word,
    and says the question asks for approval when it holds "approve", "sign off" or
    "sign-off", all ignoring case. It reads the key words from ``sheet``, because the
    picker's input carries only each fact's id and statement.
    """
    patterns = {
        fact.id: [re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE) for word in fact.keywords]
        for fact in sheet.facts
    }

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        found = _QUESTION.search(_last_prompt(messages))
        question = found.group(2) if found else ""
        ids = [i for i, words in patterns.items() if any(w.search(question) for w in words)]
        approval = any(phrase in question.lower() for phrase in APPROVAL_PHRASES)
        answer = {"fact_ids": ids, "asks_for_approval": approval}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, answer)])

    return Agent(
        FunctionModel(respond, model_name="scripted-picker"),
        name=PICKER_NAME,
        instructions=PICKER_PROMPT,
        output_type=Pick,
    )


def _last_prompt(messages: Sequence[ModelMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    return part.content
    return ""
