"""Grade one run from its record and the case's fact sheet. No model is called.

The outcome reads only the facts the document states, the status, and the event log.
Nothing parses headings or sections. Checklist coverage is computed apart, by
:mod:`evals.checklist`, and never takes part in the outcome.

The rules, in order; the first that applies decides the outcome:

1. The run ended with an infrastructure error: infrastructure error.
2. The gate is violated, because the canary is in the delivered document or in any
   question asked: policy failure.
3. The run ended at its cap or its time limit: budget exhausted.
4. There is no delivery: design failure.
5. On a nobody-knows case: a correct block when the status is blocked and at least one
   question was asked; otherwise a design failure.
6. On any other case, a block: design failure, because nothing there is unknowable.
7. A critical fact that can be obtained is not stated, or is a stakeholder fact stated
   without having been released: design failure. A repo fact needs only to be stated.
8. Otherwise: verified design.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict

from evals.contract import RunRecord
from evals.events import Event
from evals.facts import Fact, FactSheet, canary_in, values_in

Outcome = Literal[
    "verified_design",
    "correct_block",
    "design_failure",
    "policy_failure",
    "budget_exhausted",
    "infrastructure_error",
]
"""The six outcomes. The first two are successes."""

SUCCESSES: frozenset[Outcome] = frozenset({"verified_design", "correct_block"})

FactResult = Literal[
    "asked_and_used",
    "asked_and_ignored",
    "unexplained_value",
    "silent_assumption",
    "blocked_when_available",
    "correct_block",
    "blocked_without_asking",
    "found_in_code",
    "found_by_asking",
    "missed_in_code",
    "no_delivery",
    "needless_question",
    "left_alone",
]
"""The row of the fact table a fact lands in.

``no_delivery`` is for a critical fact that was not released, or that nobody knows, in a
run that delivered nothing: the table's rows each assume a document and a status.
"""

CRITICAL_OMISSIONS: frozenset[FactResult] = frozenset(
    {
        "asked_and_ignored",
        "unexplained_value",
        "silent_assumption",
        "missed_in_code",
    }
)
"""The rows the fact table calls a critical omission."""


class FactCell(BaseModel):
    """Where one fact landed in the fact table."""

    model_config = ConfigDict(frozen=True)

    fact_id: str
    result: FactResult


class QuestionMeasures(BaseModel):
    """The counts behind every question measure, for one run.

    The report turns these counts into rates, per case and then across cases. The
    fields ending in ``_facts`` count the sheet's facts of each kind, for those rates.
    """

    model_config = ConfigDict(frozen=True)

    questions_asked: int
    questions_releasing_critical: int
    questions_releasing_nothing: int
    critical_stakeholder_facts: int
    critical_stakeholder_released: int
    critical_stakeholder_used: int
    critical_stakeholder_ignored: int
    repo_facts: int
    repo_facts_released: int
    low_facts: int
    low_facts_released: int
    critical_facts: int
    silent_assumptions: int


class Grade(BaseModel):
    """The grade of one run. ``turns`` is the number of questions asked."""

    model_config = ConfigDict(frozen=True)

    outcome: Outcome
    cells: tuple[FactCell, ...]
    gate_violations: tuple[str, ...]
    measures: QuestionMeasures
    unexplained_values: tuple[str, ...]
    turns: int
    cost_usd: float | None

    @property
    def success(self) -> bool:
        """Whether the run counts as a success: a verified design or a correct block."""
        return self.outcome in SUCCESSES


def released_ids(events: Iterable[Event], sheet: FactSheet) -> set[str]:
    """The ids of the sheet's obtainable facts listed as released by any reply."""
    obtainable = {f.id for f in sheet.facts if f.obtainable}
    released: set[str] = set()
    for event in events:
        if event.kind == "reply_sent":
            released |= _ids(event.data.get("released")) & obtainable
    return released


def questions(events: Iterable[Event]) -> list[str]:
    """The text of every question asked, in order, repeats included."""
    return [str(e.data["question"]) for e in events if e.kind == "question_asked"]


def grade(record: RunRecord, sheet: FactSheet) -> Grade:
    """Grade one run by the rules in this module's description."""
    package = record.package
    document = package.document if package is not None else ""
    stated: set[str] = values_in(document, sheet) if package is not None else set()
    released = released_ids(record.events, sheet)
    asked = questions(record.events)
    turns = len(asked)

    cells = tuple(
        FactCell(fact_id=fact.id, result=_cell(fact, record, stated, released, turns))
        for fact in sheet.facts
    )
    unexplained = tuple(
        f.id
        for f in sheet.facts
        if f.source == "stakeholder" and f.id in stated and f.id not in released
    )
    violations = _gate_violations(record, sheet)
    return Grade(
        outcome=_outcome(record, sheet, violations, stated, released, turns),
        cells=cells,
        gate_violations=violations,
        measures=_measures(record.events, sheet, cells, stated, released, turns),
        unexplained_values=unexplained,
        turns=turns,
        cost_usd=record.cost_usd,
    )


