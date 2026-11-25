"""Software Factory Evals as a :class:`~pydantic_ai_factory.evals.Suite`.

It measures how well a system designs software: the Design station of the software
factory, with and without independent review, and Claude Code, on cases whose requests
leave facts out. The framework runs the arms and grades every run; this module says what
the cases, the arms, the judge and the limits are, and prints the scorecard.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pydantic_ai_factory.evals import (
    ChecklistJudge,
    FactSheet,
    GradedRun,
    Judge,
    Suite,
)
from pydantic_ai_factory.evals import regrade as regrade_suite
from pydantic_ai_factory.evals import run_experiment as run_suite

from .arms import ARMS
from .cases import SPLITS, cases, load_sheet, sheet_for, split_of
from .report import (
    judge_agreement,
    run_card,
    run_card_text,
    scorecard,
    scorecard_text,
    write_reports,
)
from .version import (
    CAP_USD,
    JUDGE_MODEL,
    JUDGE_PROMPT,
    PICKER_MODEL,
    PINNED_MODEL,
    TIME_LIMIT_SECONDS,
)
from .workspace import check_case, workspace_for

OFFLINE_NOTE = (
    "Offline: scripted systems and a scripted designer on a scripted picker. These numbers "
    "show the harness works. They say nothing about real models."
)


def real_judge_for(sheet: FactSheet) -> ChecklistJudge:
    """The checklist judge on the pinned judge model, the same for every case."""
    return ChecklistJudge(JUDGE_MODEL, JUDGE_PROMPT)


def print_reports(out: Path, graded: Sequence[GradedRun], judge: Judge) -> None:
    """Build the scorecard and the run card for every run in ``out``, print both and write both.

    The run card's splits, arms and repeats are those of the runs in ``out``.
    """
    if not graded:
        return
    names = sorted({run.record.case_name for run in graded})
    split_names = {name: split_of(name) for name in names}
    sheets = {name: load_sheet(split_names[name], name) for name in names}
    score = scorecard(
        graded,
        sheets,
        judge_agreement=judge_agreement(),
        judge_scripted=judge == "scripted",
    )
    card = run_card(
        [split for split in SPLITS if split in split_names.values()],
        max(run.record.repeat for run in graded),
        sorted({run.record.arm for run in graded}),
        judge=judge,
    )
    write_reports(out, score, card)
    print()
    print(scorecard_text(score))
    print()
    print(run_card_text(card))


def _sheet_for(case_name: str) -> FactSheet:
    return sheet_for(case_name)


SUITE = Suite(
    name="Software Factory Evals",
    splits=SPLITS,
    cases=cases,
    sheet_for=_sheet_for,
    arms=ARMS,
    judge_for=real_judge_for,
    picker_model=PICKER_MODEL,
    model=PINNED_MODEL,
    cap_usd=CAP_USD,
    time_limit_seconds=TIME_LIMIT_SECONDS,
    workspace_for=workspace_for,
    check_case=check_case,
    report=print_reports,
    offline_note=OFFLINE_NOTE,
)
"""The Design suite, as the command runs it."""


def run_experiment(
    arms: Sequence[str],
    split: str,
    repeat: int,
    out: Path,
    *,
    offline: bool,
    model: str | None = None,
    judge: Judge | None = None,
) -> int:
    """Run the suite's ``arms`` on ``split``, as :func:`pydantic_ai_factory.evals.run_experiment`
    does."""
    return run_suite(SUITE, arms, split, repeat, out, offline=offline, model=model, judge=judge)


def regrade(out: Path, *, judge: Judge = "real") -> int:
    """Grade every run in ``out`` again, with stored verdicts only."""
    return regrade_suite(SUITE, out, judge=judge)
