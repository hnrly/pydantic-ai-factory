"""The port contract every system under test is run through.

A system receives a brief: the request alone, with the case's repository mounted
read-only as its working folder. It may ask the stakeholder questions, and it delivers a
document with a status. Nothing else crosses. There is no output contract, no template
and no approval port, and a system is not told its spending cap.

This module imports nothing from the factory, so any system can be plugged in.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from evals.events import Event
from evals.version import MAX_QUESTIONS_PER_CALL

Status = Literal["delivered", "blocked"]
"""How a system ends its design: a design it stands behind, or a stop for want of facts."""

ReportEnd = Literal["finished", "budget", "infrastructure"]
"""How a system says it stopped: on its own, at its own cap, or because something broke."""

RecordEnd = Literal["finished", "budget", "timeout", "infrastructure"]
"""How the harness says a run stopped, from the system's report and the clock."""


class Brief(BaseModel):
    """What a system receives. ``text`` is the request, unchanged.

    ``snapshot`` is the folder the sandbox mounts read-only at ``/workspace``. A system
    sees the files, never this path.
    """

    model_config = ConfigDict(frozen=True)

    case_name: str
    text: str
    snapshot: Path


class Reply(BaseModel):
    """The stakeholder's answer to one question."""

    model_config = ConfigDict(frozen=True)

    text: str


class DesignPackage(BaseModel):
    """What a system delivers: the design as markdown, in any shape, and a status.

    The ports compute the hash, in every arm alike, so a system never supplies one.
    Build one with :meth:`of`.
    """

    model_config = ConfigDict(frozen=True)

    document: str
    document_sha256: str
    status: Status

    @classmethod
    def of(cls, document: str, status: Status) -> DesignPackage:
        """The package for ``document``, with its SHA-256 hash computed here."""
        return cls(document=document, document_sha256=sha256_of(document), status=status)


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

    ``package`` is the first delivery, or ``None`` when there was none. ``end`` is
    ``timeout`` when the time limit stopped the run, and ``budget`` when a finished
    run's cost is over its cap.
    """

    model_config = ConfigDict(frozen=True)

    arm: str
    case_name: str
    repeat: int
    package: DesignPackage | None
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

    async def deliver(self, document: str, status: Status) -> None:
        """Deliver the design. The first call is the delivery; a later one changes nothing."""
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


def sha256_of(document: str) -> str:
    """The SHA-256 hash of a document's UTF-8 text, as hex."""
    return hashlib.sha256(document.encode("utf-8")).hexdigest()


def check_questions(questions: Sequence[str]) -> None:
    """Refuse a stakeholder call that carries no questions, or too many.

    Every implementation of :meth:`Ports.ask_stakeholder` calls this first.
    """
    if not 1 <= len(questions) <= MAX_QUESTIONS_PER_CALL:
        raise ValueError(
            f"a stakeholder call carries one to {MAX_QUESTIONS_PER_CALL} questions, "
            f"not {len(questions)}"
        )


def brief_for(case_name: str, request: str, snapshot: Path) -> Brief:
    """The brief for a case: its request, unchanged, and its repository snapshot."""
    return Brief(case_name=case_name, text=request, snapshot=snapshot)