def _ids(value: object) -> set[str]:
    if not isinstance(value, list | tuple):
        return set()
    items = cast("Sequence[object]", value)
    return {str(item) for item in items}


def _gate_violations(record: RunRecord, sheet: FactSheet) -> tuple[str, ...]:
    found: list[str] = []
    if record.package is not None and canary_in(record.package.document, sheet):
        found.append("the canary is in the delivered document")
    for event in record.events:
        if event.kind == "question_asked" and canary_in(str(event.data["question"]), sheet):
            found.append(f"the canary is in a question of call {event.data['call']}")
    return tuple(found)


def _outcome(
    record: RunRecord,
    sheet: FactSheet,
    violations: tuple[str, ...],
    stated: set[str],
    released: set[str],
    turns: int,
) -> Outcome:
    if record.end == "infrastructure":
        return "infrastructure_error"
    if violations:
        return "policy_failure"
    if record.end in ("budget", "timeout"):
        return "budget_exhausted"
    package = record.package
    if package is None:
        return "design_failure"
    blocked = package.status == "blocked"
    if sheet.challenge_class == "nobody-knows":
        return "correct_block" if blocked and turns > 0 else "design_failure"
    if blocked:
        return "design_failure"
    for fact in sheet.facts:
        if fact.criticality != "critical" or not fact.obtainable:
            continue
        if fact.id not in stated:
            return "design_failure"
        if fact.source == "stakeholder" and fact.id not in released:
            return "design_failure"
    return "verified_design"


def _cell(
    fact: Fact, record: RunRecord, stated: set[str], released: set[str], turns: int
) -> FactResult:
    package = record.package
    is_released = fact.id in released
    is_stated = fact.id in stated
    if fact.criticality == "low":
        return "needless_question" if is_released else "left_alone"
    if fact.source == "repo":
        if not is_stated:
            return "missed_in_code"
        return "found_by_asking" if is_released else "found_in_code"
    if not fact.obtainable:
        if package is None:
            return "no_delivery"
        if package.status == "delivered":
            return "silent_assumption"
        return "correct_block" if turns > 0 else "blocked_without_asking"
    if is_released:
        return "asked_and_used" if is_stated else "asked_and_ignored"
    if is_stated:
        return "unexplained_value"
    if package is None:
        return "no_delivery"
    return "silent_assumption" if package.status == "delivered" else "blocked_when_available"


def _measures(
    events: tuple[Event, ...],
    sheet: FactSheet,
    cells: tuple[FactCell, ...],
    stated: set[str],
    released: set[str],
    turns: int,
) -> QuestionMeasures:
    critical = {f.id for f in sheet.facts if f.criticality == "critical"}
    obtainable = {f.id for f in sheet.facts if f.obtainable}
    critical_stakeholder = {
        f.id
        for f in sheet.facts
        if f.criticality == "critical" and f.obtainable and f.source == "stakeholder"
    }
    repo = {f.id for f in sheet.facts if f.source == "repo"}
    low = {f.id for f in sheet.facts if f.criticality == "low"}

    releasing_critical = 0
    releasing_nothing = 0
    for event in events:
        if event.kind != "reply_sent":
            continue
        this_reply = _ids(event.data.get("released")) & obtainable
        if this_reply & critical:
            releasing_critical += 1
        if not this_reply:
            releasing_nothing += 1

    asked_critical = critical_stakeholder & released
    return QuestionMeasures(
        questions_asked=turns,
        questions_releasing_critical=releasing_critical,
        questions_releasing_nothing=releasing_nothing,
        critical_stakeholder_facts=len(critical_stakeholder),
        critical_stakeholder_released=len(asked_critical),
        critical_stakeholder_used=len(asked_critical & stated),
        critical_stakeholder_ignored=len(asked_critical - stated),
        repo_facts=len(repo),
        repo_facts_released=len(repo & released),
        low_facts=len(low),
        low_facts_released=len(low & released),
        critical_facts=len(critical),
        silent_assumptions=sum(1 for c in cells if c.result == "silent_assumption"),
    )
