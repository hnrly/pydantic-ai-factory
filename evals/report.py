"""The scorecard and the run card: what an experiment prints, and writes under ``--out``.

The scorecard has one row for each arm, with each split's cases in a table of their own,
so dev and test are reported apart. There is no combined score. Its parts, in order:

* the headline: the verified design rate, the gate line, reliability and cost;
* "How the session asked": the question measures;
* "Design quality: beside the outcome, never deciding it": checklist coverage;
* below the tables, the paired comparisons, every unexplained value, the coverage of
  each checklist item, the reruns and the limits every scorecard states.

Each rate is computed for each case first, then across cases with
:func:`~evals.stats.clustered_rate`, because the case is the unit of analysis. With one
repeat, a rate is printed with no interval.

The run card says what the results were measured with. An item whose source is missing,
such as a labelled sample not taken yet, says "not available".
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import subprocess
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from evals import facts, stats
from evals.adapters.claude_code import CLAUDE_CODE_ARM, claude_options
from evals.adapters.factory import FACTORY_ARM, SIMPLE_ARM, designer_question_caps
from evals.cases import CASES, SPLITS, case_hash, cases, split_of
from evals.evaluators import GradedRun
from evals.facts import FactSheet
from evals.grading import QuestionMeasures
from evals.port_tools import ASK_DESCRIPTION, DELIVER_DESCRIPTION
from evals.sandbox import REFERENCE
from evals.version import (
    BENCHMARK_NAME,
    BENCHMARK_VERSION,
    CAP_USD,
    CLAUDE_CODE_MODEL,
    CLAUDE_CODE_VERSION,
    JUDGE_MIN_AGREEMENT,
    JUDGE_MODEL,
    JUDGE_PROMPT,
    MINUTES_PER_TURN,
    PICKER_MODEL,
    PICKER_PROMPT,
    PINNED_MODEL,
    SANDBOX_IMAGE,
    TIME_LIMIT_SECONDS,
)

SCORECARD_FILE = "scorecard.json"
RUN_CARD_FILE = "run-card.json"

NOT_AVAILABLE = "not available"

RELIABILITY_K = 3
"""Reliability is the chance that this many attempts at a case all succeed."""

ROOT = Path(__file__).resolve().parent.parent
"""The repository's root, which holds the factory's skill library."""

SKILL_FOLDERS: tuple[Path, ...] = (
    ROOT / "src" / "pydantic_ai_factory" / "methodologies" / "_elephant_goldfish" / "skills",
    ROOT / "packages" / "software" / "src" / "pydantic_ai_factory_software" / "skills",
)
"""The factory's skill library: Elephant Goldfish's skills, then the software package's."""

LABELS = Path(__file__).parent / "labels"
"""Where the author's labelled samples are kept, once they are taken."""

PRIMARY = (FACTORY_ARM, CLAUDE_CODE_ARM)
ABLATION = (FACTORY_ARM, SIMPLE_ARM)

LIMITS: tuple[str, ...] = (
    "With about twelve cases, an interval on a rate is roughly 30 points either way.",
    "The cases are public, so a model trained later may have seen them.",
    "The author writes both the cases and the factory.",
    "One person labelled the samples.",
    "The picker is from the same model family as the systems under test.",
    "The factory is compared with a stock agent only.",
    "The Claude Code arm's cost is a list-price figure on a subscription seat, and the "
    "factory arms' cost is priced from tokens.",
    "No arm was given an output contract.",
    "The checklist judge is from the same model family as the arms, and checklist coverage "
    "says whether a design took the author's expected directions, not whether they are wise.",
)
"""The limits every scorecard states."""

T = TypeVar("T")

Probe = Callable[[Sequence[str]], str | None]
"""Runs a command and returns what it printed, or ``None`` when it could not run."""


class Estimate(BaseModel):
    """A rate across cases: its mean, its 95 per cent interval and the cases behind it.

    The bounds are ``None`` when there is no interval, and ``no_interval`` says why.
    """

    model_config = ConfigDict(frozen=True)

    mean: float
    low: float | None
    high: float | None
    cases: int
    no_interval: str | None = None

    def text(self) -> str:
        """The estimate as the scorecard prints it."""
        if self.low is None or self.high is None:
            return f"{self.mean:.2f}"
        return f"{self.mean:.2f} [{self.low:.2f}, {self.high:.2f}]"


