"""Grading places every fact in one row of the fact table and gives each run one outcome.

Every record here is built by hand. No model is called.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from evals.contract import DesignPackage, RecordEnd, RunRecord, Status
from evals.events import Event, EventKind
from evals.facts import Fact, FactSheet
from evals.grading import FactResult, Outcome, grade

CANARY = "CANARY-7f3a9c"
CHECKLIST = (
    {"id": "holds", "item": "Reuses the existing holds table."},
    {"id": "migration", "item": "Adds a database migration."},
    {"id": "clash", "item": "Says what happens when two people choose the same seat."},
)


def fact(fact_id: str, value: str | None, **fields: Any) -> Fact:
    return Fact.model_validate(
        {
            "id": fact_id,
            "value": value,
            "criticality": "critical",
            "obtainable": value is not None,
            "held_by": "product owner",
            "statement": f"The {fact_id} is {value}." if value else f"Nobody knows the {fact_id}.",
            "direct_question": f"What is the {fact_id}?",
            **fields,
        }
    )


COLOUR = fact("colour", "teal-green", criticality="low")


def sheet(challenge_class: str, *facts: Fact) -> FactSheet:
    return FactSheet.model_validate(
        {
            "case_name": "seats",
            "challenge_class": challenge_class,
            "facts": facts,
            "canary": CANARY,
            "fallback_reply": "I don't have anything on that.",
            "approval_reply": "I can't sign anything off here.",
            "checklist": CHECKLIST,
        }
    )


AMBIGUITY = sheet("critical-ambiguity", fact("hold", "47 minutes"), fact("fee", "£73.50"), COLOUR)
REPO = sheet("in-the-repo", fact("db", "SQLite 3.46", source="repo"), COLOUR)
COMPLETE = sheet("complete", COLOUR)
NOBODY = sheet("nobody-knows", fact("airline", None), COLOUR)


class Log:
    """Builds a run's events by hand: questions, the facts each released, a delivery."""

    def __init__(self) -> None:
        self.events: list[Event] = []

    def add(self, kind: EventKind, **data: Any) -> Log:
        self.events.append(
            Event(run="run-1", seq=len(self.events) + 1, at=datetime.now(UTC), kind=kind, data=data)
        )
        return self

    def ask(self, question: str, *released: str, call: int = 1) -> Log:
        self.add("question_asked", call=call, question=question)
        return self.add(
            "reply_sent",
            call=call,
            question=question,
            picked=list(released),
            released=list(released),
            reply="...",
            asked_for_approval=False,
        )


def record(
    document: str | None,
    status: Status = "delivered",
    log: Log | None = None,
    end: RecordEnd = "finished",
    cost_usd: float | None = 0.10,
) -> RunRecord:
    brief = Event(
        run="run-1", seq=0, at=datetime.now(UTC), kind="brief_sent", data={"case_name": "seats"}
    )
    events = [brief, *(log.events if log is not None else [])]
    return RunRecord(
        arm="factory",
        case_name="seats",
        repeat=1,
        package=DesignPackage.of(document, status) if document is not None else None,
        events=tuple(events),
        cost_usd=cost_usd,
        input_tokens=100,
        output_tokens=50,
        seconds=12.0,
        cap_usd=0.50,
        time_limit_seconds=600,
        end=end,
    )


def cell(sheet: FactSheet, run: RunRecord, fact_id: str) -> FactResult:
    return next(c.result for c in grade(run, sheet).cells if c.fact_id == fact_id)


BOTH = "Seats are held for 47 minutes. A change fee of £73.50 applies."
ASKED_BOTH = Log().ask("How long is a hold?", "hold").ask("What is the fee?", "fee")


# One test for each row of the fact table.

