"""What a person sends to answer a waiting run, and whether it fits the run.

The API checks an answer before it queues it, and the worker reads it back from the
job and checks it again. This module imports nothing from the rest of the app, so both
can use it.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_ai_factory import Run

Choice = Literal["approve", "changes", "reject", "reply"]


class Answer(BaseModel):
    """A person's answer to a waiting run, as they saw it at ``revision``.

    ``approve``, ``changes`` and ``reject`` answer an approval or an escalation.
    ``changes`` and ``reject`` need a ``note`` saying why. ``reply`` answers an agent's
    tool calls: ``replies`` maps each question's call id to the person's text, and
    ``approvals`` maps each call that needs sign-off to whether it may run.
    """

    choice: Choice
    by: str = Field(min_length=1, max_length=200)
    revision: int = Field(ge=0)
    note: str | None = None
    replies: dict[str, str] | None = None
    approvals: dict[str, bool] | None = None

    @field_validator("by")
    @classmethod
    def _name_is_trimmed(cls, value: str) -> str:
        value = value.strip()
        if not value or "\n" in value:
            raise ValueError("by must be a name on one line")
        return value

    @model_validator(mode="after")
    def _fits_the_choice(self) -> Self:
        if self.choice in ("changes", "reject") and not (self.note and self.note.strip()):
            raise ValueError(f"{self.choice!r} needs a note saying why")
        if self.choice == "reply":
            if not self.replies and not self.approvals:
                raise ValueError("'reply' needs replies, approvals or both")
            if self.replies and any(not text.strip() for text in self.replies.values()):
                raise ValueError("each reply needs some text")
            if self.note is not None:
                raise ValueError("'reply' takes replies and approvals, not a note")
        elif self.replies is not None or self.approvals is not None:
            raise ValueError(f"{self.choice!r} takes no replies or approvals")
        return self


def problem_with(answer: Answer, run: Run) -> str | None:
    """Say why ``answer`` does not fit what ``run`` waits for, or return ``None``.

    ``approve``, ``changes`` and ``reject`` fit an approval or an escalation, except that
    ``approve`` does not fit an escalation that offers no work. ``reply`` fits a wait on
    tool calls, and needs exactly one entry for every pending call: a reply for each
    question and an approval for each call that needs sign-off.
    """
    waiting = run.waiting
    if waiting is None:
        return f"The run is not waiting; it is {run.outcome!r}."
    if waiting.kind != "tool-calls":
        if answer.choice == "reply":
            return f"The run waits for {waiting.kind}; approve, ask for changes or reject."
        if answer.choice == "approve" and waiting.artifact is None:
            return "The run offers no work to accept; ask for changes or reject."
        return None
    requests = waiting.requests
    if answer.choice != "reply" or requests is None:
        return "The run waits on an agent's tool calls; reply to them."
    questions = sorted(call.tool_call_id for call in requests.calls)
    sign_offs = sorted(call.tool_call_id for call in requests.approvals)
    if sorted(answer.replies or {}) != questions:
        return f"Reply to exactly these questions: {questions}."
    if sorted(answer.approvals or {}) != sign_offs:
        return f"Approve or deny exactly these tool calls: {sign_offs}."
    return None
