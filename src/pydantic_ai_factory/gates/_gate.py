"""Gates: plain code that judges recorded evidence and gives its reasons."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import AwareDatetime, BaseModel, Field

from ..artifacts import Artifact, ArtifactRef
from ..contracts import FactoryError, MethodologyRef, Name, Record

OutT = TypeVar("OutT", bound=BaseModel)
EvidenceT = TypeVar("EvidenceT", bound=BaseModel)

Judgement = Literal["pass", "fail", "needs_approval"]


class GateDefinitionError(FactoryError):
    """A gate was declared in a way that cannot work."""


class Verdict(Record):
    """What a gate's check returns: a judgement and the reasons for it.

    Build one with :func:`passed`, :func:`failed` or :func:`needs_approval`.
    """

    judgement: Judgement
    reasons: tuple[str, ...] = ()


def passed(*reasons: str) -> Verdict:
    """The work may move on."""
    return Verdict(judgement="pass", reasons=reasons)


def failed(*reasons: str) -> Verdict:
    """The work was judged and is not good enough. At least one reason is required."""
    if not reasons:
        raise ValueError("a failed verdict needs at least one reason")
    return Verdict(judgement="fail", reasons=reasons)


def needs_approval(*reasons: str) -> Verdict:
    """The checks are satisfied, and a person must now decide."""
    return Verdict(judgement="needs_approval", reasons=reasons)


class Evidence:
    """The artifacts a gate may read: a station's output and what it recorded beside it."""

    def __init__(self, artifacts: Sequence[Artifact[Any]] = ()) -> None:
        self._artifacts = tuple(artifacts)

    @property
    def refs(self) -> tuple[ArtifactRef, ...]:
        """The ref of every artifact held, in the order recorded."""
        return tuple(artifact.ref for artifact in self._artifacts)

    def of(self, payload_type: type[EvidenceT]) -> list[Artifact[EvidenceT]]:
        """Return every artifact whose payload is a ``payload_type``."""
        return [a for a in self._artifacts if isinstance(a.payload, payload_type)]

    def one(self, payload_type: type[EvidenceT]) -> Artifact[EvidenceT]:
        """Return the single artifact whose payload is a ``payload_type``.

        Raises ``LookupError`` if there is none, or more than one.
        """
        found = self.of(payload_type)
        if len(found) != 1:
            raise LookupError(
                f"expected one {payload_type.__name__} in evidence, found {len(found)}"
            )
        return found[0]

    def __len__(self) -> int:
        return len(self._artifacts)


@dataclass(frozen=True)
class Gate(Generic[OutT]):
    """A named, versioned check over a station's output and evidence.

    ``check`` must be plain code with no I/O. ``requires`` lists the payload types that
    must be present in evidence. If one is missing the check is never called, and the
    gate reports that it could not be evaluated.
    """

    name: str
    criteria_version: str
    check: Callable[[Artifact[OutT], Evidence], Verdict]
    requires: tuple[type[BaseModel], ...] = ()


def gate(
    *,
    version: str,
    requires: Sequence[type[BaseModel]] = (),
    name: str | None = None,
) -> Callable[[Callable[[Artifact[OutT], Evidence], Verdict]], Gate[OutT]]:
    """Turn a function into a :class:`Gate`.

    ``version`` is the criteria version. Change it whenever the check's rules change, so
    a decision always says which rules made it.
    """

    def wrap(check: Callable[[Artifact[OutT], Evidence], Verdict]) -> Gate[OutT]:
        gate_name = name or getattr(check, "__name__", "")
        if not gate_name or gate_name == "<lambda>":
            raise GateDefinitionError("a gate needs a name; pass name= when using a lambda")
        return Gate(name=gate_name, criteria_version=version, check=check, requires=tuple(requires))

    return wrap


class GateDecision(Record):
    """The record of one gate judging one output.

    It names the gate, the version of its criteria, every artifact it was shown and the
    reasons for its judgement.
    """

    gate: Name
    criteria_version: Name
    judgement: Judgement
    reasons: tuple[str, ...] = ()
    judged: tuple[ArtifactRef, ...] = Field(min_length=1)
    methodologies: tuple[MethodologyRef, ...] = ()
    decided_at: AwareDatetime


class NotEvaluated(Record):
    """A gate that could not be evaluated. This is never a pass and never a fail."""

    gate: Name
    criteria_version: Name
    reason: str


def evaluate_gate(
    gate: Gate[OutT],
    output: Artifact[OutT],
    evidence: Evidence,
    *,
    methodologies: Sequence[MethodologyRef] = (),
    decided_at: datetime | None = None,
) -> GateDecision | NotEvaluated:
    """Judge ``output`` with ``gate``, or say why it could not be judged.

    The gate is not evaluated when required evidence is missing, when the check raises,
    or when it returns something other than a :class:`Verdict`.
    """

    def not_evaluated(reason: str) -> NotEvaluated:
        return NotEvaluated(gate=gate.name, criteria_version=gate.criteria_version, reason=reason)

    missing = [required.__name__ for required in gate.requires if not evidence.of(required)]
    if missing:
        return not_evaluated(f"missing evidence: {', '.join(missing)}")
    try:
        verdict: object = gate.check(output, evidence)
    except Exception as error:
        return not_evaluated(f"the check raised {type(error).__name__}: {error}")
    # The check is user code and may be untyped, so its result is checked at run time.
    if not isinstance(verdict, Verdict):  # pyright: ignore[reportUnnecessaryIsInstance]
        return not_evaluated(f"the check returned {type(verdict).__name__}, not a verdict")

    judged = tuple(dict.fromkeys((output.ref, *evidence.refs)))
    return GateDecision(
        gate=gate.name,
        criteria_version=gate.criteria_version,
        judgement=verdict.judgement,
        reasons=verdict.reasons,
        judged=judged,
        methodologies=tuple(methodologies),
        decided_at=decided_at or datetime.now(UTC),
    )