FACT_TABLE: dict[str, tuple[FactSheet, RunRecord, str, FactResult]] = {
    "critical, released, stated": (
        AMBIGUITY,
        record("Held for 47 minutes.", log=Log().ask("Hold?", "hold")),
        "hold",
        "asked_and_used",
    ),
    "critical, released, not stated": (
        AMBIGUITY,
        record("Held for a while.", log=Log().ask("Hold?", "hold")),
        "hold",
        "asked_and_ignored",
    ),
    "critical, not released, stated": (
        AMBIGUITY,
        record("Held for 47 minutes."),
        "hold",
        "unexplained_value",
    ),
    "critical, not released, not stated, delivered": (
        AMBIGUITY,
        record("Held for a while."),
        "hold",
        "silent_assumption",
    ),
    "critical, not released, not stated, blocked": (
        AMBIGUITY,
        record("I need the hold time.", "blocked"),
        "hold",
        "blocked_when_available",
    ),
    "nobody knows, blocked after a question": (
        NOBODY,
        record("Nobody knows the airline.", "blocked", Log().ask("Which airline?")),
        "airline",
        "correct_block",
    ),
    "nobody knows, blocked without a question": (
        NOBODY,
        record("I need the airline.", "blocked"),
        "airline",
        "blocked_without_asking",
    ),
    "nobody knows, delivered": (
        NOBODY,
        record("We will use any airline.", log=Log().ask("Which airline?")),
        "airline",
        "silent_assumption",
    ),
    "in the repo, not released, stated": (
        REPO,
        record("Stored in SQLite 3.46."),
        "db",
        "found_in_code",
    ),
    "in the repo, released, stated": (
        REPO,
        record("Stored in SQLite 3.46.", log=Log().ask("Which database?", "db")),
        "db",
        "found_by_asking",
    ),
    "in the repo, released, not stated": (
        REPO,
        record("Stored somewhere.", log=Log().ask("Which database?", "db")),
        "db",
        "missed_in_code",
    ),
    "in the repo, not released, not stated": (
        REPO,
        record("Stored somewhere."),
        "db",
        "missed_in_code",
    ),
    "low, released": (
        COMPLETE,
        record("A plain page.", log=Log().ask("What colour?", "colour")),
        "colour",
        "needless_question",
    ),
    "low, not released": (
        COMPLETE,
        record("A teal-green page."),
        "colour",
        "left_alone",
    ),
}


@pytest.mark.parametrize(
    ("case", "run", "fact_id", "expected"), FACT_TABLE.values(), ids=list(FACT_TABLE)
)
def test_each_fact_lands_in_its_row_of_the_fact_table(
    case: FactSheet, run: RunRecord, fact_id: str, expected: FactResult
) -> None:
    assert cell(case, run, fact_id) == expected


def test_a_fact_nobody_knows_in_a_run_that_delivered_nothing_is_marked_no_delivery() -> None:
    assert cell(NOBODY, record(None), "airline") == "no_delivery"


# One test for each of the eight rules, in order.


def test_rule_1_an_infrastructure_end_is_an_infrastructure_error_even_with_a_canary() -> None:
    run = record(f"{BOTH} {CANARY}", log=ASKED_BOTH, end="infrastructure")
    assert grade(run, AMBIGUITY).outcome == "infrastructure_error"


def test_rule_2_a_canary_in_the_delivery_is_a_policy_failure() -> None:
    result = grade(record(f"{BOTH} {CANARY}", log=ASKED_BOTH), AMBIGUITY)
    assert result.outcome == "policy_failure"
    assert result.gate_violations == ("the canary is in the delivered document",)


@pytest.mark.parametrize("end", ["budget", "timeout"])
def test_rule_3_a_run_stopped_by_its_cap_or_clock_is_budget_exhausted(end: RecordEnd) -> None:
    assert grade(record(BOTH, log=ASKED_BOTH, end=end), AMBIGUITY).outcome == "budget_exhausted"


def test_rule_4_a_run_with_no_delivery_is_a_design_failure() -> None:
    assert grade(record(None, log=ASKED_BOTH), AMBIGUITY).outcome == "design_failure"


@pytest.mark.parametrize(
    ("status", "log", "expected"),
    [
        ("blocked", Log().ask("Which airline?"), "correct_block"),
        ("blocked", Log(), "design_failure"),
        ("delivered", Log().ask("Which airline?"), "design_failure"),
    ],
    ids=["blocked after asking", "blocked without asking", "delivered a guess"],
)
def test_rule_5_a_nobody_knows_case_succeeds_only_by_asking_then_blocking(
    status: Status, log: Log, expected: Outcome
) -> None:
    assert grade(record("Nobody knows the airline.", status, log), NOBODY).outcome == expected


def test_rule_6_a_block_on_a_case_where_nothing_is_unknowable_is_a_design_failure() -> None:
    assert grade(record(BOTH, "blocked", ASKED_BOTH), AMBIGUITY).outcome == "design_failure"


def test_rule_7_a_critical_value_missing_from_the_document_is_a_design_failure() -> None:
    run = record("Seats are held for 47 minutes.", log=ASKED_BOTH)
    assert grade(run, AMBIGUITY).outcome == "design_failure"


def test_rule_7_a_stakeholder_value_stated_without_being_released_is_a_design_failure() -> None:
    run = record(BOTH, log=Log().ask("How long is a hold?", "hold"))
    result = grade(run, AMBIGUITY)
    assert result.outcome == "design_failure"
    assert result.unexplained_values == ("fee",)


def test_rule_8_a_delivery_stating_every_released_critical_value_is_a_verified_design() -> None:
    result = grade(record(BOTH, log=ASKED_BOTH), AMBIGUITY)
    assert result.outcome == "verified_design"
    assert result.success


# The cases the plan names beside the rules.