class ArmRow(BaseModel):
    """One arm's results on one split. A metric with no case to compute it from is ``None``."""

    model_config = ConfigDict(frozen=True)

    arm: str
    runs: int
    cases: int
    repeats: int
    verified_design_rate: Estimate
    policy_failures: int
    reliability: float | None
    cost_per_verified_design: float | None
    cost_known: bool
    obtained_by_asking: Estimate | None
    question_precision: Estimate | None
    asked_and_used: Estimate | None
    asked_and_ignored: Estimate | None
    silent_assumptions: Estimate | None
    asked_what_the_code_says: Estimate | None
    needless_questions: Estimate | None
    released_nothing: Estimate | None
    questions_per_case: float
    minutes_per_case: float
    correct_blocks: Estimate | None
    delivered_on_nobody_knows: Estimate | None
    questions_on_complete_cases: float | None
    checklist_coverage: Estimate | None
    coverage_left_out: int
    item_coverage: dict[str, float]
    unexplained_values: tuple[str, ...]
    reruns: int


class Comparison(BaseModel):
    """The paired difference in verified design rate between two arms, case by case."""

    model_config = ConfigDict(frozen=True)

    label: str
    first: str
    second: str
    difference: Estimate


class SplitTable(BaseModel):
    """The scorecard's table for one split."""

    model_config = ConfigDict(frozen=True)

    split: str
    rows: tuple[ArmRow, ...]
    comparisons: tuple[Comparison, ...]


class Scorecard(BaseModel):
    """Every split's table, whether coverage is shown, and the limits."""

    model_config = ConfigDict(frozen=True)

    benchmark: str
    tables: tuple[SplitTable, ...]
    coverage_shown: bool
    coverage_note: str | None
    limits: tuple[str, ...] = LIMITS


class CardItem(BaseModel):
    """One line of the run card, and where its value came from."""

    model_config = ConfigDict(frozen=True)

    item: str
    value: str
    source: str


class RunCard(BaseModel):
    """What the results were measured with, item by item."""

    model_config = ConfigDict(frozen=True)

    items: tuple[CardItem, ...]

    def value(self, item: str) -> str:
        """The value of the item with this name. Raises ``KeyError`` when there is none."""
        for one in self.items:
            if one.item == item:
                return one.value
        raise KeyError(item)


# The scorecard.


def scorecard(
    runs: Sequence[GradedRun],
    sheets: Mapping[str, FactSheet],
    *,
    judge_agreement: float | None = None,
    judge_scripted: bool = False,
    cases_root: Path = CASES,
) -> Scorecard:
    """Build the scorecard from graded runs and the fact sheet of each of their cases.

    Checklist coverage is shown for a scripted judge, which is right by construction, or
    when ``judge_agreement`` with the author's labels meets
    :data:`~evals.version.JUDGE_MIN_AGREEMENT`. Otherwise it is withheld, and the
    scorecard says why.
    """
    by_split: dict[str, dict[str, list[GradedRun]]] = defaultdict(lambda: defaultdict(list))
    for run in runs:
        split = split_of(run.record.case_name, root=cases_root)
        by_split[split][run.record.arm].append(run)
    tables: list[SplitTable] = []
    for split in SPLITS:
        arms = by_split.get(split)
        if not arms:
            continue
        rows = tuple(_row(arm, arm_runs, sheets) for arm, arm_runs in sorted(arms.items()))
        tables.append(SplitTable(split=split, rows=rows, comparisons=_comparisons(arms, rows)))
    shown, note = _coverage_shown(judge_agreement, judge_scripted)
    return Scorecard(
        benchmark=f"{BENCHMARK_NAME} {BENCHMARK_VERSION}",
        tables=tuple(tables),
        coverage_shown=shown,
        coverage_note=note,
    )


def _coverage_shown(agreement: float | None, scripted: bool) -> tuple[bool, str | None]:
    if scripted:
        return (
            True,
            "Checklist coverage is from the scripted judge, which is right by construction.",
        )
    if agreement is None:
        return False, (
            "Checklist coverage is withheld: the judge's agreement with the author's labels "
            "is not available."
        )
    if agreement < JUDGE_MIN_AGREEMENT:
        return False, (
            f"Checklist coverage is withheld: the judge agreed with {agreement:.0%} of the "
            f"author's labels, below the minimum of {JUDGE_MIN_AGREEMENT:.0%}."
        )
    return True, None


