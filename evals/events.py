"""The append-only log of everything that crosses a port during one run.

Graders read this log and the delivered package. They never read a claim a system makes
about itself. An event, once written, cannot be changed or removed.

A stakeholder call with several questions is logged as one ``question_asked`` event for
each question, each followed by its ``reply_sent``, all with the same ``call`` number.
Each question is one stakeholder turn, and a repeated question counts again.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

EventKind = Literal[
    "brief_sent",
    "question_asked",
    "reply_sent",
    "adapter_approved",
    "delivered",
    "delivered_again",
]
"""What an event records."""

EVENT_DATA: dict[str, frozenset[str]] = {
    "brief_sent": frozenset({"case_name"}),
    "question_asked": frozenset({"call", "question"}),
    "reply_sent": frozenset(
        {"call", "question", "picked", "released", "reply", "asked_for_approval"}
    ),
    "adapter_approved": frozenset({"what", "reasons"}),
    "delivered": frozenset({"document_sha256", "status"}),
    "delivered_again": frozenset({"document_sha256", "status"}),
}
"""The data each kind of event carries, no more and no less."""


class Event(BaseModel):
    """One thing that crossed a port, with the run it belongs to and when."""

    model_config = ConfigDict(frozen=True)

    run: str
    seq: int
    at: datetime
    kind: EventKind
    data: dict[str, Any]


class EventLog:
    """The events of one run, kept in order and written to ``path`` as JSON lines.

    It can only add an event. There is no way to change or remove one.
    """

    def __init__(self, path: Path, *, run: str) -> None:
        self._path = path
        self._run = run
        self._events: list[Event] = []

    @property
    def run(self) -> str:
        """The run these events belong to."""
        return self._run

    @property
    def events(self) -> tuple[Event, ...]:
        """Every event so far, in the order appended."""
        return tuple(self._events)

    def append(self, kind: EventKind, **data: Any) -> Event:
        """Add one event, and write it as one line of JSON.

        Raises ``ValueError`` when ``data`` does not hold exactly the fields the kind
        carries.
        """
        expected = EVENT_DATA[kind]
        if frozenset(data) != expected:
            raise ValueError(
                f"a {kind} event carries {sorted(expected)}, but was given {sorted(data)}"
            )
        event = Event(
            run=self._run,
            seq=len(self._events) + 1,
            at=datetime.now(UTC),
            kind=kind,
            data=json.loads(json.dumps(data)),
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as file:
            file.write(event.model_dump_json() + "\n")
        self._events.append(event)
        return event

    def next_call(self) -> int:
        """The number the next stakeholder call takes. Calls are numbered from 1."""
        calls = [int(e.data["call"]) for e in self._events if e.kind == "question_asked"]
        return max(calls, default=0) + 1

    def deliver(self, document_sha256: str, status: str) -> bool:
        """Log a delivery. Only the first counts; any later one is ``delivered_again``.

        Returns whether this was the delivery.
        """
        first = not any(e.kind == "delivered" for e in self._events)
        kind: EventKind = "delivered" if first else "delivered_again"
        self.append(kind, document_sha256=document_sha256, status=status)
        return first
