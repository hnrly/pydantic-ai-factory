"""Methodologies: Pydantic AI capabilities whose work the factory can check."""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar, cast

from pydantic_ai import CapabilityEvent, RunContext
from pydantic_ai.agent import AbstractAgent
from pydantic_ai.capabilities import AbstractCapability

from ..artifacts import Artifact
from ..contracts import DefinitionError, MethodologyRef, check_name, digest_of
from ..gates import Evidence, Gate, GateDecision, NotEvaluated, Verdict


class MethodologyDefinitionError(DefinitionError):
    """A methodology was declared, attached or used in a way that cannot work."""


@dataclass(kw_only=True)
class EvidenceRecorded(CapabilityEvent, namespace="pydantic_ai_factory", name="evidence"):
    """The event :meth:`Methodology.record` emits into the agent's event stream."""

    methodology: str
    version: str
    payload: Any


@dataclass(init=False)
class Methodology(AbstractCapability[Any]):
    """A Pydantic AI capability that can also be checked.

    Subclass it and set ``name`` and ``version``, list the payload types it records in
    ``evidence``, record them with :meth:`record` from any capability hook, and judge
    them in :meth:`judge`. On an agent run on its own it is an ordinary capability.
    Inside a station, ``ctx.run_agent`` stores the evidence it records, joins its gate to
    the station's and stamps its name and version on the records.

    ``output_retries`` is how many output retries its own hooks may use, by raising
    ``ModelRetry``. ``ctx.run_agent`` adds it to the agent's own output retry budget, so
    the methodology's rounds and ordinary validation retries do not starve each other.

    One instance serves every run, as for any capability. Keep per-run state out of it,
    or return a fresh copy from ``for_run()``.
    """

    name: ClassVar[str]
    version: ClassVar[str]
    evidence: ClassVar[tuple[type[Any], ...]] = ()
    output_retries: ClassVar[int] = 1

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        problems = _problems_with(cls)
        if problems:
            raise MethodologyDefinitionError(f"methodology {cls.__qualname__!r}", problems)

    async def record(self, ctx: RunContext[Any], payload: Any) -> None:
        """Record ``payload`` as evidence of this methodology at work.

        Call it from a capability hook or a tool this methodology adds. The evidence is
        emitted into the run's event stream as an :class:`EvidenceRecorded` event. When
        the agent runs in a station, the station also stores it and its gate reads it.
        On an agent run on its own, nothing else happens.

        Raises :class:`MethodologyDefinitionError` if ``payload`` is not one of the
        types listed in ``evidence``.
        """
        if not isinstance(payload, self.evidence):
            listed = ", ".join(kind.__name__ for kind in self.evidence) or "nothing"
            raise MethodologyDefinitionError(
                f"methodology {self.name!r}",
                [
                    f"it recorded a {type(payload).__name__}, but its evidence lists {listed}; "
                    "add the type to evidence"
                ],
            )
        event = await ctx.emit(
            EvidenceRecorded(methodology=self.name, version=self.version, payload=payload)
        )
        for capability in ctx.capabilities.values():
            if isinstance(capability, EvidenceCollector):
                capability.recorded.append(event)

    def judge(self, output: Artifact[Any], evidence: Evidence) -> Verdict:
        """Judge the station's output from the evidence. Plain code: no model, no I/O."""
        raise NotImplementedError  # every concrete methodology overrides it

    @property
    def ref(self) -> MethodologyRef:
        """The name and version, as stamped on records."""
        return MethodologyRef(name=self.name, version=self.version)

    @property
    def gate(self) -> Gate[Any]:
        """This methodology's gate: :meth:`judge`, requiring every type in ``evidence``."""
        return Gate(name=self.name, version=self.version, check=self.judge, requires=self.evidence)


def _problems_with(cls: type[Methodology]) -> list[str]:
    """What is wrong with a methodology class, checked when it is declared."""
    problems: list[str] = []
    for label in ("name", "version"):
        value: object = getattr(cls, label, None)
        if not isinstance(value, str):
            problems.append(f"set {label} to a string, such as {label} = '1'")
        elif problem := check_name(value):
            problems.append(f"the {label} {problem}")
    evidence: object = getattr(cls, "evidence", None)
    kinds: tuple[object, ...] = cast("tuple[object, ...]", evidence)
    if not isinstance(evidence, tuple) or not all(inspect.isclass(kind) for kind in kinds):
        problems.append("evidence must be a tuple of classes, such as (ScenarioCheck,)")
    retries: object = getattr(cls, "output_retries", None)
    if not isinstance(retries, int) or isinstance(retries, bool) or retries < 0:
        problems.append("output_retries must be a whole number, 0 or more")
    if cls.judge is Methodology.judge:
        problems.append("define judge(self, output, evidence) to return a Verdict")
    return problems


