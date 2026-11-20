"""The one Pydantic Evals evaluator: grade a run from its record and the case's fact sheet.

The task hands back a :class:`~evals.contract.RunRecord`. :class:`Graded` loads the fact
sheet by the case's name, here on the grader side, and returns the outcome, whether it is
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
from pydantic_ai import Agent
from pydantic_evals.evaluators import Evaluator, EvaluatorContext
from pydantic_evals.evaluators.evaluator import EvaluationScalar

from evals import facts
from evals.cases import CASES, split_of
from evals.checklist import ItemVerdict, JudgeAnswer, coverage, scripted_judge
from evals.contract import RunRecord
from evals.grading import Grade, grade

VERDICTS_FILE = "checklist.json"
"""The file in a run's folder that holds its checklist verdicts."""

JudgeFor = Callable[[facts.FactSheet], Agent[None, JudgeAnswer]]
"""Builds the checklist judge for one case's sheet."""

_VERDICTS = TypeAdapter(tuple[ItemVerdict, ...])


class MissingVerdicts(LookupError):
    """A regrade found a delivered run whose checklist verdicts were never stored."""


class GradedRun(BaseModel):
    """Everything known about one graded run. ``coverage`` is ``None`` with no delivery."""

    model_config = ConfigDict(frozen=True)

    record: RunRecord
    grade: Grade
    coverage: float | None

    @property
    def run_id(self) -> str:
        return f"{self.record.arm}/{self.record.case_name}/{self.record.repeat}"

    @property
    def needless_questions(self) -> int:
        """Low facts and repo facts released: questions the system did not need to ask."""
        measures = self.grade.measures
        return measures.low_facts_released + measures.repo_facts_released

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
    cases_root: Path = CASES,
) -> GradedRun:
    """Grade the run kept in ``folder``, and store its checklist verdicts there.

    With no ``judge_for``, every verdict must already be stored, and no model is called:
    that is a regrade. A missing verdict then raises :class:`MissingVerdicts`.
    """
    split = split_of(record.case_name, root=cases_root)
    sheet = facts.load(split, record.case_name, root=cases_root)
    graded = grade(record, sheet)
    stored = _read_verdicts(folder)
    package = record.package
    share: float | None = None
    if package is not None:
        if judge_for is None:
            have = {v.item_id for v in stored if v.document_sha256 == package.document_sha256}
            missing = [item.id for item in sheet.checklist if item.id not in have]
            if missing:
                raise MissingVerdicts(f"{folder}: no stored verdict for {', '.join(missing)}")
            judge = _no_judge()
        else:
            judge = judge_for(sheet)
        found = await coverage(package, sheet, judge, stored)
        if found is not None:
            share = found.share
            _write_verdicts(folder, found.verdicts)
    return GradedRun(record=record, grade=graded, coverage=share)


@dataclass
class Graded(Evaluator[str, RunRecord, None]):
    """Grade the run the task returned. The only evaluator the experiment uses."""

    out: Path
    judge_for: JudgeFor

    async def evaluate(
        self, ctx: EvaluatorContext[str, RunRecord, None]
    ) -> dict[str, EvaluationScalar]:
        record = ctx.output
        folder = self.out / record.arm / record.case_name / str(record.repeat)
        graded = await grade_run(record, folder, self.judge_for)
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


def _no_judge() -> Agent[None, JudgeAnswer]:
    """A judge that is never called: a regrade reuses every stored verdict."""
    return scripted_judge({})
