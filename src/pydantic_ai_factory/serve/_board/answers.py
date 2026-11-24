"""Building a person's answer from the run it answers."""

from __future__ import annotations

from typing import Any

from ...factory import Answer, ApprovalRefused, Run
from .views import AnswerBody


def make_answer(run: Run, body: AnswerBody, by: str) -> Answer:
    """Build the person's answer from the run, so the station, wait and work are the run's.

    Raises ``RunConflict`` when ``body.revision`` is earlier than the run's, and
    ``ApprovalRefused`` or ``ValueError`` when the answer cannot be made.
    """
    if body.kind != "reply":
        if body.replies is not None or body.approvals is not None:
            raise ApprovalRefused(f"{body.kind!r} takes no replies or approvals")
        if body.kind == "approve":
            return run.approve(by=by, note=body.note, revision=body.revision)
        if body.kind == "changes":
            return run.request_changes(by=by, note=body.note or "", revision=body.revision)
        return run.reject(by=by, note=body.note or "", revision=body.revision)
    if body.note is not None:
        raise ApprovalRefused("'reply' takes replies and approvals, not a note")
    replies, approvals = body.replies or {}, body.approvals or {}
    if twice := sorted(set(replies) & set(approvals)):
        raise ApprovalRefused(f"more than one answer for {', '.join(map(repr, twice))}")
    answers: dict[Any, Any] = dict(approvals)
    for call_id, reply in replies.items():
        if isinstance(reply, str):
            answers[call_id] = reply
            continue
        # An AskPerson call is answered question by question, keyed by header.
        asked = {q.header: q for q in run.questions if q.call_id == call_id and q.header}
        for header, value in reply.items():
            if header not in asked:
                raise ApprovalRefused(
                    f"call {call_id!r} asks no question with the header {header!r}"
                )
            answers[asked[header]] = value
    return run.reply(answers, by=by, revision=body.revision)
