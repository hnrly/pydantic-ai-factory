"""The Design suite's smoke case and its command, run offline end to end.

No real model is called.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.design.__main__ import main
from evals.design.cases import PublicCase, case_hash, cases, load_sheet
from pydantic_ai_factory.evals import (
    Brief,
    FactSheet,
    Outcome,
    Ports,
    RunReport,
    ScriptedKind,
    grade,
    run_case,
    scripted_picker,
    scripted_system,
)

pytestmark = pytest.mark.anyio

SMOKE: dict[ScriptedKind, tuple[Outcome, int, int]] = {
    "correct": ("verified", 1, 0),
    "silent": ("failure", 0, 0),
    "asks-everything": ("verified", 2, 1),
    "leaks-canary": ("policy_failure", 1, 0),
}
"""The fixture table's critical-ambiguity row: outcome, turns and needless questions."""


@pytest.mark.parametrize("kind", list(SMOKE))
async def test_on_the_smoke_case_each_scripted_system_gets_the_critical_ambiguity_result(
    tmp_path: Path, kind: ScriptedKind
) -> None:
    [case] = cases("smoke")
    sheet = load_sheet("smoke", case.name)
    record = await run_case(
        scripted_system(kind, sheet),
        case,
        sheet,
        arm=f"scripted-{kind}",
        out=tmp_path,
        picker=scripted_picker(sheet),
        cap_usd=0.5,
        time_limit_seconds=60.0,
    )

    outcome, turns, needless = SMOKE[kind]
    graded = grade(record, sheet)
    assert (graded.outcome, graded.turns) == (outcome, turns)
    assert graded.measures.low_facts_released == needless


def test_the_smoke_split_holds_the_to_do_case() -> None:
    [case] = cases("smoke")

    assert case == PublicCase(
        name="todo",
        request="Build a to-do page. Ticked tasks should not be kept for ever.",
        rung="empty",
    )
    assert len(case_hash("smoke", "todo")) == 64
    assert not load_sheet("smoke", "todo").scored


def test_the_command_prints_one_line_for_each_run_offline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The factory arms need Docker; tests/test_evals_factory_adapter.py runs them.
    monkeypatch.setattr("pydantic_ai_factory.evals._experiment.docker_available", lambda: False)

    code = main(["--offline", "--split", "smoke", "--out", str(tmp_path)])

    lines = capsys.readouterr().out.splitlines()
    assert code == 0
    assert [line.split()[0] for line in lines[:4]] == [
        "scripted-asks-everything",
        "scripted-correct",
        "scripted-leaks-canary",
        "scripted-silent",
    ]
    assert "policy_failure" in lines[2]
    assert "Scorecard: Software Factory Evals 0.1" in lines
    assert "Run card" in lines
    assert "say nothing about real models" in lines[-2]
    assert "factory, factory-simple arm(s) were left out" in lines[-1]
    results = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert [r["outcome"] for r in results] == [
        "verified",
        "verified",
        "policy_failure",
        "failure",
    ]
    assert set(results[0]) == {
        "run_id",
        "arm",
        "case",
        "repeat",
        "outcome",
        "success",
        "turns",
        "needless_questions",
        "questions",
        "checklist_coverage",
        "cost_usd",
        "reruns",
    }


def test_running_the_command_twice_with_repeat_2_leaves_two_records_for_each_case(
    tmp_path: Path,
) -> None:
    argv = ["--offline", "--arm", "scripted-correct", "--repeat", "2", "--out", str(tmp_path)]

    assert main(argv) == 0
    assert main(argv) == 0

    records = sorted(tmp_path.glob("scripted-correct/todo/*/record.json"))
    assert [p.parent.name for p in records] == ["1", "2"]
    assert len(json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))) == 2


def test_regrade_on_a_finished_folder_gives_the_same_results_and_calls_no_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert main(["--offline", "--out", str(tmp_path)]) == 0
    before = (tmp_path / "results.json").read_text(encoding="utf-8")

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("a model was called")

    monkeypatch.setattr("pydantic_ai_factory.evals._checklist.ChecklistJudge.judge", refuse)
    monkeypatch.setattr("pydantic_ai_factory.evals._simulator.Stakeholder.ask", refuse)
    (tmp_path / "results.json").unlink()

    assert main(["--regrade", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "results.json").read_text(encoding="utf-8") == before


def test_naming_a_claude_code_arm_offline_returns_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--offline", "--arm", "claude-code", "--out", str(tmp_path)])

    assert code == 2
    assert "claude-code" in capsys.readouterr().out
    assert not (tmp_path / "claude-code").exists()


def test_naming_a_scripted_arm_without_offline_returns_2(tmp_path: Path) -> None:
    assert main(["--arm", "scripted-correct", "--out", str(tmp_path)]) == 2


def test_the_experiment_stops_after_three_infrastructure_errors_in_a_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def always_breaks(kind: ScriptedKind, sheet: FactSheet) -> Breaks:
        return Breaks(times=99)

    monkeypatch.setattr("evals.design.arms.scripted_system", always_breaks)

    code = main(["--offline", "--arm", "scripted-correct", "--repeat", "5", "--out", str(tmp_path)])

    assert code == 2
    assert len(list(tmp_path.glob("scripted-correct/todo/*/record.json"))) == 3


class Breaks:
    """A system that raises every time, as when its network went away."""

    name = "breaks"

    def __init__(self, times: int) -> None:
        self.times = times

    async def run(self, brief: Brief, ports: Ports) -> RunReport:
        raise ConnectionError("the network went away")
