"""Gates: plain code that judges recorded evidence and gives its reasons."""

from ._gate import (
    Evidence,
    Gate,
    GateDecision,
    GateDefinitionError,
    Judgement,
    NotEvaluated,
    Verdict,
    escalate,
    failed,
    gate,
    passed,
)

__all__ = [
    "Evidence",
    "Gate",
    "GateDecision",
    "GateDefinitionError",
    "Judgement",
    "NotEvaluated",
    "Verdict",
    "escalate",
    "failed",
    "gate",
    "passed",
]
