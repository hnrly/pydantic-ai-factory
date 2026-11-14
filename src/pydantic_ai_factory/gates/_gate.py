"""Gates: plain code that judges recorded evidence and gives its reasons."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Generic, Literal, Self, TypeVar

from pydantic import AwareDatetime, Field, model_validator

from .. import _tracing as tracing
from ..artifacts import Artifact, ArtifactRef
from ..contracts import DefinitionError, MethodologyRef, Name, Record, check_name

logger = logging.getLogger("pydantic_ai_factory")

OutT = TypeVar("OutT")
EvidenceT = TypeVar("EvidenceT")

Judgement = Literal["passed", "failed", "escalate"]


class GateDefinitionError(DefinitionError):
    """A gate was declared in a way that cannot work."""


class Verdict(Record):
    """What a gate's check returns: a judgement and the reasons for it.

    Build one with :func:`passed`, :func:`failed` or :func:`escalate`. Every
    verdict carries at least one reason, so a decision always says why.
    """

    judgement: Judgement
    reasons: tuple[str, ...] = Field(min_length=1)


def passed(reason: str, *more: str) -> Verdict:
    """The work may move on."""
    return Verdict(judgement="passed", reasons=(reason, *more))


def failed(reason: str, *more: str) -> Verdict:
    """The work was judged and is not good enough."""
    return Verdict(judgement="failed", reasons=(reason, *more))


def escalate(reason: str, *more: str) -> Verdict:
    """Code must not settle this; a person must. The run waits for one."""
    return Verdict(judgement="escalate", reasons=(reason, *more))


class Evidence:
    """What a station recorded beside its output, for its gate to read.

    The output itself is not in here; a check receives it as its first argument.
    """

    def __init__(self, artifacts: Sequence[Artifact[Any]] = ()) -> None:
        self._artifacts = tuple(dict.fromkeys(artifacts))

    @property
    def refs(self) -> tuple[ArtifactRef, ...]:
        """The ref of every artifact held, in the order recorded."""
        return tuple(artifact.ref for artifact in self._artifacts)

    def of(self, payload_type: type[EvidenceT]) -> list[Artifact[EvidenceT]]:
        """Return every artifact whose payload is a ``payload_type`` or a subclass of it."""
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

    ``check`` must be plain synchronous code with no I/O. ``requires`` lists the payload
    types that must have been recorded as evidence. If one is missing the check is never
    called, and the gate reports that it could not be evaluated.
    """

    name: str
    version: str
    check: Callable[[Artifact[OutT], Evidence], Verdict]
    requires: tuple[type[Any], ...] = ()

    def __post_init__(self) -> None:
        problems: list[str] = []
        for label, value in (("name", self.name), ("version", self.version)):
            problem = check_name(value)
            if problem:
                problems.append(f"the {label} {problem}")
        if not callable(self.check):
            problems.append("check must be a function")
        elif inspect.iscoroutinefunction(self.check):
            problems.append("check must be a plain function, not async")
        requires: object = self.requires
        if not isinstance(requires, tuple):  # pyright: ignore[reportUnnecessaryIsInstance]
            problems.append("requires must be a list of classes")
        else:
            for required in self.requires:
                if not inspect.isclass(required):
                    problems.append(f"requires must hold classes, not {required!r}")
        if problems:
            raise GateDefinitionError(f"gate {self.name!r}", problems)

    def evaluate(
        self,
        output: Artifact[OutT],
        evidence: Evidence,
        *,
        methodologies: Sequence[MethodologyRef] = (),
        decided_at: datetime | None = None,
    ) -> GateDecision | NotEvaluated:
        """Judge ``output``, or say why it could not be judged.

        The gate is not evaluated when required evidence is missing, when the check
        raises, or when it returns something other than a :class:`Verdict`. A check that
        raises is logged with its traceback on the ``pydantic_ai_factory`` logger.

        The evaluation is traced as a span named ``gate`` and the gate's name. A gate
        that could not be evaluated marks it as an error; a gate that fails the work
        does not.
        """
        producer = output.produced_by
        with tracing.span(
            f"gate {self.name}",
            {
                "factory.gate.name": self.name,
                "factory.gate.version": self.version,
                "factory.gate.judged": list(
                    dict.fromkeys(ref.digest for ref in (output.ref, *evidence.refs))
                ),
                "factory.run.id": producer.run_id if producer else None,
            },
        ) as current:
            result = self._evaluate(
                output, evidence, methodologies=methodologies, decided_at=decided_at
            )
            if isinstance(result, NotEvaluated):
                tracing.annotate(
                    current,
                    {
                        "factory.gate.not_evaluated": result.reason,
                        "factory.gate.missing": result.missing,
                    },
                )
                tracing.mark_error(current, result.reason)
            else:
                tracing.annotate(
                    current,
                    {
                        "factory.gate.judgement": result.judgement,
                        "factory.gate.reasons": result.reasons,
                    },
                )
            return result

    def _evaluate(
        self,
        output: Artifact[OutT],
        evidence: Evidence,
        *,
        methodologies: Sequence[MethodologyRef],
        decided_at: datetime | None,
    ) -> GateDecision | NotEvaluated:
        """Do the work of :meth:`evaluate`."""

        def not_evaluated(reason: str, missing: tuple[str, ...] = ()) -> NotEvaluated:
            return NotEvaluated(
                gate=self.name, version=self.version, reason=reason, missing=missing
            )

        missing = tuple(r.__name__ for r in self.requires if not evidence.of(r))
        if missing:
            return not_evaluated(f"missing evidence: {', '.join(missing)}", missing)
        try:
            verdict: object = self.check(output, evidence)
        except Exception as error:
            logger.exception("gate %r raised while judging %s", self.name, output.ref)
            return not_evaluated(f"the check raised {type(error).__name__}: {error}")
        # The check is user code and may be untyped, so its result is checked at run time.
        if not isinstance(verdict, Verdict):  # pyright: ignore[reportUnnecessaryIsInstance]
            return not_evaluated(f"the check returned {type(verdict).__name__}, not a verdict")

        return GateDecision(
            gate=self.name,
            version=self.version,
            judgement=verdict.judgement,
            reasons=verdict.reasons,
            judged=tuple(dict.fromkeys((output.ref, *evidence.refs))),
            methodologies=tuple(methodologies),
            decided_at=decided_at or datetime.now(UTC),
        )