def _by_case(runs: Iterable[GradedRun]) -> dict[str, list[GradedRun]]:
    grouped: dict[str, list[GradedRun]] = defaultdict(list)
    for run in runs:
        grouped[run.record.case_name].append(run)
    return dict(sorted(grouped.items()))


def _estimate(case_rates: Sequence[float], repeats: int) -> Estimate | None:
    if not case_rates:
        return None
    mean, low, high = stats.clustered_rate(case_rates)
    reason: str | None = None
    if repeats < 2:
        low, high, reason = None, None, "one repeat"
    elif low is None:
        reason = "one case" if len(case_rates) == 1 else "more cases than the t table holds"
    return Estimate(mean=mean, low=low, high=high, cases=len(case_rates), no_interval=reason)


def _ratio_per_case(
    groups: Mapping[str, list[GradedRun]],
    top: Callable[[QuestionMeasures], int],
    bottom: Callable[[QuestionMeasures], int],
    runs_of: Callable[[GradedRun], bool] = lambda run: True,
) -> list[float]:
    """For each case, ``top`` over ``bottom`` summed over its runs; cases with no bottom drop."""
    rates: list[float] = []
    for case_runs in groups.values():
        kept = [run.grade.measures for run in case_runs if runs_of(run)]
        below = sum(bottom(m) for m in kept)
        if below:
            rates.append(sum(top(m) for m in kept) / below)
    return rates


def _row(arm: str, runs: list[GradedRun], sheets: Mapping[str, FactSheet]) -> ArmRow:
    groups = _by_case(runs)
    repeats = max(len(case_runs) for case_runs in groups.values())

    def rate(case_rates: Sequence[float]) -> Estimate | None:
        return _estimate(case_rates, repeats)

    success_rates = [sum(r.grade.success for r in rs) / len(rs) for rs in groups.values()]
    verified = rate(success_rates)
    if verified is None:
        raise stats.NoData(f"the {arm} arm has no runs")

    reliability_rates = [
        stats.pass_k(sum(r.grade.success for r in rs), len(rs), RELIABILITY_K)
        for rs in groups.values()
    ]
    reliability = (
        None
        if any(value is None for value in reliability_rates)
        else sum(v for v in reliability_rates if v is not None) / len(reliability_rates)
    )

    costs = [run.record.cost_usd for run in runs]
    successes = sum(run.grade.success for run in runs)
    cost_known = all(cost is not None for cost in costs)
    cost = sum(c for c in costs if c is not None) / successes if cost_known and successes else None

    def class_of(name: str) -> str:
        return sheets[name].challenge_class

    nobody = {name: rs for name, rs in groups.items() if class_of(name) == "nobody-knows"}
    complete = {name: rs for name, rs in groups.items() if class_of(name) == "complete"}
    turns_per_case = [sum(r.grade.turns for r in rs) / len(rs) for rs in groups.values()]
    questions = sum(turns_per_case) / len(turns_per_case)

    coverage_rates: list[float] = []
    left_out = 0
    for case_runs in groups.values():
        delivered = [r.coverage for r in case_runs if r.record.package is not None]
        left_out += sum(1 for r in case_runs if r.record.package is None)
        shares = [share for share in delivered if share is not None]
        if shares:
            coverage_rates.append(sum(shares) / len(shares))

    return ArmRow(
        arm=arm,
        runs=len(runs),
        cases=len(groups),
        repeats=repeats,
        verified_design_rate=verified,
        policy_failures=sum(1 for run in runs if run.grade.outcome == "policy_failure"),
        reliability=reliability,
        cost_per_verified_design=cost,
        cost_known=cost_known,
        obtained_by_asking=rate(
            _ratio_per_case(
                groups,
                lambda m: m.critical_stakeholder_released,
                lambda m: m.critical_stakeholder_facts,
            )
        ),
        question_precision=rate(
            _ratio_per_case(
                groups, lambda m: m.questions_releasing_critical, lambda m: m.questions_asked
            )
        ),
        asked_and_used=rate(
            _ratio_per_case(
                groups,
                lambda m: m.critical_stakeholder_used,
                lambda m: m.critical_stakeholder_released,
            )
        ),
        asked_and_ignored=rate(
            _ratio_per_case(
                groups,
                lambda m: m.critical_stakeholder_ignored,
                lambda m: m.critical_stakeholder_released,
            )
        ),
        silent_assumptions=rate(
            _ratio_per_case(groups, lambda m: m.silent_assumptions, lambda m: m.critical_facts)
        ),
        asked_what_the_code_says=rate(
            _ratio_per_case(groups, lambda m: m.repo_facts_released, lambda m: m.repo_facts)
        ),
        needless_questions=rate(
            _ratio_per_case(groups, lambda m: m.low_facts_released, lambda m: m.low_facts)
        ),
        released_nothing=rate(
            _ratio_per_case(
                groups, lambda m: m.questions_releasing_nothing, lambda m: m.questions_asked
            )
        ),
        questions_per_case=questions,
        minutes_per_case=questions * MINUTES_PER_TURN,
        correct_blocks=rate(
            [
                sum(r.grade.outcome == "correct_block" for r in rs) / len(rs)
                for rs in nobody.values()
            ]
        ),
        delivered_on_nobody_knows=rate(
            [
                sum(
                    r.record.package is not None and r.record.package.status == "delivered"
                    for r in rs
                )
                / len(rs)
                for rs in nobody.values()
            ]
        ),
        questions_on_complete_cases=(
            sum(sum(r.grade.turns for r in rs) / len(rs) for rs in complete.values())
            / len(complete)
            if complete
            else None
        ),
        checklist_coverage=rate(coverage_rates),
        coverage_left_out=left_out,
        item_coverage=_item_coverage(runs),
        unexplained_values=tuple(
            f"{run.run_id}: {fact_id}" for run in runs for fact_id in run.grade.unexplained_values
        ),
        reruns=sum(run.record.reruns for run in runs),
    )


