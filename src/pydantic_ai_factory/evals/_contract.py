"""The port contract every system under test is run through.

A system receives a brief: the request alone, and, when the suite gives one, a workspace
folder it may read. It may ask the stakeholder questions, and it delivers a text with a
status. Nothing else crosses. There is no output template and no approval port, and a
system is not told its spending cap.

This module imports nothing from the factory, so any system can be plugged in: a factory,
one station, or a program that runs outside this process.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, JsonValue

from ._events import Event

MAX_QUESTIONS_PER_CALL = 4
"""How many questions one stakeholder call may carry. Each question is one turn."""

Status = Literal["delivered", "blocked"]
"""How a system ends: a delivery it stands behind, or a stop for want of facts."""

ReportEnd = Literal["finished", "budget", "infrastructure"]
"""How a system says it stopped: on its own, at its own cap, or because something broke."""

RecordEnd = Literal["finished", "budget", "timeout", "infrastructure"]
"""How the harness says a run stopped, from the system's report and the clock."""


class Brief(BaseModel):
    """What a system receives. ``text`` is the request, unchanged.

    ``workspace`` is the folder a system may read, or ``None`` when the suite gives none.
    A system that runs in a sandbox sees the files, never this path.
    """

    model_config = ConfigDict(frozen=True)

    case_name: str
    text: str
    workspace: Path | None = None


class Reply(BaseModel):
    """The stakeholder's answer to one question."""

    model_config = ConfigDict(frozen=True)

    text: str


class Delivery(BaseModel):
    """What a system delivers: its work as text, in any shape, and a status.

    ``output`` is the system's typed output as JSON, when it has one, such as a station's
    output. Graders read only ``text``. The ports compute the hash, in every system alike,
    so a system never supplies one. Build one with :meth:`of`.
    """

    model_config = ConfigDict(frozen=True)

    text: str
    sha256: str
    status: Status
    output: JsonValue = None

    @classmethod
    def of(cls, text: str, status: Status, *, output: JsonValue = None) -> Delivery:
        """The delivery of ``text``, with its SHA-256 hash computed here."""
        return cls(text=text, sha256=sha256_of(text), status=status, output=output)


class RunReport(BaseModel):
    """What an adapter says about its own run when it ends: cost, tokens and how it stopped.

    ``cost_usd`` is ``None`` when the system reported no price. It is never guessed.
    """

    model_config = ConfigDict(frozen=True)

    cost_usd: float | None
    input_tokens: int
    output_tokens: int
    end: ReportEnd
    detail: str = ""


class RunRecord(BaseModel):
    """Everything grading reads about one run. The harness builds it.

    ``arm`` names the system under test. ``delivery`` is the first delivery, or ``None``
    when there was none. ``end`` is ``timeout`` when the time limit stopped the run, and
    ``budget`` when a finished run's cost is over its cap.
    """

    model_config = ConfigDict(frozen=True)

    arm: str
    case_name: str
    repeat: int
    delivery: Delivery | None
    events: tuple[Event, ...]
    cost_usd: float | None
    input_tokens: int
    output_tokens: int
    seconds: float
    cap_usd: float
    time_limit_seconds: float
    end: RecordEnd
    detail: str = ""
    reruns: int = 0


class Ports(Protocol):
    """The two ports a system calls out through. Every method is async."""

    async def ask_stakeholder(self, questions: list[str]) -> list[Reply]:
        """Ask one to ``MAX_QUESTIONS_PER_CALL`` questions; get one reply for each, in order.

        An empty list, or a longer one, raises ``ValueError``, which the port tools turn
        into a tool error the system can read and retry.
        """
        ...

    async def deliver(self, text: str, status: Status, *, output: JsonValue = None) -> None:
        """Deliver the work. The first call is the delivery; a later one changes nothing."""
        ...


Approved = Literal["approval", "escalation"]
"""What an in-process adapter approved on a person's behalf."""


@runtime_checkable
class ApprovalLog(Protocol):
    """Where an in-process adapter notes an approval it gave in place of a person.

    It is not a port: no system calls it as a tool, and grading ignores what it writes.
    Only the factory and station adapters use it, when they answer a factory's own
    approval wait or an escalation, so a reader can see where the factory stopped for a
    person.
    """

    def adapter_approved(self, what: Approved, reasons: list[str]) -> None:
        """Log that the adapter approved ``what``, with the reasons the factory gave."""
        ...


class SystemAdapter(Protocol):
    """A system under test, run through the ports."""

    @property
    def name(self) -> str:
        """The arm's name, such as ``factory``."""
        ...

    async def run(self, brief: Brief, ports: Ports) -> RunReport:
        """Run the system on one brief, and report what the run cost."""
        ...


def sha256_of(text: str) -> str:
    """The SHA-256 hash of a text's UTF-8 bytes, as hex."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def check_questions(questions: Sequence[str]) -> None:
    """Refuse a stakeholder call that carries no questions, or too many.

    Every implementation of :meth:`Ports.ask_stakeholder` calls this first.
    """
    if not 1 <= len(questions) <= MAX_QUESTIONS_PER_CALL:
        raise ValueError(
            f"a stakeholder call carries one to {MAX_QUESTIONS_PER_CALL} questions, "
            f"not {len(questions)}"
        )
