"""What every suite's report is built from: estimates across cases, the run card, and the
report evaluators an experiment runs on Pydantic Evals' report.

Each rate is computed for each case first, then across cases with :func:`clustered_rate`,
because the case is the unit of analysis. With one repeat, a rate has no interval.

The run card says what results were measured with. An item whose source is missing says
:data:`NOT_AVAILABLE`.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import subprocess
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic_evals.evaluators import ReportEvaluator, ReportEvaluatorContext
from pydantic_evals.reporting.analyses import ReportAnalysis, ScalarResult, TableResult

from .stats import clustered_rate, pass_k

NOT_AVAILABLE = "not available"

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
        """The estimate as a report prints it."""
        if self.low is None or self.high is None:
            return f"{self.mean:.2f}"
        return f"{self.mean:.2f} [{self.low:.2f}, {self.high:.2f}]"


def estimate(case_rates: Sequence[float], repeats: int) -> Estimate | None:
    """The estimate across cases of each case's own rate, or ``None`` with no case.

    With one repeat there is no interval, since a case's rate is then a single run.
    """
    if not case_rates:
        return None
    mean, low, high = clustered_rate(case_rates)
    reason: str | None = None
    if repeats < 2:
        low, high, reason = None, None, "one repeat"
    elif low is None:
        reason = "one case" if len(case_rates) == 1 else "more cases than the t table holds"
    return Estimate(mean=mean, low=low, high=high, cases=len(case_rates), no_interval=reason)


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

    def text(self) -> str:
        """The run card as a command prints it."""
        return "\n".join(["Run card", *(f"{i.item}: {i.value}" for i in self.items)])


def grid(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    """Lines of a plain-text table, each column as wide as its widest cell."""
    widths = [max(len(row[i]) for row in [header, *rows]) for i in range(len(header))]
    return [
        "  ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=True)).rstrip()
        for row in [header, *rows]
    ]


def sha256_text(text: str) -> str:
    """The SHA-256 hash of a text's UTF-8 bytes, as hex."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def folder_hash(
    folders: Sequence[Path], pattern: str = "*", *, left_out: frozenset[str] = frozenset()
) -> str:
    """SHA-256 over each matching file's path, relative to its folder, and bytes.

    The folders are taken in the order given, and each folder's files in path order. A
    file under a folder named in ``left_out`` is skipped.
    """
    digest = hashlib.sha256()
    for folder in folders:
        if not folder.is_dir():
            continue
        files = sorted(
            p
            for p in folder.rglob(pattern)
            if p.is_file() and not left_out.intersection(p.relative_to(folder).parts)
        )
        for path in files:
            digest.update(path.relative_to(folder).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def package_version(package: str) -> str:
    """The installed version of ``package``, or :data:`NOT_AVAILABLE`."""
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return NOT_AVAILABLE


def probe(command: Sequence[str]) -> str | None:
    """The first line ``command`` printed, or ``None`` when it could not run or failed."""
    try:
        result = subprocess.run(
            list(command), capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip().splitlines()[0] if result.stdout.strip() else None


def _successes_by_case(ctx: ReportEvaluatorContext[Any, Any, Any]) -> dict[str, list[bool]]:
    """Each case's runs, as whether each was a success, from the ``success`` result."""
    by_case: dict[str, list[bool]] = defaultdict(list)
    for case in ctx.report.cases:
        found = case.scores.get("success") or case.labels.get("success")
        value: Any = found.value if found is not None else None
        if value is None:
            asserted = case.assertions.get("success")
            value = asserted.value if asserted is not None else None
        if value is not None:
            by_case[case.source_case_name or case.name].append(bool(value))
    return by_case


@dataclass(repr=False)
class SuccessRate(ReportEvaluator[Any, Any, Any]):
    """The success rate across cases, each case's own rate first, with its 95 per cent
    interval. A run's success is the ``success`` value its grader returned."""

    def evaluate(self, ctx: ReportEvaluatorContext[Any, Any, Any]) -> list[ReportAnalysis]:
        by_case = _successes_by_case(ctx)
        rates = [sum(runs) / len(runs) for runs in by_case.values() if runs]
        repeats = min((len(runs) for runs in by_case.values()), default=0)
        found = estimate(rates, repeats)
        if found is None:
            return []
        return [
            ScalarResult(title="success rate", value=found.mean, description=found.text()),
            TableResult(
                title="success by case",
                columns=["case", "runs", "successes"],
                rows=[[name, len(runs), sum(runs)] for name, runs in sorted(by_case.items())],
            ),
        ]


@dataclass(repr=False)
class PassK(ReportEvaluator[Any, Any, Any]):
    """Reliability: the chance that ``k`` attempts at a case all succeed, averaged over
    cases. Cases with fewer than ``k`` runs are left out."""

    k: int = 3

    def evaluate(self, ctx: ReportEvaluatorContext[Any, Any, Any]) -> list[ReportAnalysis]:
        values = [
            found
            for runs in _successes_by_case(ctx).values()
            if (found := pass_k(sum(runs), len(runs), self.k)) is not None
        ]
        if not values:
            return []
        return [ScalarResult(title=f"pass^{self.k}", value=sum(values) / len(values))]
