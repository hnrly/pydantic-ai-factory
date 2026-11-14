"""Missions, signals and the records intake and outcomes are made of."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import AwareDatetime, Field

from ..artifacts import Artifact, ArtifactRef
from ..contracts import FactoryError, Name, Record

PayloadT = TypeVar("PayloadT")


class IntakeError(FactoryError):
    """Intake could not decide: no signals, no accept function, or no decision came back."""


def new_mission_id() -> str:
    """Return a fresh mission id."""
    return f"mission-{uuid.uuid4().hex[:12]}"


class Mission(Record):
    """What a factory commits to: an objective, how to tell it was met, and constraints.

    Build one with ``Mission(objective, success_criteria=..., constraints=...)``. When a
    factory's first station takes a mission or a string, a plain string becomes one, and a
    station that takes a string is given :meth:`as_prompt`. Every artifact a run makes
    carries the mission's ``id``.

    ``signals`` names the signals a mission came from. Only :meth:`Factory.intake` fills it
    in; a mission built by hand records none.
    """

    id: Name = Field(default_factory=new_mission_id)
    objective: str = Field(min_length=1)
    success_criteria: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    signals: tuple[ArtifactRef, ...] = ()

    def __init__(
        self,
        objective: str,
        *,
        success_criteria: Sequence[str] = (),
        constraints: Sequence[str] = (),
        id: str | None = None,
        signals: Sequence[ArtifactRef] = (),
    ) -> None:
        fields: dict[str, Any] = {
            "objective": objective,
            "success_criteria": tuple(success_criteria),
            "constraints": tuple(constraints),
            "signals": tuple(signals),
        }
        if id is not None:
            fields["id"] = id
        super().__init__(**fields)

    def as_prompt(self) -> str:
        """The mission as text for an agent: the objective, then any criteria and constraints.

        A mission with only an objective is just the objective.
        """
        parts = [self.objective]
        for heading, items in (
            ("Success criteria:", self.success_criteria),
            ("Constraints:", self.constraints),
        ):
            if items:
                parts.append("\n".join([heading, *(f"- {item}" for item in items)]))
        return "\n\n".join(parts)


class Signal(Record, Generic[PayloadT]):
    """A piece of data that arrives on its own, such as a bug report or a failing check.

    The payload may be any value Pydantic can serialise, and its type says what kind of
    signal it is. ``source`` says where it came from, if that matters.
    """

    payload: PayloadT
    source: Name | None = None
    received_at: AwareDatetime

    def __init__(
        self,
        payload: PayloadT,
        source: str | None = None,
        *,
        received_at: datetime | None = None,
    ) -> None:
        fields: dict[str, Any] = {
            "payload": payload,
            "source": source,
            "received_at": datetime.now(UTC) if received_at is None else received_at,
        }
        super().__init__(**fields)


class Declined(Record):
    """Intake's answer when the factory does not take the work on, with the reason.

    ``signals`` names the signals that were declined. :meth:`Factory.intake` fills it in.
    """

    reason: str = Field(min_length=1)
    signals: tuple[ArtifactRef, ...] = ()

    def __init__(self, reason: str, *, signals: Sequence[ArtifactRef] = ()) -> None:
        fields: dict[str, Any] = {"reason": reason, "signals": tuple(signals)}
        super().__init__(**fields)


class Outcome(Record):
    """How a run of a mission made by intake ended: the payload of its outcome signal.

    The library does not judge the mission's success criteria. Whoever reads this signal,
    such as the next intake, decides what the outcome means.
    """

    factory: Name
    run_id: Name
    mission_id: Name
    objective: str
    mission_ref: ArtifactRef
    outcome: Literal["passed", "failed", "did_not_run"]
    finished: bool
    output_ref: ArtifactRef | None = None


Accept = Callable[[Sequence[Signal[Any]]], Mission | Declined | Awaitable[Mission | Declined]]
"""Reads signals and returns a mission to take on, or ``Declined``. May be async."""


def signal_artifact(received: Signal[Any]) -> Artifact[Any]:
    """Record a signal as an artifact, exactly as it arrived.

    The payload keeps its type, and the artifact's time is the time the signal was
    received, so storing the same signal twice gives the same artifact.
    """
    payload: Any = received.payload
    typed: Any = Signal[type(payload)]
    return Artifact.create(
        typed(payload, received.source, received_at=received.received_at),
        type="Signal",
        created_at=received.received_at,
    )