@dataclass
class EvidenceCollector(AbstractCapability[Any]):
    """Collects the evidence methodologies record during one agent run.

    ``ctx.run_agent`` adds a fresh one to each run it makes. It has no hooks: it only
    holds what :meth:`Methodology.record` hands it.
    """

    recorded: list[EvidenceRecorded] = field(default_factory=lambda: [])


def methodologies_of(agent: AbstractAgent[Any, Any]) -> tuple[Methodology, ...]:
    """Every methodology among ``agent``'s capabilities, in the order attached.

    Raises :class:`MethodologyDefinitionError` if two have the same name.
    """
    found: list[Methodology] = []
    agent.root_capability.apply(
        lambda capability: (
            found.append(capability)
            if isinstance(capability, Methodology) and not any(capability is m for m in found)
            else None
        )
    )
    names = [methodology.name for methodology in found]
    clashes = sorted({name for name in names if names.count(name) > 1})
    if clashes:
        raise MethodologyDefinitionError(
            f"agent {agent.name!r}",
            [f"two methodologies are named {name!r}; attach each once" for name in clashes],
        )
    return tuple(found)


def unique(methodologies: Sequence[Methodology]) -> tuple[Methodology, ...]:
    """Keep one methodology of each name and version, in order.

    Two agents in one station may each carry the same methodology. Its gate is joined
    once. Two different versions of one methodology in a station are refused.
    """
    kept: dict[str, Methodology] = {}
    for methodology in methodologies:
        seen = kept.get(methodology.name)
        if seen is None:
            kept[methodology.name] = methodology
        elif seen.version != methodology.version:
            raise MethodologyDefinitionError(
                f"methodology {methodology.name!r}",
                [
                    f"versions {seen.version!r} and {methodology.version!r} are both in one "
                    "station; use one version"
                ],
            )
    return tuple(kept.values())


def judge(
    station: str,
    own: Gate[Any] | None,
    methodologies: Sequence[Methodology],
    output: Artifact[Any],
    evidence: Evidence,
    *,
    stamps: Sequence[MethodologyRef] = (),
) -> GateDecision | NotEvaluated | None:
    """Judge ``output`` with the station's own gate joined to each methodology's gate.

    Any failure fails the station. Any escalation, with no failure, asks a person. Each
    reason names the gate it came from. If one part cannot be evaluated, for example
    because the evidence it requires was not recorded, nothing is judged and that
    part's :class:`NotEvaluated` is returned. With one part, its own decision is
    returned as it is; with none, ``None``.
    """
    parts = [*([own] if own is not None else []), *(m.gate for m in methodologies)]
    if not parts:
        return None
    now = datetime.now(UTC)
    decisions: list[GateDecision] = []
    for part in parts:
        judged = part.evaluate(output, evidence, methodologies=stamps, decided_at=now)
        if isinstance(judged, NotEvaluated):
            return judged
        decisions.append(judged)
    if len(decisions) == 1:
        return decisions[0]
    judgements = {decision.judgement for decision in decisions}
    judgement = (
        "failed" if "failed" in judgements else "escalate" if "escalate" in judgements else "passed"
    )
    reasons = tuple(
        f"{decision.gate}: {reason}"
        for decision in decisions
        if judgement == "passed" or decision.judgement == judgement
        for reason in decision.reasons
    )
    version = "+".join(f"{decision.gate}@{decision.version}" for decision in decisions)
    if len(version) > 200:
        # Too many parts to list in a name: fall back to a short digest of the list.
        version = f"{len(decisions)}-gates-{digest_of(version)[7:19]}"
    return GateDecision(
        gate=station,
        version=version,
        judgement=judgement,
        reasons=reasons,
        judged=tuple(dict.fromkeys(ref for decision in decisions for ref in decision.judged)),
        methodologies=tuple(stamps),
        decided_at=now,
    )