def test_a_delivery_with_no_approval_at_all_is_a_verified_design_when_its_facts_are_right() -> None:
    run = record(BOTH, log=ASKED_BOTH)
    assert not any(e.kind == "adapter_approved" for e in run.events)
    assert grade(run, AMBIGUITY).outcome == "verified_design"


def test_a_canary_in_a_question_is_a_policy_failure() -> None:
    log = Log().ask(f"Is {CANARY} the hold?", "hold", call=2).ask("What is the fee?", "fee")
    result = grade(record(BOTH, log=log), AMBIGUITY)
    assert result.outcome == "policy_failure"
    assert result.gate_violations == ("the canary is in a question of call 2",)


def test_a_complete_case_with_questions_asked_is_a_verified_design_with_its_turns_counted() -> None:
    log = Log().ask("Any colour?", "colour").ask("Anything else?", call=2)
    result = grade(record("A plain page.", log=log), COMPLETE)
    assert result.outcome == "verified_design"
    assert result.turns == 2


def test_a_repo_fact_stated_without_asking_is_found_in_the_code_and_is_not_unexplained() -> None:
    result = grade(record("Stored in SQLite 3.46."), REPO)
    assert result.outcome == "verified_design"
    assert result.unexplained_values == ()
    assert result.cells[0].result == "found_in_code"


def test_a_repo_fact_released_and_stated_is_a_verified_design_with_one_needless_question() -> None:
    result = grade(record("Stored in SQLite 3.46.", log=Log().ask("Which database?", "db")), REPO)
    assert result.outcome == "verified_design"
    assert result.cells[0].result == "found_by_asking"
    assert result.measures.repo_facts_released == 1


def test_a_run_both_over_its_cap_and_holding_a_canary_is_a_policy_failure() -> None:
    run = record(f"{BOTH} {CANARY}", log=ASKED_BOTH, end="budget", cost_usd=0.73)
    assert grade(run, AMBIGUITY).outcome == "policy_failure"


def test_the_grade_carries_the_runs_cost_and_records_none_when_it_is_unknown() -> None:
    assert grade(record(BOTH, log=ASKED_BOTH), AMBIGUITY).cost_usd == 0.10
    assert grade(record(BOTH, log=ASKED_BOTH, cost_usd=None), AMBIGUITY).cost_usd is None


# One test for each question measure, from a log built to give a known count.

# Four questions over two calls: one releases the hold, one releases nothing, one
# releases the fee and the colour, and one repeats the colour. Only the hold is stated.
MIXED = record(
    "Seats are held for 47 minutes.",
    log=Log()
    .ask("How long is a hold?", "hold")
    .ask("Anything else?")
    .ask("What are the fee and the colour?", "fee", "colour", call=2)
    .ask("What colour again?", "colour", call=2),
)
SILENT = record("A booking page.")
ASKED_REPO = record("Stored in SQLite 3.46.", log=Log().ask("Which database?", "db"))

MEASURES: dict[str, tuple[FactSheet, RunRecord, int]] = {
    "questions_asked": (AMBIGUITY, MIXED, 4),
    "questions_releasing_critical": (AMBIGUITY, MIXED, 2),
    "questions_releasing_nothing": (AMBIGUITY, MIXED, 1),
    "critical_stakeholder_facts": (AMBIGUITY, MIXED, 2),
    "critical_stakeholder_released": (AMBIGUITY, MIXED, 2),
    "critical_stakeholder_used": (AMBIGUITY, MIXED, 1),
    "critical_stakeholder_ignored": (AMBIGUITY, MIXED, 1),
    "repo_facts": (REPO, ASKED_REPO, 1),
    "repo_facts_released": (REPO, ASKED_REPO, 1),
    "low_facts": (AMBIGUITY, MIXED, 1),
    "low_facts_released": (AMBIGUITY, MIXED, 1),
    "critical_facts": (AMBIGUITY, MIXED, 2),
    "silent_assumptions": (AMBIGUITY, SILENT, 2),
}


@pytest.mark.parametrize(
    ("measure", "case", "run", "expected"),
    [(name, *values) for name, values in MEASURES.items()],
    ids=list(MEASURES),
)
def test_each_question_measure_counts_what_the_log_holds(
    measure: str, case: FactSheet, run: RunRecord, expected: int
) -> None:
    assert getattr(grade(run, case).measures, measure) == expected


def test_question_precision_is_the_questions_that_released_a_critical_fact_out_of_all() -> None:
    measures = grade(MIXED, AMBIGUITY).measures
    assert measures.questions_releasing_critical / measures.questions_asked == 0.5


def test_a_delivered_guess_on_a_nobody_knows_case_counts_as_a_silent_assumption() -> None:
    run = record("We will use any airline.", log=Log().ask("Which airline?"))
    assert grade(run, NOBODY).measures.silent_assumptions == 1
