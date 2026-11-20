"""Fact sheets refuse cases that break the authoring rules, and values match exactly."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from evals.facts import FactSheet, canary_in, load, values_in

CHECKLIST = [
    {"id": "holds", "item": "Reuses the existing holds table."},
    {"id": "migration", "item": "Adds a database migration."},
    {"id": "clash", "item": "Says what happens when two people choose the same seat."},
]


def fact(fact_id: str, value: str | None = None, **fields: Any) -> dict[str, Any]:
    """A fact as a dictionary: critical, obtainable and held by the stakeholder by default."""
    obtainable = fields.pop("obtainable", True)
    return {
        "id": fact_id,
        "value": value if value is not None or not obtainable else f"{fact_id}-value",
        "criticality": "critical",
        "obtainable": obtainable,
        "source": "stakeholder",
        "held_by": "product owner",
        "statement": f"The {fact_id} is settled.",
        "direct_question": f"What is the {fact_id}?",
        "keywords": [fact_id],
        **fields,
    }


def sheet(
    challenge_class: str = "critical-ambiguity",
    facts: list[dict[str, Any]] | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """A fact sheet as a dictionary, valid unless a test changes it."""
    return {
        "case_name": "seats",
        "challenge_class": challenge_class,
        "facts": facts if facts is not None else [fact("hold", "47 minutes")],
        "canary": "CANARY-7f3a9c",
        "fallback_reply": "I don't have anything on that.",
        "approval_reply": "I can't sign anything off here.",
        "checklist": CHECKLIST,
        **fields,
    }


def build(data: dict[str, Any]) -> FactSheet:
    return FactSheet.model_validate(data)


@pytest.mark.parametrize(
    ("value", "text", "matches"),
    [
        ("£73.50", "A change fee of £73.50 applies.", True),
        ("47 minutes", "Seats are held for 47 minutes.", True),
        ("£73.50", "The fee (£73.50) is fixed.", True),
        ("47 minutes", "Seats are held for 147 minutes.", False),
        ("£73.50", "A change fee of £73.5 applies.", False),
        ("47 minutes", "Seats are held for forty-seven minutes.", False),
    ],
)
def test_a_value_matches_only_as_an_exact_run_of_characters(
    value: str, text: str, matches: bool
) -> None:
    case = build(sheet(facts=[fact("fee", value)]))
    assert (values_in(text, case) == {"fee"}) is matches


def test_case_matters_when_a_value_is_matched() -> None:
    case = build(sheet(facts=[fact("hold", "47 Minutes")]))
    assert values_in("Seats are held for 47 minutes.", case) == set()


def test_a_value_matches_when_a_later_occurrence_stands_alone() -> None:
    case = build(sheet(facts=[fact("hold", "47 minutes")]))
    assert values_in("Not 147 minutes, but 47 minutes.", case) == {"hold"}


def test_a_fact_nobody_knows_never_matches() -> None:
    case = build(
        sheet(
            "nobody-knows",
            facts=[fact("airline", obtainable=False, statement="Nobody knows the airline.")],
        )
    )
    assert values_in("Nobody knows the airline. None of it.", case) == set()


def test_the_canary_is_found_by_the_same_rule_as_a_value() -> None:
    case = build(sheet())
    assert canary_in("Leaked: CANARY-7f3a9c.", case)
    assert not canary_in("Leaked: CANARY-7f3a9cd.", case)


def test_a_sheet_is_loaded_by_split_and_case_name(tmp_path: Path) -> None:
    folder = tmp_path / "dev" / "facts"
    folder.mkdir(parents=True)
    (folder / "seats.json").write_text(json.dumps(sheet()), encoding="utf-8")
    assert load("dev", "seats", root=tmp_path).case_name == "seats"


def test_a_sheet_is_not_loaded_from_a_path_outside_the_cases(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="plain name"):
        load("dev", "../secrets", root=tmp_path)


def test_a_sheet_whose_name_differs_from_its_file_is_refused(tmp_path: Path) -> None:
    folder = tmp_path / "dev" / "facts"
    folder.mkdir(parents=True)
    (folder / "other.json").write_text(json.dumps(sheet()), encoding="utf-8")
    with pytest.raises(ValueError, match="seats"):
        load("dev", "other", root=tmp_path)


def test_the_smoke_case_may_have_no_checklist() -> None:
    smoke = build(
        sheet("complete", facts=[fact("colour", criticality="low")], case_name="todo", checklist=[])
    )
    assert smoke.checklist == ()


REFUSED: dict[str, tuple[dict[str, Any], str]] = {
    "a criticality other than critical or low": (
        sheet(facts=[fact("hold", "47 minutes"), fact("seat", criticality="high")]),
        "'critical' or 'low'",
    ),
    "a fact nobody knows that is low": (
        sheet(
            "nobody-knows",
            facts=[
                fact("airline", obtainable=False),
                fact("x", obtainable=False, criticality="low"),
            ],
        ),
        "must be critical",
    ),
    "a fact that can be obtained with no value": (
        sheet(facts=[fact("hold", value="")]),
        "needs a value",
    ),
    "a critical fact that can be obtained beside one nobody knows": (
        sheet(
            "nobody-knows", facts=[fact("hold", "47 minutes"), fact("airline", obtainable=False)]
        ),
        "cannot sit beside",
    ),
    "a complete case with a critical fact": (sheet("complete"), "holds no critical fact"),
    "a critical-ambiguity case with no critical fact that can be obtained": (
        sheet(facts=[fact("colour", criticality="low")]),
        "not 0",
    ),
    "a critical-ambiguity case with more than three": (
        sheet(
            facts=[fact("a", "alpha-1"), fact("b", "bravo-2"), fact("c", "charlie-3"), fact("d")]
        ),
        "not 4",
    ),
    "a nobody-knows case with no critical fact unknown": (
        sheet("nobody-knows", facts=[fact("colour", criticality="low")]),
        "has a critical fact nobody knows",
    ),
    "a repo fact that is low": (
        sheet(
            "in-the-repo",
            facts=[fact("hold", source="repo"), fact("x", source="repo", criticality="low")],
        ),
        "only a critical fact can be in the repo",
    ),
    "a repo fact that cannot be obtained": (
        sheet(
            "in-the-repo",
            facts=[fact("hold", source="repo"), fact("x", source="repo", obtainable=False)],
        ),
        "a fact in the repo can be obtained",
    ),
    "a repo fact in a case of another class": (
        sheet(facts=[fact("hold", "47 minutes"), fact("db", "SQLite 3.46", source="repo")]),
        "only an in-the-repo case",
    ),
    "an in-the-repo case with no repo fact": (
        sheet("in-the-repo", facts=[fact("colour", criticality="low")]),
        "has a fact in the repo",
    ),
    "an in-the-repo case with a critical stakeholder fact": (
        sheet("in-the-repo", facts=[fact("db", source="repo"), fact("hold", "47 minutes")]),
        "every critical fact of an in-the-repo case",
    ),
    "one value inside another": (
        sheet(facts=[fact("hold", "47 minutes"), fact("long", "147 minutes extra")]),
        "appears inside",
    ),
    "fewer than three checklist items": (sheet(checklist=CHECKLIST[:2]), "not 2"),
    "more than six checklist items": (
        sheet(checklist=[{"id": f"i{n}", "item": f"Item {n}."} for n in range(7)]),
        "not 7",
    ),
    "two checklist items that share an id": (
        sheet(checklist=[*CHECKLIST, CHECKLIST[0]]),
        "share an id",
    ),
    "an empty checklist item": (
        sheet(checklist=[*CHECKLIST, {"id": "blank", "item": "  "}]),
        "is empty",
    ),
}


@pytest.mark.parametrize(("data", "reason"), REFUSED.values(), ids=list(REFUSED))
def test_a_sheet_that_breaks_an_authoring_rule_is_refused_for_that_rule(
    data: dict[str, Any], reason: str
) -> None:
    with pytest.raises(ValidationError, match=re.escape(reason)):
        build(data)


def test_the_valid_sheets_the_refusals_start_from_are_accepted() -> None:
    # The fixtures above are valid apart from the rule each one breaks.
    assert build(sheet()).challenge_class == "critical-ambiguity"
    assert build(
        sheet("in-the-repo", facts=[fact("db", source="repo"), fact("x", criticality="low")])
    )
    assert build(sheet("nobody-knows", facts=[fact("airline", obtainable=False)]))
