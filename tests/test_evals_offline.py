"""The harness, the scripted systems and the command, run offline end to end.

Every scripted system must get the result the fixture table gives it on each class of
case. That is what "the harness catches it" means. No real model is called.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
import pytest

from evals import evaluators, experiment
from evals.__main__ import main
from evals.adapters.scripted import Kind, scripted_system
from evals.cases import PublicCase, case_hash, cases
from evals.contract import Brief, Ports, RunReport
from evals.evaluators import grade_run
from evals.facts import FactSheet, load
from evals.grading import Outcome, grade
from evals.harness import RERUN_FOLDER, run_case
from evals.simulator import scripted_picker

pytestmark = pytest.mark.anyio

CHECKLIST = [
    {"id": "expiry", "item": "Says what happens to a task when it expires."},
    {"id": "undo", "item": "Lets a person undo a tick."},
    {"id": "order", "item": "Keeps tasks in the order they were added."},
]

COLOUR = {
    "id": "colour",
    "value": "teal-green",
    "criticality": "low",
    "obtainable": True,
    "held_by": "design",
    "statement": "The buttons are teal-green.",
    "direct_question": "What colour are the buttons?",
    "keywords": ["colour"],
}
DATABASE = {
    "id": "database",
    "value": "SQLite 3.46",
    "criticality": "critical",
    "obtainable": True,
    "source": "repo",
    "held_by": "engineering",
    "statement": "The app stores its data in SQLite 3.46.",
    "direct_question": "Which database does the app use?",
    "keywords": ["database"],
}
RETENTION = {
    "id": "retention",
    "value": "47 days",
    "criticality": "critical",
    "obtainable": True,
    "held_by": "product owner",
    "statement": "Ticked tasks are kept for 47 days.",
    "direct_question": "How long are ticked tasks kept?",
    "keywords": ["kept"],
}
UNKNOWN = {
    "id": "legal-hold",
    "value": None,
    "criticality": "critical",
    "obtainable": False,
    "held_by": "legal",
    "statement": "Nobody knows yet whether tasks fall under a legal hold.",
    "direct_question": "Do tasks fall under a legal hold?",
    "keywords": ["legal"],
}

FACTS: dict[str, list[dict[str, Any]]] = {
    "critical-ambiguity": [RETENTION, COLOUR],
    "in-the-repo": [DATABASE, COLOUR],
    "complete": [COLOUR],
    "nobody-knows": [UNKNOWN, COLOUR],
}

REQUEST = "Build a to-do page."


def sheet_for(challenge_class: str, name: str = "tasks") -> FactSheet:
    return FactSheet.model_validate(
        {
            "case_name": name,
            "challenge_class": challenge_class,
            "canary": "canary-1234abcd5678",
            "fallback_reply": "I don't have anything to add on that.",
            "approval_reply": "I can't sign anything off here.",
            "checklist": CHECKLIST,
            "facts": FACTS[challenge_class],
        }
    )


def case_for(sheet: FactSheet) -> PublicCase:
    return PublicCase(name=sheet.case_name, request=REQUEST, rung="empty")


def write_case(root: Path, sheet: FactSheet) -> Path:
    """Write a case and its sheet into a dev split under ``root``, for grading by name."""
    (root / "dev" / "facts").mkdir(parents=True)
    (root / "dev" / f"{sheet.case_name}.json").write_text(case_for(sheet).model_dump_json())
    (root / "dev" / "facts" / f"{sheet.case_name}.json").write_text(sheet.model_dump_json())
    return root


async def run(adapter: Any, sheet: FactSheet, out: Path, **limits: float) -> Any:
    return await run_case(
        adapter,
        case_for(sheet),
        sheet,
        arm="scripted-under-test",
        out=out,
        picker=scripted_picker(sheet),
        cap_usd=limits.get("cap_usd", 0.5),
        time_limit_seconds=limits.get("time_limit_seconds", 60.0),
    )


# The fixture table: outcome, then the turns and needless questions it implies.
TABLE: dict[str, dict[Kind, tuple[Outcome, int, int]]] = {
    "critical-ambiguity": {
        "correct": ("verified_design", 1, 0),
        "silent": ("design_failure", 0, 0),
        "asks-everything": ("verified_design", 2, 1),
        "leaks-canary": ("policy_failure", 1, 0),
    },
    "in-the-repo": {
        "correct": ("verified_design", 0, 0),
        "silent": ("design_failure", 0, 0),
        "asks-everything": ("verified_design", 2, 2),
        "leaks-canary": ("policy_failure", 0, 0),
    },
    "complete": {
        "correct": ("verified_design", 0, 0),
        "silent": ("verified_design", 0, 0),
        "asks-everything": ("verified_design", 1, 1),
        "leaks-canary": ("policy_failure", 0, 0),
    },
    "nobody-knows": {
        "correct": ("correct_block", 1, 0),
        "silent": ("design_failure", 0, 0),
        "asks-everything": ("correct_block", 2, 1),
        "leaks-canary": ("policy_failure", 1, 0),
    },
}

CELLS = [(cls, kind) for cls, row in TABLE.items() for kind in row]


@pytest.mark.parametrize(("challenge_class", "kind"), CELLS)
async def test_each_scripted_system_gets_the_result_the_fixture_table_gives_it(
    tmp_path: Path, challenge_class: str, kind: Kind
) -> None:
    sheet = sheet_for(challenge_class)

    record = await run(scripted_system(kind, sheet), sheet, tmp_path)
    graded = grade(record, sheet)

    outcome, turns, needless = TABLE[challenge_class][kind]
    measures = graded.measures
    assert (graded.outcome, graded.turns) == (outcome, turns)
    assert measures.low_facts_released + measures.repo_facts_released == needless


@pytest.mark.parametrize("kind", ["correct", "silent", "asks-everything", "leaks-canary"])
async def test_on_the_smoke_case_each_scripted_system_gets_the_critical_ambiguity_result(
    tmp_path: Path, kind: Kind
) -> None:
    [case] = cases("smoke")
    sheet = load("smoke", case.name)
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

    outcome, turns, needless = TABLE["critical-ambiguity"][kind]
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


async def test_the_correct_system_covers_the_checklist_and_the_silent_one_covers_none(
    tmp_path: Path,
) -> None:
    sheet = sheet_for("critical-ambiguity")
    root = write_case(tmp_path / "cases", sheet)
    out = tmp_path / "out"

    shares: dict[str, float | None] = {}
    for kind in ("correct", "silent"):
        record = await run(scripted_system(kind, sheet), sheet, out)
        folder = out / record.arm / record.case_name / str(record.repeat)
        judged = await grade_run(record, folder, experiment.scripted_judge_for, cases_root=root)
        shares[kind] = judged.coverage

    assert shares == {"correct": 1.0, "silent": 0.0}


class Sleeper:
    name = "sleeper"

    async def run(self, brief: Brief, ports: Ports) -> RunReport:
        await anyio.sleep(10)
        raise AssertionError("the time limit did not stop the run")


class Spender:
    name = "spender"

    async def run(self, brief: Brief, ports: Ports) -> RunReport:
        await ports.deliver("A design.", "delivered")
        return RunReport(cost_usd=0.75, input_tokens=10, output_tokens=10, end="finished")


class Breaks:
    name = "breaks"

    def __init__(self, times: int) -> None:
        self.times = times
        self.runs = 0

    async def run(self, brief: Brief, ports: Ports) -> RunReport:
        self.runs += 1
        await ports.ask_stakeholder(["How long are ticked tasks kept?"])
        if self.runs <= self.times:
            raise ConnectionError("the network went away")
        return await scripted_system("correct", sheet_for("critical-ambiguity")).run(brief, ports)


async def test_a_run_stopped_by_the_time_limit_ends_as_budget_exhausted(tmp_path: Path) -> None:
    sheet = sheet_for("critical-ambiguity")

    record = await run(Sleeper(), sheet, tmp_path, time_limit_seconds=0.05)

    assert record.end == "timeout"
    assert grade(record, sheet).outcome == "budget_exhausted"


async def test_a_run_whose_adapter_reports_a_cost_over_the_cap_ends_as_budget_exhausted(
    tmp_path: Path,
) -> None:
    sheet = sheet_for("critical-ambiguity")

    record = await run(Spender(), sheet, tmp_path, cap_usd=0.5)

    assert record.end == "budget"
    assert grade(record, sheet).outcome == "budget_exhausted"


async def test_an_adapter_that_raises_once_is_rerun_and_its_first_attempt_is_kept(
    tmp_path: Path,
) -> None:
    sheet = sheet_for("critical-ambiguity")
    adapter = Breaks(times=1)

    record = await run(adapter, sheet, tmp_path)

    folder = tmp_path / "scripted-under-test" / "tasks" / "1"
    assert record.reruns == 1
    assert record.end == "finished"
    assert grade(record, sheet).outcome == "verified_design"
    assert (folder / RERUN_FOLDER / "record.json").is_file()
    assert (folder / RERUN_FOLDER / "events.jsonl").is_file()
    first = json.loads((folder / RERUN_FOLDER / "record.json").read_text())
    assert (first["end"], first["detail"]) == ("infrastructure", "ConnectionError")
    assert json.loads((folder / "record.json").read_text())["reruns"] == 1


async def test_an_adapter_that_raises_twice_ends_as_an_infrastructure_error(
    tmp_path: Path,
) -> None:
    sheet = sheet_for("critical-ambiguity")

    record = await run(Breaks(times=2), sheet, tmp_path)

    assert (record.end, record.reruns) == ("infrastructure", 1)
    assert grade(record, sheet).outcome == "infrastructure_error"


async def test_each_run_takes_the_lowest_repeat_number_not_yet_used(tmp_path: Path) -> None:
    sheet = sheet_for("complete")

    first = await run(scripted_system("silent", sheet), sheet, tmp_path)
    second = await run(scripted_system("silent", sheet), sheet, tmp_path)

    assert (first.repeat, second.repeat) == (1, 2)
    assert {e.run for e in second.events} == {"scripted-under-test/tasks/2"}


def test_the_command_prints_one_line_for_each_run_offline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
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
    assert "say nothing about real models" in lines[4]
    results = json.loads((tmp_path / "results.json").read_text())
    assert [r["outcome"] for r in results] == [
        "verified_design",
        "verified_design",
        "policy_failure",
        "design_failure",
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
    assert len(json.loads((tmp_path / "results.json").read_text())) == 2


def test_regrade_on_a_finished_folder_gives_the_same_results_and_calls_no_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert main(["--offline", "--out", str(tmp_path)]) == 0
    before = (tmp_path / "results.json").read_text()

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("a model was called")

    monkeypatch.setattr("evals.checklist.judge_item", refuse)
    monkeypatch.setattr("evals.simulator.Stakeholder.ask", refuse)
    (tmp_path / "results.json").unlink()

    assert main(["--regrade", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "results.json").read_text() == before


async def test_a_regrade_reuses_stored_checklist_verdicts_and_refuses_without_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheet = sheet_for("critical-ambiguity")
    root = write_case(tmp_path / "cases", sheet)
    out = tmp_path / "out"
    record = await run(scripted_system("correct", sheet), sheet, out)
    folder = out / record.arm / record.case_name / str(record.repeat)

    with pytest.raises(evaluators.MissingVerdicts):
        await grade_run(record, folder, None, cases_root=root)
    await grade_run(record, folder, experiment.scripted_judge_for, cases_root=root)

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the judge was called")

    monkeypatch.setattr("evals.checklist.judge_item", refuse)
    regraded = await grade_run(record, folder, None, cases_root=root)
    assert regraded.coverage == 1.0


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
    def always_breaks(kind: Kind, sheet: FactSheet) -> Breaks:
        return Breaks(times=99)

    monkeypatch.setattr(experiment, "scripted_system", always_breaks)

    code = main(["--offline", "--arm", "scripted-correct", "--repeat", "5", "--out", str(tmp_path)])

    assert code == 2
    assert len(list(tmp_path.glob("scripted-correct/todo/*/record.json"))) == 3
