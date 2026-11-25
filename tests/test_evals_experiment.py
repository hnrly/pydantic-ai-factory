"""An experiment runs a suite's arms on its cases, grades every run and resumes where it
stopped. The suite here is a small one of its own, on scripted systems only.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic_evals import Case as EvalsCase
from pydantic_evals import Dataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext
from pydantic_evals.reporting.analyses import ScalarResult, TableResult

from pydantic_ai_factory.evals import (
    Arm,
    ArmContext,
    Brief,
    Case,
    FactSheet,
    GradedRun,
    Judge,
    PassK,
    Ports,
    RunReport,
    SuccessRate,
    Suite,
    SystemAdapter,
    regrade,
    run_experiment,
    scripted_system,
)
from pydantic_ai_factory.evals._experiment import docker_refusal, scripted_judge_for

SHEET = FactSheet.model_validate(
    {
        "case_name": "tasks",
        "challenge_class": "critical-ambiguity",
        "canary": "canary-1234abcd5678",
        "fallback_reply": "I don't have anything to add on that.",
        "approval_reply": "I can't sign anything off here.",
        "checklist": [
            {"id": "expiry", "item": "Says what happens to a task when it expires."},
            {"id": "undo", "item": "Lets a person undo a tick."},
            {"id": "order", "item": "Keeps tasks in the order they were added."},
        ],
        "facts": [
            {
                "id": "retention",
                "value": "47 days",
                "criticality": "critical",
                "obtainable": True,
                "held_by": "product owner",
                "statement": "Ticked tasks are kept for 47 days.",
                "direct_question": "How long are ticked tasks kept?",
                "keywords": ["kept"],
            }
        ],
    }
)
CASE = Case(name="tasks", request="Build a to-do page.")


class Breaks:
    name = "breaks"

    async def run(self, brief: Brief, ports: Ports) -> RunReport:
        raise ConnectionError("the network went away")


def correct(case: Case, sheet: FactSheet, context: ArmContext) -> SystemAdapter:
    return scripted_system("correct", sheet)


def breaks(case: Case, sheet: FactSheet, context: ArmContext) -> SystemAdapter:
    return Breaks()


def suite(reports: list[int] | None = None) -> Suite:
    def report(out: Path, graded: Sequence[GradedRun], judge: Judge) -> None:
        if reports is not None:
            reports.append(len(graded))

    return Suite(
        name="tasks",
        splits=("dev",),
        cases=lambda split: [CASE],
        sheet_for=lambda name: SHEET,
        arms=(
            Arm(name="correct", build=correct, real=False),
            Arm(name="breaks", build=breaks, real=False),
            Arm(name="program", build=correct, offline=False),
            Arm(name="reads", build=correct, reads_files=True),
        ),
        judge_for=scripted_judge_for,
        picker_model="test",
        model="test",
        cap_usd=0.5,
        time_limit_seconds=60.0,
        report=report,
    )


def test_an_experiment_grades_every_run_writes_results_and_calls_the_suites_report(
    tmp_path: Path,
) -> None:
    reports: list[int] = []

    code = run_experiment(suite(reports), ["correct"], "dev", 2, tmp_path, offline=True)

    assert code == 0
    results = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert [(r["arm"], r["outcome"], r["checklist_coverage"]) for r in results] == [
        ("correct", "verified", 1.0),
        ("correct", "verified", 1.0),
    ]
    assert reports == [2]


def test_an_experiment_resumes_and_runs_only_what_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_experiment(suite(), ["correct"], "dev", 1, tmp_path, offline=True) == 0
    capsys.readouterr()

    assert run_experiment(suite(), ["correct"], "dev", 1, tmp_path, offline=True) == 0
    assert "Nothing was run" in capsys.readouterr().out


def test_a_regrade_calls_no_judge_and_gives_the_same_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert run_experiment(suite(), ["correct"], "dev", 1, tmp_path, offline=True) == 0
    before = (tmp_path / "results.json").read_text(encoding="utf-8")

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the judge was called")

    monkeypatch.setattr("pydantic_ai_factory.evals._checklist.ChecklistJudge.judge", refuse)
    assert regrade(suite(), tmp_path, judge="scripted") == 0
    assert (tmp_path / "results.json").read_text(encoding="utf-8") == before


def test_the_experiment_stops_after_three_infrastructure_errors_in_a_row(tmp_path: Path) -> None:
    code = run_experiment(suite(), ["breaks"], "dev", 5, tmp_path, offline=True)

    assert code == 2
    assert len(list(tmp_path.glob("breaks/tasks/*/record.json"))) == 3


@pytest.mark.parametrize(
    ("arm", "offline", "said"),
    [
        ("missing", True, "There is no arm called 'missing'"),
        ("correct", False, "runs only with --offline"),
        ("program", True, "so not with --offline"),
    ],
)
def test_an_arm_that_cannot_run_in_this_mode_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], arm: str, offline: bool, said: str
) -> None:
    assert run_experiment(suite(), [arm], "dev", 1, tmp_path, offline=offline) == 2
    assert said in capsys.readouterr().out
    assert not (tmp_path / arm).exists()


def test_an_arm_that_reads_files_is_refused_when_docker_is_not_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pydantic_ai_factory.evals._experiment.docker_available", lambda: False)

    refusal = docker_refusal(suite(), ["reads", "correct"])

    assert refusal is not None
    assert "Docker is needed to run the reads arm(s)" in refusal
    assert docker_refusal(suite(), ["correct"]) is None


def test_with_no_arm_named_an_offline_experiment_leaves_out_what_needs_docker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pydantic_ai_factory.evals._experiment.docker_available", lambda: False)

    assert suite().default_arms(offline=True) == (["correct", "breaks"], ["reads"])
    assert suite().default_arms(offline=False) == (["program", "reads"], [])


def test_the_report_evaluators_give_the_success_rate_across_cases_and_reliability() -> None:
    @dataclass
    class Succeeds(Evaluator[str, str, None]):
        def evaluate(self, ctx: EvaluatorContext[str, str, None]) -> dict[str, bool]:
            return {"success": ctx.output == "always"}

    async def task(name: str) -> str:
        return name

    dataset = Dataset[str, str, None](
        name="report",
        cases=[EvalsCase(name=name, inputs=name) for name in ("always", "never")],
        evaluators=[Succeeds()],
        report_evaluators=[SuccessRate(), PassK(k=2)],
    )
    report = dataset.evaluate_sync(task, repeat=2, progress=False)

    scalars = {a.title: a.value for a in report.analyses if isinstance(a, ScalarResult)}
    assert scalars == {"success rate": 0.5, "pass^2": 0.5}
    [table] = [a for a in report.analyses if isinstance(a, TableResult)]
    assert table.rows == [["always", 2, 2], ["never", 2, 0]]