def _item_coverage(runs: Sequence[GradedRun]) -> dict[str, float]:
    """For each checklist item, the share of the arm's judged designs that covered it."""
    seen: dict[str, list[bool]] = defaultdict(list)
    for run in runs:
        for verdict in run.verdicts:
            seen[f"{run.record.case_name}/{verdict.item_id}"].append(verdict.present)
    return {item: sum(found) / len(found) for item, found in sorted(seen.items())}


def _comparisons(
    arms: Mapping[str, list[GradedRun]], rows: Sequence[ArmRow]
) -> tuple[Comparison, ...]:
    repeats = {row.arm: row.repeats for row in rows}
    found: list[Comparison] = []
    for (first, second), label in (
        (PRIMARY, "Primary comparison"),
        (ABLATION, "Ablation, exploratory"),
    ):
        if first not in arms or second not in arms:
            continue
        a, b = _by_case(arms[first]), _by_case(arms[second])
        shared = [name for name in a if name in b]
        if not shared:
            continue
        a_rates = [sum(r.grade.success for r in a[name]) / len(a[name]) for name in shared]
        b_rates = [sum(r.grade.success for r in b[name]) / len(b[name]) for name in shared]
        mean, low, high = stats.paired_difference(a_rates, b_rates)
        reason: str | None = None
        if min(repeats[first], repeats[second]) < 2:
            low, high, reason = None, None, "one repeat"
        elif low is None:
            reason = "one case" if len(shared) == 1 else "more cases than the t table holds"
        found.append(
            Comparison(
                label=label,
                first=first,
                second=second,
                difference=Estimate(
                    mean=mean, low=low, high=high, cases=len(shared), no_interval=reason
                ),
            )
        )
    return tuple(found)


def scorecard_text(card: Scorecard) -> str:
    """The scorecard as the command prints it."""
    lines = [f"Scorecard: {card.benchmark}", ""]
    for table in card.tables:
        lines += _table_text(table, card)
    if card.coverage_note:
        lines += [card.coverage_note, ""]
    lines.append("Limits:")
    lines += [f"- {limit}" for limit in card.limits]
    return "\n".join(lines)


