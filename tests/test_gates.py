"""A gate is plain code over evidence. Missing evidence is never a pass."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from pydantic_ai_factory import (
    Artifact,
    Evidence,
    Gate,
    GateDecision,
    GateDefinitionError,
    MethodologyRef,
    NotEvaluated,
    Verdict,
    failed,
    gate,
    needs_approval,
    passed,
)

WHEN = datetime(2026, 1, 2, tzinfo=UTC)


class Design(BaseModel):
    summary: str


class Review(BaseModel):
    blockers: list[str] = []


class Lint(BaseModel):
    clean: bool


DESIGN = Artifact.create(Design(summary="Split the bill evenly"), created_at=WHEN)
CLEAN_REVIEW = Artifact.create(Review(), created_at=WHEN)
BLOCKED_REVIEW = Artifact.create(Review(blockers=["no rounding rule"]), created_at=WHEN)


@gate(version="2", requires=[Review])
def review_gate(output: Artifact[Design], evidence: Evidence) -> Verdict:
    blockers = evidence.one(Review).payload.blockers
    if blockers:
        return failed(*blockers)
    return passed(f"{output.payload.summary!r} has no open blockers")


def test_a_gate_takes_its_name_from_the_function() -> None:
    assert (review_gate.name, review_gate.version) == ("review_gate", "2")
    assert review_gate.requires == (Review,)


def test_a_passing_decision_names_the_gate_its_version_reasons_and_what_it_judged() -> None:
    decision = review_gate.evaluate(
        DESIGN,
        Evidence([CLEAN_REVIEW]),
        methodologies=[MethodologyRef(name="review", version="1")],
        decided_at=WHEN,
    )

    assert decision == GateDecision(
        gate="review_gate",
        version="2",
        judgement="passed",
        reasons=("'Split the bill evenly' has no open blockers",),
        judged=(DESIGN.ref, CLEAN_REVIEW.ref),
        methodologies=(MethodologyRef(name="review", version="1"),),
        decided_at=WHEN,
    )


def test_a_failing_decision_carries_every_reason() -> None:
    decision = review_gate.evaluate(DESIGN, Evidence([BLOCKED_REVIEW]))

    assert isinstance(decision, GateDecision)
    assert (decision.judgement, decision.reasons) == ("failed", ("no rounding rule",))
    assert decision.judged == (DESIGN.ref, BLOCKED_REVIEW.ref)


def test_a_gate_can_ask_for_a_person() -> None:
    @gate(version="1")
    def sign_off(output: Artifact[Design], evidence: Evidence) -> Verdict:
        return needs_approval("ready for sign-off")

    decision = sign_off.evaluate(DESIGN, Evidence())

    assert isinstance(decision, GateDecision)
    assert decision.judgement == "needs_approval"


def test_missing_evidence_is_not_evaluated_and_the_check_is_never_called() -> None:
    calls: list[str] = []

    @gate(version="1", requires=[Review, Lint])
    def strict(output: Artifact[Design], evidence: Evidence) -> Verdict:
        calls.append("called")
        return passed("ok")

    result = strict.evaluate(DESIGN, Evidence([CLEAN_REVIEW]))

    assert result == NotEvaluated(
        gate="strict", version="1", reason="missing evidence: Lint", missing=("Lint",)
    )
    assert calls == []


def test_a_check_that_raises_is_not_evaluated_rather_than_failed() -> None:
    result = review_gate.evaluate(DESIGN, Evidence([CLEAN_REVIEW, BLOCKED_REVIEW]))

    assert isinstance(result, NotEvaluated)
    assert result.reason == (
        "the check raised LookupError: expected one Review in evidence, found 2"
    )


def test_a_check_that_returns_something_else_is_not_evaluated() -> None:
    @gate(version="1")
    def sloppy(output: Artifact[Design], evidence: Evidence) -> Verdict:
        return True  # type: ignore[return-value]

    result = sloppy.evaluate(DESIGN, Evidence())

    assert isinstance(result, NotEvaluated)
    assert result.reason == "the check returned bool, not a verdict"


def test_every_verdict_and_decision_must_say_why() -> None:
    for judgement in ("passed", "failed", "needs_approval"):
        with pytest.raises(ValidationError):
            Verdict(judgement=judgement, reasons=())
        with pytest.raises(ValidationError):
            GateDecision(
                gate="g",
                version="1",
                judgement=judgement,
                reasons=(),
                judged=(DESIGN.ref,),
                decided_at=WHEN,
            )


def test_a_decision_must_name_what_it_judged() -> None:
    with pytest.raises(ValidationError):
        GateDecision(
            gate="g",
            version="1",
            judgement="passed",
            reasons=("ok",),
            judged=(),
            decided_at=WHEN,
        )


def test_a_lambda_gate_needs_a_name() -> None:
    with pytest.raises(GateDefinitionError, match="needs a name"):
        gate(version="1")(lambda output, evidence: passed("ok"))

    named: Gate[Design] = gate(version="1", name="always")(lambda output, evidence: passed("ok"))
    assert named.name == "always"


def test_evidence_finds_artifacts_by_payload_type() -> None:
    evidence = Evidence([DESIGN, CLEAN_REVIEW, BLOCKED_REVIEW, CLEAN_REVIEW])

    assert len(evidence) == 3
    assert [a.ref for a in evidence.of(Review)] == [CLEAN_REVIEW.ref, BLOCKED_REVIEW.ref]
    assert evidence.one(Design).ref == DESIGN.ref
    assert evidence.of(Lint) == []
    with pytest.raises(LookupError, match="expected one Lint in evidence, found 0"):
        evidence.one(Lint)


def test_the_output_does_not_count_as_its_own_evidence() -> None:
    @gate(version="1", requires=[Design])
    def needs_a_second_design(output: Artifact[Design], evidence: Evidence) -> Verdict:
        return passed("ok")

    result = needs_a_second_design.evaluate(DESIGN, Evidence())

    assert isinstance(result, NotEvaluated)
    assert result.missing == ("Design",)


def test_evidence_of_a_subclass_satisfies_a_requirement_for_its_base() -> None:
    class DeepReview(Review):
        depth: int = 2

    deep = Artifact.create(DeepReview(), created_at=WHEN)

    decision = review_gate.evaluate(DESIGN, Evidence([deep]))

    assert isinstance(decision, GateDecision)
    assert decision.judgement == "passed"


def test_a_badly_declared_gate_is_refused_with_the_reason() -> None:
    def check(output: Artifact[Design], evidence: Evidence) -> Verdict:
        return passed("ok")

    async def async_check(output: Artifact[Design], evidence: Evidence) -> Verdict:
        return passed("ok")

    declare: Any = gate
    cases: list[tuple[dict[str, Any], Any, str]] = [
        ({"version": ""}, check, "the version must be non-empty"),
        ({"version": "1", "name": " x"}, check, "the name must be non-empty"),
        ({"version": "1"}, async_check, "must be a plain function, not async"),
        ({"version": "1", "requires": ["Review"]}, check, "requires must hold classes"),
        ({"version": "1", "requires": Review}, check, "requires must be a list"),
    ]
    for arguments, function, message in cases:
        with pytest.raises(GateDefinitionError, match=message):
            declare(**arguments)(function)


def test_a_check_that_raises_is_logged_with_its_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("ERROR", logger="pydantic_ai_factory"):
        review_gate.evaluate(DESIGN, Evidence([CLEAN_REVIEW, BLOCKED_REVIEW]))

    (record,) = caplog.records
    assert "gate 'review_gate' raised" in record.getMessage()
    assert record.exc_info is not None
