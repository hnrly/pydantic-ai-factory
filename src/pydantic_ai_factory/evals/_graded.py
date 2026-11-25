"""The Pydantic Evals evaluator that grades a run from its record and the case's fact sheet.

The task hands back a :class:`RunRecord`. :class:`Graded` loads the fact sheet by the
case's name, here on the grader side, and returns the outcome, whether it is
a success, the counts behind every question measure and the checklist coverage. Nothing
from a fact sheet is ever placed in a case's inputs, expected output or metadata.

The checklist verdicts are stored beside the record, as ``checklist.json``, so a run can
be regraded without calling the judge, and comes out the same.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, TypeAdapter
from pydantic_evals.evaluators import Evaluator, EvaluatorContext
from pydantic_evals.evaluators.evaluator import EvaluationScalar

from ._checklist import ChecklistJudge, ItemVerdict, coverage, scripted_judge
from ._contract import RunRecord
from ._facts import FactSheet
from ._grading import Grade, grade

VERDICTS_FILE = "checklist.json"
"""The file in a run's folder that holds its checklist verdicts."""

JudgeFor = Callable[[FactSheet], ChecklistJudge]
"""Builds the checklist judge for one case's sheet."""

SheetFor = Callable[[str], FactSheet]
"""Loads the fact sheet of a case, by its name, on the grader side."""

_VERDICTS = TypeAdapter(tuple[ItemVerdict, ...])


class MissingVerdicts(LookupError):
    """A regrade found a delivered run whose checklist verdicts were never stored."""


class GradedRun(BaseModel):
    """Everything known about one graded run. ``coverage`` is ``None`` with no delivery.

    ``verdicts`` holds the checklist judge's verdict on each item, for the scorecard's
    coverage of each item.
    """

    model_config = ConfigDict(frozen=True)

    record: RunRecord
    grade: Grade
    coverage: float | None
    verdicts: tuple[ItemVerdict, ...] = ()

    @property
    def run_id(self) -> str:
        return f"{self.record.arm}/{self.record.case_name}/{self.record.repeat}"

    @property
    def needless_questions(self) -> int:
        """Low facts and environment facts released: questions it did not need to ask."""
        measures = self.grade.measures
        return measures.low_facts_released + measures.environment_facts_released

    def summary(self) -> dict[str, Any]:
        """The run's entry in ``results.json``."""
        return {
            "run_id": self.run_id,
            "arm": self.record.arm,
            "case": self.record.case_name,
            "repeat": self.record.repeat,
            "outcome": self.grade.outcome,
            "success": self.grade.success,
            "turns": self.grade.turns,
            "needless_questions": self.needless_questions,
            "questions": self.grade.measures.model_dump(),
            "checklist_coverage": self.coverage,
            "cost_usd": self.record.cost_usd,
            "reruns": self.record.reruns,
        }

    def line(self) -> str:
        """The one line the command prints for this run."""
        share = "-" if self.coverage is None else f"{self.coverage:.2f}"
        return (
            f"{self.record.arm}  {self.record.case_name}  repeat {self.record.repeat}  "
            f"{self.grade.outcome}  turns {self.grade.turns}  "
            f"needless {self.needless_questions}  checklist {share}"
        )


async def grade_run(
    record: RunRecord,
    folder: Path,
    judge_for: JudgeFor | None,
    *,
    sheet_for: SheetFor,
) -> GradedRun:
    """Grade the run kept in ``folder``, and store its checklist verdicts there.

    With no ``judge_for``, every verdict must already be stored, and no model is called:
    that is a regrade. A missing verdict then raises :class:`MissingVerdicts`.
    """
    sheet = sheet_for(record.case_name)
    graded = grade(record, sheet)
    stored = _read_verdicts(folder)
    delivery = record.delivery
    share: float | None = None
    verdicts: tuple[ItemVerdict, ...] = ()
    if delivery is not None:
        if judge_for is None:
            have = {v.item_id for v in stored if v.sha256 == delivery.sha256}
            missing = [item.id for item in sheet.checklist if item.id not in have]
            if missing:
                raise MissingVerdicts(f"{folder}: no stored verdict for {', '.join(missing)}")
            judge = _no_judge()
        else:
            judge = judge_for(sheet)
        found = await coverage(delivery, sheet, judge, stored)
        if found is not None:
            share = found.share
            verdicts = found.verdicts
            _write_verdicts(folder, found.verdicts)
    return GradedRun(record=record, grade=graded, coverage=share, verdicts=verdicts)


@dataclass
class Graded(Evaluator[str, RunRecord, None]):
    """Grade the run the task returned. The only evaluator an experiment uses."""

    out: Path
    judge_for: JudgeFor
    sheet_for: SheetFor

    async def evaluate(
        self, ctx: EvaluatorContext[str, RunRecord, None]
    ) -> dict[str, EvaluationScalar]:
        record = ctx.output
        folder = self.out / record.arm / record.case_name / str(record.repeat)
        graded = await grade_run(record, folder, self.judge_for, sheet_for=self.sheet_for)
        result: dict[str, EvaluationScalar] = {
            "outcome": graded.grade.outcome,
            "success": graded.grade.success,
            "turns": graded.grade.turns,
            "needless_questions": graded.needless_questions,
        }
        for name, count in graded.grade.measures.model_dump().items():
            result[name] = int(count)
        if graded.coverage is not None:
            result["checklist_coverage"] = graded.coverage
        return result

    def build_serialization_arguments(self) -> dict[str, Any]:
        # The judge is a live object, so the evaluator is named in reports without arguments.
        return {}


def _read_verdicts(folder: Path) -> tuple[ItemVerdict, ...]:
    path = folder / VERDICTS_FILE
    if not path.is_file():
        return ()
    return _VERDICTS.validate_json(path.read_bytes())


def _write_verdicts(folder: Path, verdicts: Sequence[ItemVerdict]) -> None:
    data = [v.model_dump(mode="json") for v in verdicts]
    (folder / VERDICTS_FILE).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _no_judge() -> ChecklistJudge:
    """A judge that is never called: a regrade reuses every stored verdict."""
    return scripted_judge({})
