"""The evaluation set scores designs with plain code, and compares review on with review off."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic_evals.evaluators import EvaluatorContext

from evals import design as evals
from pydantic_ai_factory_software import Design

pytestmark = pytest.mark.anyio

EXPECTED = evals.Expectation(must_cover=("add", "tick"))


def result(*criteria: str, behaviour: str = "Type a task and add it.") -> evals.DesignResult:
    design = Design(
        title="To-do list",
        summary="Keep track of tasks.",
        behaviour=[behaviour],
        acceptance_criteria=list(criteria),
    )
    return evals.DesignResult(
        design=design, outcome="passed", attempts=1, rounds=0, open_findings=0
    )


def score(
    evaluator: Any, output: evals.DesignResult, expected: evals.Expectation = EXPECTED
) -> Any:
    fields: dict[str, Any] = {
        "name": "case",
        "inputs": "A to-do list web page.",
        "metadata": expected,
        "expected_output": None,
        "output": output,
        "duration": 0.0,
        "_span_tree": None,
        "attributes": {},
        "metrics": {},
    }
    context: Any = EvaluatorContext(**fields)
    return evaluator.evaluate(context)


def test_coverage_is_the_share_of_key_words_found_anywhere_in_the_design() -> None:
    assert score(evals.CoversTheMission(), result("It works.")) == pytest.approx(1 / 2)
    assert score(evals.CoversTheMission(), result("Ticked tasks stay ticked.")) == 1.0


def test_enough_criteria_is_capped_at_one() -> None:
    assert score(evals.EnoughCriteria(), result("a")) == pytest.approx(0.5)
    assert score(evals.EnoughCriteria(), result("a", "b", "c")) == pytest.approx(1.0)


def test_a_criterion_that_names_nothing_from_the_mission_does_not_count() -> None:
    mixed = result("It works.", "A ticked task shows a tick.")

    assert score(evals.CriteriaTiedToTheMission(), mixed) == pytest.approx(0.5)
    assert score(evals.CriteriaTiedToTheMission(), result("It works.")) == 0.0


def test_a_run_with_no_design_scores_nothing_but_still_reports_its_effort() -> None:
    nothing = evals.DesignResult(
        design=None, outcome="failed", attempts=3, rounds=4, open_findings=2
    )

    for evaluator in (
        evals.CoversTheMission(),
        evals.EnoughCriteria(),
        evals.CriteriaTiedToTheMission(),
        evals.SettlesTheUnsaid(),
    ):
        assert score(evaluator, nothing) == 0.0
    assert score(evals.Effort(), nothing) == {
        "attempts": 3.0,
        "rounds": 4.0,
        "open_findings": 2.0,
    }


@pytest.mark.posix_only
async def test_one_mission_runs_both_ways_and_review_changes_what_reaches_a_person() -> None:
    designer, plain_designer = evals.designers(evals.offline_model())
    mission = evals.CASES[0].inputs

    reviewed = await evals.run_design(designer, mission)
    plain = await evals.run_design(plain_designer, mission)

    assert (reviewed.outcome, reviewed.attempts, reviewed.rounds, reviewed.open_findings) == (
        "passed",
        1,
        2,
        1,
    )
    assert (plain.outcome, plain.attempts, plain.rounds, plain.open_findings) == (
        "passed",
        1,
        0,
        0,
    )
    assert reviewed.design is not None and plain.design is not None
    assert plain.design.acceptance_criteria == [evals.THIN]
    assert len(reviewed.design.acceptance_criteria) == 3


@pytest.mark.posix_only
def test_both_arms_share_a_name_and_differ_only_in_the_methodology() -> None:
    designer, plain_designer = evals.designers(evals.offline_model())

    with_review = evals.design_factory(designer).methodologies()["design"]
    without = evals.design_factory(plain_designer).methodologies()["design"]

    assert designer.name == plain_designer.name == "designer"
    assert [str(m.ref) for m in with_review] == ["elephant-goldfish@2"]
    assert without == ()


@pytest.mark.posix_only
def test_the_offline_comparison_runs_from_the_command_line_and_writes_its_results(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "results.json"

    code = evals.main(["--offline", "--out", str(out)])

    printed = capsys.readouterr().out
    results = json.loads(out.read_text(encoding="utf-8"))
    assert code == 0
    assert results["cases"] == ["todo-list", "keep-ticked-tasks", "delete-tasks", "due-dates"]
    assert (results["model"], results["failures"]) == ("offline (scripted)", 0)
    assert results["with_review"]["CriteriaTiedToTheMission"] > 0.8
    assert results["without_review"]["CriteriaTiedToTheMission"] == 0.0
    assert results["with_review"]["SettlesTheUnsaid"] == 0.0
    assert results["with_review"]["rounds"] == 2.0
    assert results["without_review"]["rounds"] == 0.0
    assert "with review" in printed and "These numbers come from a scripted model." in printed


def test_every_case_names_words_its_mission_really_turns_on() -> None:
    for case in evals.CASES:
        assert case.metadata is not None and case.name
        assert len(case.metadata.must_cover) >= 3
        assert all(term in case.inputs.lower() for term in case.metadata.must_cover)


def test_a_hidden_check_is_settled_only_by_one_statement_that_answers_it() -> None:
    blank = evals.BLANK_TASK

    assert blank.settled_by("Pressing Enter on a blank input adds nothing.")
    assert not blank.settled_by("Whitespace is trimmed before a task is added.")
    assert not blank.settled_by("The list starts empty.")


def test_a_statement_that_rules_the_feature_out_does_not_settle_how_it_works() -> None:
    assert evals.DELETE_TICKED.settled_by("Any task can be deleted, ticked or not.")
    assert not evals.DELETE_TICKED.settled_by("No deleting of tasks; ticked tasks stay.")


def test_settling_the_unsaid_is_the_share_of_hidden_checks_the_design_answers() -> None:
    expected = evals.Expectation(
        must_cover=("add",), hidden=(evals.BLANK_TASK, evals.UNTICK, evals.DUE_TODAY)
    )
    design = result(
        "Unticking a task marks it not done.",
        behaviour="Adding a blank task does nothing.",
    )

    assert score(evals.SettlesTheUnsaid(), design, expected) == pytest.approx(2 / 3)
    assert score(evals.SettlesTheUnsaid(), result("It works."), expected) == 0.0


def test_no_mission_settles_its_own_hidden_checks() -> None:
    for case in evals.CASES:
        assert case.metadata is not None and len(case.metadata.hidden) >= 3
        assert not [c.name for c in case.metadata.hidden if c.settled_by(case.inputs)]
