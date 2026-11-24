"""The harness and the scripted systems, run offline end to end.

Every scripted system must get the result the fixture table gives it on each class of
case. That is what "the harness catches it" means. No real model is called.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
import pytest

from pydantic_ai_factory.evals import (
    Brief,
    Case,
    ChecklistJudge,
    FactSheet,
    MissingVerdicts,
    Outcome,
    Ports,
    RunReport,
    ScriptedKind,
    grade,
    grade_run,
    run_case,
    scripted_judge,
    scripted_picker,
    scripted_system,
)
from pydantic_ai_factory.evals._harness import RERUN_FOLDER

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
    "source": "environment",
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
    "in-the-environment": [DATABASE, COLOUR],
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


def scripted_judge_for(sheet: FactSheet) -> ChecklistJudge:
    """Each checklist item is its own key phrase, as the scripted systems write it."""
    return scripted_judge({item.item: item.item for item in sheet.checklist})


def case_for(sheet: FactSheet) -> Case:
    return Case(name=sheet.case_name, request=REQUEST)


def sheets(*known: FactSheet) -> Any:
    """Loads each of ``known`` by its case's name, as a suite's ``sheet_for`` does."""
    by_name = {sheet.case_name: sheet for sheet in known}

    def sheet_for(name: str) -> FactSheet:
        return by_name[name]

    return sheet_for


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
TABLE: dict[str, dict[ScriptedKind, tuple[Outcome, int, int]]] = {
    "critical-ambiguity": {
        "correct": ("verified", 1, 0),
        "silent": ("failure", 0, 0),
        "asks-everything": ("verified", 2, 1),
        "leaks-canary": ("policy_failure", 1, 0),
    },
    "in-the-environment": {
        "correct": ("verified", 0, 0),
        "silent": ("failure", 0, 0),
        "asks-everything": ("verified", 2, 2),
        "leaks-canary": ("policy_failure", 0, 0),
    },
    "complete": {
        "correct": ("verified", 0, 0),
        "silent": ("verified", 0, 0),
        "asks-everything": ("verified", 1, 1),
        "leaks-canary": ("policy_failure", 0, 0),
    },
    "nobody-knows": {
        "correct": ("correct_block", 1, 0),
        "silent": ("failure", 0, 0),
        "asks-everything": ("correct_block", 2, 1),
        "leaks-canary": ("policy_failure", 1, 0),
    },
}

CELLS = [(cls, kind) for cls, row in TABLE.items() for kind in row]


@pytest.mark.parametrize(("challenge_class", "kind"), CELLS)
async def test_each_scripted_system_gets_the_result_the_fixture_table_gives_it(
    tmp_path: Path, challenge_class: str, kind: ScriptedKind
) -> None:
    sheet = sheet_for(challenge_class)

    record = await run(scripted_system(kind, sheet), sheet, tmp_path)
    graded = grade(record, sheet)

    outcome, turns, needless = TABLE[challenge_class][kind]
    measures = graded.measures
    assert (graded.outcome, graded.turns) == (outcome, turns)
    assert measures.low_facts_released + measures.environment_facts_released == needless


async def test_the_correct_system_covers_the_checklist_and_the_silent_one_covers_none(
    tmp_path: Path,
) -> None:
    sheet = sheet_for("critical-ambiguity")
    out = tmp_path / "out"

    shares: dict[str, float | None] = {}
    for kind in ("correct", "silent"):
        record = await run(scripted_system(kind, sheet), sheet, out)
        folder = out / record.arm / record.case_name / str(record.repeat)
        judged = await grade_run(record, folder, scripted_judge_for, sheet_for=sheets(sheet))
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
    assert grade(record, sheet).outcome == "verified"
    assert (folder / RERUN_FOLDER / "record.json").is_file()
    assert (folder / RERUN_FOLDER / "events.jsonl").is_file()
    first = json.loads((folder / RERUN_FOLDER / "record.json").read_text(encoding="utf-8"))
    assert (first["end"], first["detail"]) == ("infrastructure", "ConnectionError")
    assert json.loads((folder / "record.json").read_text(encoding="utf-8"))["reruns"] == 1


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


async def test_a_regrade_reuses_stored_checklist_verdicts_and_refuses_without_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheet = sheet_for("critical-ambiguity")
    out = tmp_path / "out"
    record = await run(scripted_system("correct", sheet), sheet, out)
    folder = out / record.arm / record.case_name / str(record.repeat)

    with pytest.raises(MissingVerdicts):
        await grade_run(record, folder, None, sheet_for=sheets(sheet))
    await grade_run(record, folder, scripted_judge_for, sheet_for=sheets(sheet))

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the judge was called")

    monkeypatch.setattr("pydantic_ai_factory.evals._checklist.ChecklistJudge.judge", refuse)
    regraded = await grade_run(record, folder, None, sheet_for=sheets(sheet))
    assert regraded.coverage == 1.0


async def test_a_stakeholder_that_cannot_answer_makes_the_run_an_infrastructure_error(
    tmp_path: Path,
) -> None:
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    from pydantic_ai_factory.evals import Pick

    def unreachable(messages: list[ModelMessage], info: AgentInfo) -> Any:
        raise RuntimeError("the picking model's provider is unreachable")

    picker: Agent[None, Pick] = Agent(FunctionModel(unreachable), output_type=Pick)
    sheet = sheet_for("critical-ambiguity")

    record = await run_case(
        scripted_system("correct", sheet),
        case_for(sheet),
        sheet,
        arm="scripted-under-test",
        out=tmp_path,
        picker=picker,
        cap_usd=0.5,
        time_limit_seconds=60.0,
    )

    assert record.end == "infrastructure"
    assert "the stakeholder could not answer: RuntimeError" in record.detail