def gate(
    *,
    version: str,
    requires: Sequence[type[Any]] = (),
    name: str | None = None,
) -> Callable[[Callable[[Artifact[OutT], Evidence], Verdict]], Gate[OutT]]:
    """Turn a function into a :class:`Gate`.

    Change ``version`` whenever the check's rules change, so a decision always says
    which rules made it.
    """

    def wrap(check: Callable[[Artifact[OutT], Evidence], Verdict]) -> Gate[OutT]:
        gate_name = getattr(check, "__name__", "") if name is None else name
        if gate_name == "<lambda>":
            raise GateDefinitionError("a lambda gate", ["it needs a name; pass name="])
        listed: object = requires
        if isinstance(listed, type):
            raise GateDefinitionError(
                f"gate {gate_name!r}", [f"requires must be a list, such as [{listed.__name__}]"]
            )
        return Gate(name=gate_name, version=version, check=check, requires=tuple(requires))

    return wrap


class GateDecision(Record):
    """The record of one gate judging one output.

    It names the gate, its version, every artifact it was shown and the
    reasons for its judgement.
    """

    gate: Name
    version: Name
    judgement: Judgement
    reasons: tuple[str, ...] = Field(min_length=1)
    judged: tuple[ArtifactRef, ...] = Field(min_length=1)
    methodologies: tuple[MethodologyRef, ...] = ()
    decided_at: AwareDatetime


class NotEvaluated(Record):
    """A gate that could not be evaluated. This is never a pass and never a fail.

    ``missing`` names the required evidence that was absent, when that was the cause.
    """

    gate: Name
    version: Name
    reason: str
    missing: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _has_a_reason(self) -> Self:
        if not self.reason.strip():
            raise ValueError("a gate that was not evaluated must say why")
        return self