def _table_text(table: SplitTable, card: Scorecard) -> list[str]:
    rows = table.rows
    lines = [f"Split: {table.split}", ""]
    lines += _grid(
        ["Arm", "Runs", "Verified design rate", "Gate line", "Reliability", "Cost per verified"],
        [
            [
                r.arm,
                str(r.runs),
                r.verified_design_rate.text(),
                str(r.policy_failures),
                _number(r.reliability),
                "-" if r.cost_per_verified_design is None else f"${r.cost_per_verified_design:.2f}",
            ]
            for r in rows
        ],
    )
    lines += ["", "How the session asked", ""]
    lines += _grid(
        ["Arm", "Obtained", "Precision", "Used", "Ignored", "Silent", "Code asked", "Needless"],
        [
            [
                r.arm,
                *(
                    _text(e)
                    for e in (
                        r.obtained_by_asking,
                        r.question_precision,
                        r.asked_and_used,
                        r.asked_and_ignored,
                        r.silent_assumptions,
                        r.asked_what_the_code_says,
                        r.needless_questions,
                    )
                ),
            ]
            for r in rows
        ],
    )
    lines.append("")
    lines += _grid(
        [
            "Arm",
            "Released nothing",
            "Questions",
            "Minutes",
            "Correct blocks",
            "Delivered, nobody knows",
            "On complete",
        ],
        [
            [
                r.arm,
                _text(r.released_nothing),
                f"{r.questions_per_case:.2f}",
                f"{r.minutes_per_case:.1f}",
                _text(r.correct_blocks),
                _text(r.delivered_on_nobody_knows),
                _number(r.questions_on_complete_cases),
            ]
            for r in rows
        ],
    )
    lines += ["", "Design quality: beside the outcome, never deciding it", ""]
    lines += _grid(
        ["Arm", "Checklist coverage", "No delivery, left out"],
        [
            [
                r.arm,
                _text(r.checklist_coverage) if card.coverage_shown else "withheld",
                str(r.coverage_left_out),
            ]
            for r in rows
        ],
    )
    lines.append("")
    for comparison in table.comparisons:
        d = comparison.difference
        lines.append(
            f"{comparison.label}: {comparison.first} minus {comparison.second}, verified "
            f"design rate, {d.text()} over {d.cases} case(s)"
            + (f"; no interval: {d.no_interval}" if d.no_interval else "")
        )
    reasons = sorted({r.verified_design_rate.no_interval or "" for r in rows} - {""})
    for reason in reasons:
        arms = ", ".join(r.arm for r in rows if r.verified_design_rate.no_interval == reason)
        lines.append(f"No interval: {reason}, for {arms}.")
    for r in rows:
        for value in r.unexplained_values:
            lines.append(f"Unexplained value, to read by hand: {value}")
    if card.coverage_shown:
        for r in rows:
            for item, share in r.item_coverage.items():
                lines.append(f"Item coverage: {r.arm} {item} {share:.2f}")
    lines.append(f"Runs rerun: {sum(r.reruns for r in rows)}")
    lines.append(
        "Unknown cost, so no cost per verified design: "
        + (", ".join(r.arm for r in rows if not r.cost_known) or "none")
    )
    lines.append("")
    return lines


def _text(estimate: Estimate | None) -> str:
    return "-" if estimate is None else estimate.text()


