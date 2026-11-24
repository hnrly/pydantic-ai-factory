"""A person's answer to a waiting run: one type, which names its run and can be queued as JSON."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, model_validator
from pydantic_ai import DeferredToolResults

from ..artifacts import ArtifactRef
from ..contracts import Name, Record
from ..stations import Waiting, WaitingKind


class AnswerBase(Record):
    """What every answer says: which run, which wait, who answered and when.

    ``revision`` is the run's revision when the person saw it. ``station``, ``waiting``
    and ``work`` copy the run's wait when the answer was made, so an answer cannot be
    carried over to another wait or to revised work.
    """

    run_id: Name
    revision: int
    station: Name
    waiting: WaitingKind
    work: ArtifactRef | None
    by: Name
    at: AwareDatetime


class Approve(AnswerBase):
    """Accept the waiting work and move it on.

    ``overrode`` is set by the factory, never by a person: it is true when the answer
    approved an escalation, and so accepted work that a gate or a station had refused.
    """

    kind: Literal["approve"] = "approve"
    note: str | None = None
    overrode: bool = False


class Changes(AnswerBase):
    """Send the waiting work back to its station once, with ``note`` as the feedback."""

    kind: Literal["changes"] = "changes"
    note: str

    @model_validator(mode="after")
    def _says_why(self) -> Self:
        _require_note(self.kind, self.note)
        return self


class Reject(AnswerBase):
    """Reject the waiting work. The run ends as failed, and ``note`` says why."""

    kind: Literal["reject"] = "reject"
    note: str

    @model_validator(mode="after")
    def _says_why(self) -> Self:
        _require_note(self.kind, self.note)
        return self


class Reply(AnswerBase):
    """Answer an agent's tool calls, with Pydantic AI's ``DeferredToolResults``."""

    kind: Literal["reply"] = "reply"
    results: DeferredToolResults


Answer = Annotated[Approve | Changes | Reject | Reply, Field(discriminator="kind")]
"""A person's answer to a waiting run. ``kind`` tells the four apart."""


class AnswerProblem(Record):
    """Why an answer does not fit a run, from ``Factory.check``.

    ``stale`` is true when the run has moved on since the revision the answer was made
    at. Someone else has answered, or the run has carried on, so the person should look
    at the run again.
    """

    stale: bool
    reason: str


def _require_note(kind: str, note: str) -> None:
    if not note.strip():
        raise ValueError(f"an answer of kind {kind!r} needs a note saying why")


def reply_problem(waiting: Waiting, results: DeferredToolResults) -> str | None:
    """Whether ``results`` answer each of the wait's tool calls exactly once, in the right place.

    A question is answered in ``results.calls`` and an action in ``results.approvals``;
    Pydantic AI would drop a result in the wrong place. ``run.reply`` and
    ``Factory.check`` both use this rule, so they refuse a reply with the same reason.
    """
    named = {question.call_id: repr(question.text) for question in waiting.questions}
    named |= {action.call_id: str(action) for action in waiting.actions}
    actions = {action.call_id for action in waiting.actions}
    questions = {question.call_id for question in waiting.questions}
    given_actions, given_calls = set(results.approvals), set(results.calls)

    def say(call_ids: set[str]) -> str:
        return ", ".join(named.get(call_id, repr(call_id)) for call_id in sorted(call_ids))

    found: list[str] = []
    if missing := (questions - given_calls) | (actions - given_actions):
        found.append(f"no answer for {say(missing)}")
    if extra := (given_calls - questions) | (given_actions - actions):
        found.append(f"an answer for {say(extra)}, which it is not waiting on")
    if twice := given_calls & given_actions:
        found.append(f"more than one answer for {say(twice)}")
    if not found:
        return None
    return "a reply needs exactly one answer for each tool call it waits on: " + "; ".join(found)