def _number(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def _grid(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    widths = [max(len(row[i]) for row in [header, *rows]) for i in range(len(header))]
    return [
        "  ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=True)).rstrip()
        for row in [header, *rows]
    ]


# The run card.


def run_card(
    splits: Sequence[str],
    repeat: int,
    arms: Sequence[str],
    *,
    judge: str,
    cases_root: Path = CASES,
    labels: Path = LABELS,
    reference: Path = REFERENCE,
    skill_folders: Sequence[Path] = SKILL_FOLDERS,
    probe: Probe | None = None,
    now: datetime | None = None,
) -> RunCard:
    """The run card for an experiment on ``splits`` with ``repeat`` runs of ``arms``.

    ``judge`` is ``"real"`` or ``"scripted"``. ``probe`` runs a command, such as
    ``git rev-parse HEAD``; by default it runs the real one. ``claude auth status`` is
    asked only when the ``claude-code`` arm ran.
    """
    run = probe or _probe
    rounds, per_round = designer_question_caps()
    judge_label = _judge_labels(labels / "checklist-judge.json")
    items: list[CardItem] = [
        CardItem(
            item="Benchmark",
            value=f"{BENCHMARK_NAME} {BENCHMARK_VERSION}",
            source="evals/version.py",
        ),
        CardItem(item="Spending cap for a run", value=f"${CAP_USD:.2f}", source="evals/version.py"),
        CardItem(
            item="Time limit for a run",
            value=f"{TIME_LIMIT_SECONDS:g} seconds",
            source="evals/version.py",
        ),
        CardItem(item="Factory model", value=PINNED_MODEL, source="evals/version.py"),
        CardItem(item="Claude Code model", value=CLAUDE_CODE_MODEL, source="evals/version.py"),
        CardItem(item="Picker model", value=PICKER_MODEL, source="evals/version.py"),
        CardItem(item="Minutes for a turn", value=str(MINUTES_PER_TURN), source="evals/version.py"),
        CardItem(item="Picker prompt hash", value=_sha(PICKER_PROMPT), source="PICKER_PROMPT"),
        CardItem(item="Judge prompt hash", value=_sha(JUDGE_PROMPT), source="JUDGE_PROMPT"),
        CardItem(
            item="Port tool descriptions hash",
            value=_sha(ASK_DESCRIPTION + "\n" + DELIVER_DESCRIPTION),
            source="evals/port_tools.py",
        ),
        CardItem(
            item="Checklist judge",
            value=f"{judge}, {JUDGE_MODEL}" if judge == "real" else judge,
            source="the command's arguments",
        ),
        CardItem(
            item="Checklist judge's agreement with the author's labels",
            value=NOT_AVAILABLE
            if judge_label is None
            else f"{judge_label[0]:.2f} on {judge_label[1]} pairs",
            source="evals/labels/checklist-judge.json",
        ),
    ]
    for split in splits:
        for case in cases(split, root=cases_root):
            items.append(
                CardItem(
                    item=f"Case {split}/{case.name}",
                    value=case_hash(split, case.name, root=cases_root),
                    source="the case files",
                )
            )
            sheet = facts.load(split, case.name, root=cases_root)
            items.append(
                CardItem(
                    item=f"Checklist {split}/{case.name}",
                    value=_sha(
                        json.dumps([i.model_dump() for i in sheet.checklist], sort_keys=True)
                    ),
                    source="the fact sheet",
                )
            )
    for rung in ("r0", "r1"):
        folder = reference / rung
        items.append(
            CardItem(
                item=f"Reference app {rung}",
                value=_folder_hash([folder]) if folder.is_dir() else NOT_AVAILABLE,
                source=f"evals/reference/{rung}/",
            )
        )
    digest = run(["docker", "image", "inspect", "--format", "{{.Id}}", SANDBOX_IMAGE])
    items += [
        CardItem(
            item="Sandbox image digest",
            value=digest or NOT_AVAILABLE,
            source="docker image inspect",
        ),
        CardItem(
            item="Pydantic AI Harness version",
            value=_version("pydantic-ai-harness"),
            source="the installed package",
        ),
        CardItem(item="Splits", value=", ".join(splits), source="the command's arguments"),
        CardItem(item="Repeats", value=str(repeat), source="the command's arguments"),
        CardItem(item="Arms", value=", ".join(arms), source="the command's arguments"),
        CardItem(
            item="Factory commit",
            value=run(["git", "-C", str(ROOT), "rev-parse", "HEAD"]) or NOT_AVAILABLE,
            source="git rev-parse HEAD",
        ),
        CardItem(
            item="Factory skill library hash",
            value=_folder_hash(skill_folders, pattern="SKILL.md")
            if any(f.is_dir() for f in skill_folders)
            else NOT_AVAILABLE,
            source="every SKILL.md of Elephant Goldfish, then of the software package",
        ),
        CardItem(
            item="Designer's cap on rounds of questions, and on questions in a round",
            value=f"{rounds} rounds, {per_round} questions",
            source="AskPerson, as the Design station builds it",
        ),
        CardItem(
            item="Claude Code version",
            value=CLAUDE_CODE_VERSION,
            source="the sandbox image, evals/version.py",
        ),
        CardItem(
            item="Claude Code options",
            value=" ".join(
                claude_options(model=CLAUDE_CODE_MODEL, cap_usd=CAP_USD, config="<config>")
            ),
            source="the Claude Code adapter",
        ),
        CardItem(
            item="Claude Code sign-in plan",
            value=(run(["claude", "auth", "status"]) or NOT_AVAILABLE)
            if CLAUDE_CODE_ARM in arms
            else NOT_AVAILABLE,
            source="claude auth status",
        ),
        CardItem(
            item="Simulator pass rates, leak test and answer test",
            value=_simulator_tests(labels / "simulator-tests.json"),
            source="evals/labels/simulator-tests.json",
        ),
        CardItem(
            item="Simulator error rate on the labelled sample",
            value=_fidelity(labels / "simulator-fidelity.json"),
            source="evals/labels/simulator-fidelity.json",
        ),
        CardItem(
            item="Date",
            value=(now or datetime.now(UTC)).isoformat(timespec="seconds"),
            source="the clock",
        ),
    ]
    return RunCard(items=tuple(items))


def run_card_text(card: RunCard) -> str:
    """The run card as the command prints it."""
    return "\n".join(["Run card", *(f"{i.item}: {i.value}" for i in card.items)])


def judge_agreement(labels: Path = LABELS) -> float | None:
    """The checklist judge's agreement with the author's labels, or ``None`` when not known."""
    found = _judge_labels(labels / "checklist-judge.json")
    return None if found is None else found[0]


def write_reports(out: Path, score: Scorecard, card: RunCard) -> None:
    """Write ``scorecard.json`` and ``run-card.json`` under ``out``."""
    out.mkdir(parents=True, exist_ok=True)
    (out / SCORECARD_FILE).write_text(score.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (out / RUN_CARD_FILE).write_text(card.model_dump_json(indent=2) + "\n", encoding="utf-8")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _folder_hash(folders: Sequence[Path], pattern: str = "*") -> str:
    """SHA-256 over each matching file's path, relative to its folder, and bytes.

    The folders are taken in the order given, and each folder's files in path order.
    """
    digest = hashlib.sha256()
    for folder in folders:
        if not folder.is_dir():
            continue
        files = sorted(
            p for p in folder.rglob(pattern) if p.is_file() and "node_modules" not in p.parts
        )
        for path in files:
            digest.update(path.relative_to(folder).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _version(package: str) -> str:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return NOT_AVAILABLE


def _probe(command: Sequence[str]) -> str | None:
    try:
        result = subprocess.run(
            list(command), capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip().splitlines()[0] if result.stdout.strip() else None


class _JudgeLabel(BaseModel):
    judge: bool | None = None
    author_label: bool | None = None


class _SimulatorTest(BaseModel):
    rate: float


class _SimulatorTests(BaseModel):
    prompt_sha256: str
    tests: dict[str, _SimulatorTest]


class _FidelityEntry(BaseModel):
    all_asked_picked: bool | None = None
    nothing_extra: bool | None = None


_LABELS = TypeAdapter(list[_JudgeLabel])
_FIDELITY = TypeAdapter(list[_FidelityEntry])
_TESTS = TypeAdapter(_SimulatorTests)


def _read(path: Path, adapter: TypeAdapter[T]) -> T | None:
    """The file's contents in the shape ``adapter`` reads, or ``None`` when it cannot be read."""
    try:
        return adapter.validate_json(path.read_bytes())
    except (OSError, ValidationError):
        return None


def _judge_labels(path: Path) -> tuple[float, int] | None:
    """Agreement and size from the author's labels: each entry's ``judge`` and ``author_label``."""
    entries = _read(path, _LABELS) or []
    labelled = [
        (e.judge, e.author_label)
        for e in entries
        if e.judge is not None and e.author_label is not None
    ]
    if not labelled:
        return None
    return sum(j == a for j, a in labelled) / len(labelled), len(labelled)


def _simulator_tests(path: Path) -> str:
    """The two pass rates, when the file's prompt hash is the current picker prompt's."""
    found = _read(path, _TESTS)
    if found is None or found.prompt_sha256 != _sha(PICKER_PROMPT) or not found.tests:
        return NOT_AVAILABLE
    return ", ".join(f"{name} {test.rate:.2f}" for name, test in sorted(found.tests.items()))


def _fidelity(path: Path) -> str:
    """The share of labelled entries where either label is no, and the number of entries."""
    entries = _read(path, _FIDELITY) or []
    labelled = [
        (e.all_asked_picked, e.nothing_extra)
        for e in entries
        if e.all_asked_picked is not None and e.nothing_extra is not None
    ]
    if not labelled:
        return NOT_AVAILABLE
    wrong = sum(not (picked and extra) for picked, extra in labelled)
    return f"{wrong / len(labelled):.2f} on {len(labelled)} entries"
