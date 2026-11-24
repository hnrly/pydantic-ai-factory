"""Run a suite's arms on every case of a split, grade each run and write ``results.json``.

A :class:`Suite` says what is measured: its cases and fact sheets, its arms, its judge
and its limits. :func:`run_experiment` runs it.

For each arm, one Pydantic Evals ``Dataset`` holds one ``Case`` for each case of the
split. The case's name and its input are both the case name, so every arm's runs line up
for paired analysis. The task builds the system, calls :func:`run_case` and returns the
record. The evaluator is :class:`Graded`; the report evaluators are :class:`SuccessRate`
and :class:`PassK`. Runs go one at a time.

An experiment continues where it stopped: before each arm it counts the records already
in ``<out>/<arm>/<case>/`` and runs only what is missing to reach ``repeat``. It stops,
and returns 2, after three runs in a row end as ``infrastructure``. When the runs end,
and after :func:`regrade`, the suite's own report is printed and written.

An arm that reads files is given the case's workspace, built by the suite, and runs in a
Docker container: the experiment refuses to start one when ``docker info`` fails.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import anyio
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_evals import Case as EvalsCase
from pydantic_evals import Dataset

from ._checklist import ChecklistJudge, scripted_judge
from ._contract import RunRecord, SystemAdapter
from ._facts import FactSheet
from ._graded import Graded, GradedRun, JudgeFor, grade_run
from ._harness import Case, WorkspaceFor, read_record, run_case
from ._report import PassK, SuccessRate
from ._sandbox import docker_available
from ._simulator import Pick, picker_agent, scripted_picker

Judge = Literal["real", "scripted"]
"""Which checklist judge graded the runs: the suite's pinned one, or the scripted one."""

STOP_AFTER = 3
"""An experiment stops after this many runs in a row end as an infrastructure error."""

RESULTS_FILE = "results.json"

DOCKER_NEEDED = "Docker is needed to run the {arms} arm(s), and `docker info` failed."

LEFT_OUT = "Docker is not running, so the {arms} arm(s) were left out. Start Docker to run them."


@dataclass(frozen=True)
class ArmContext:
    """What an arm is built with for one run: whether it is offline, and the model to run."""

    offline: bool
    model: Model | str


@dataclass(frozen=True, kw_only=True)
class Arm:
    """One system under test, as a suite names it.

    ``build`` builds the system for one case. ``offline`` and ``real`` say in which modes
    it may run: a scripted fixture runs only offline, and a real program only on real
    models. ``reads_files`` gives it the case's workspace, in a Docker container.
    """

    name: str
    build: Callable[[Case, FactSheet, ArmContext], SystemAdapter]
    offline: bool = True
    real: bool = True
    reads_files: bool = False


Report = Callable[[Path, Sequence[GradedRun], Judge], None]
"""Prints and writes a suite's own report of every graded run in an output folder."""


@dataclass(frozen=True, kw_only=True)
class Suite:
    """What an experiment measures, and how.

    * ``cases`` gives a split's cases, ``sheet_for`` a case's fact sheet, by name;
    * ``arms`` are the systems under test, in the order they run;
    * ``judge_for`` builds the pinned checklist judge for a case's sheet;
    * ``picker_model`` is the stakeholder's pinned picking model;
    * ``workspace_for`` builds a case's workspace for an arm that reads files, and
      ``check_case`` refuses a case that cannot run before its run folder is made;
    * ``report`` prints and writes the suite's own report.
    """

    name: str
    splits: tuple[str, ...]
    cases: Callable[[str], Sequence[Case]]
    sheet_for: Callable[[str], FactSheet]
    arms: tuple[Arm, ...]
    judge_for: JudgeFor
    picker_model: Model | str
    model: Model | str
    cap_usd: float
    time_limit_seconds: float
    workspace_for: WorkspaceFor | None = None
    check_case: Callable[[Case], None] | None = None
    report: Report | None = None
    offline_note: str = ""

    def arm(self, name: str) -> Arm:
        """The arm with this name. Raises ``KeyError`` when there is none."""
        for arm in self.arms:
            if arm.name == name:
                return arm
        raise KeyError(name)

    def default_arms(self, offline: bool) -> tuple[list[str], list[str]]:
        """Every arm the mode can run here, for an experiment that names none, and those
        left out because Docker is not running."""
        names = [arm.name for arm in self.arms if (arm.offline if offline else arm.real)]
        if not offline:
            return names, []
        needing = [name for name in names if self.arm(name).reads_files]
        if needing and not docker_available():
            return [name for name in names if name not in needing], needing
        return names, []


class RunsStopped(RuntimeError):
    """Too many runs in a row ended as an infrastructure error."""


@dataclass
class _Progress:
    streak: int = 0
    done: list[str] = field(default_factory=list[str])


def run_experiment(
    suite: Suite,
    arms: Sequence[str],
    split: str,
    repeat: int,
    out: Path,
    *,
    offline: bool,
    model: str | Model | None = None,
    judge: Judge | None = None,
) -> int:
    """Run ``arms`` of ``suite`` on every case of ``split`` until each has ``repeat`` runs.

    Returns 0 when every run was graded, whatever the outcomes; 1 when a run could not be
    graded; and 2 when the arms cannot run in this mode or the experiment stopped.
    ``model`` runs the arms that take one; by default the suite's own.
    """
    if repeat < 1:
        print(f"--repeat is at least 1, not {repeat}")
        return 2
    if split not in suite.splits:
        print(f"{split!r} is not a split; the splits are {', '.join(suite.splits)}")
        return 2
    chosen, left_out = (list(arms), []) if arms else suite.default_arms(offline)
    known = [arm.name for arm in suite.arms]
    for name in chosen:
        if name not in known:
            print(f"There is no arm called {name!r}. The arms are {', '.join(known)}.")
            return 2
        arm = suite.arm(name)
        if not arm.real and not offline:
            print(f"The {name} arm is a scripted test fixture and runs only with --offline.")
            return 2
        if not arm.offline and offline:
            print(f"The {name} arm starts the real program on a real model, so not with --offline.")
            return 2
    refusal = docker_refusal(suite, chosen)
    if refusal is not None:
        print(refusal)
        return 2
    judge_kind: Judge = judge or ("scripted" if offline else "real")
    judge_for: JudgeFor = suite.judge_for if judge_kind == "real" else scripted_judge_for
    code = anyio.run(
        _run,
        suite,
        chosen,
        split,
        repeat,
        out,
        offline,
        judge_for,
        model or suite.model,
        judge_kind,
    )
    if left_out:
        print(LEFT_OUT.format(arms=", ".join(left_out)))
    return code


def docker_refusal(suite: Suite, arms: Sequence[str]) -> str | None:
    """Why ``arms`` cannot start here, or ``None`` when they can.

    ``docker info`` is asked only when an arm that reads files is chosen.
    """
    needing = [name for name in arms if suite.arm(name).reads_files]
    if needing and not docker_available():
        return DOCKER_NEEDED.format(arms=", ".join(needing))
    return None


def regrade(suite: Suite, out: Path, *, judge: Judge = "real") -> int:
    """Grade every record in ``out`` again with its stored verdicts, and call no model.

    ``judge`` says which judge gave the stored verdicts, for the suite's report.
    """
    folders = run_folders(out)
    if not folders:
        print(f"There are no runs to regrade in {out}.")
        return 2
    graded = anyio.run(_grade_all, suite, folders, None)
    _write_results(out, graded)
    for run in graded:
        print(run.line())
    if suite.report is not None:
        suite.report(out, graded, judge)
    return 0


async def _run(
    suite: Suite,
    arms: list[str],
    split: str,
    repeat: int,
    out: Path,
    offline: bool,
    judge_for: JudgeFor,
    model: str | Model,
    judge: Judge,
) -> int:
    split_cases = list(suite.cases(split))
    by_name = {case.name: case for case in split_cases}
    progress = _Progress()
    ungraded = 0
    for arm in arms:
        missing: dict[int, list[str]] = defaultdict(list)
        for case in split_cases:
            need = repeat - len(list((out / arm / case.name).glob("*/record.json")))
            if need > 0:
                missing[need].append(case.name)
        for need, names in sorted(missing.items()):
            dataset = Dataset[str, RunRecord, None](
                name=f"{split}-{arm}",
                cases=[EvalsCase(name=name, inputs=name) for name in names],
                evaluators=[Graded(out=out, judge_for=judge_for, sheet_for=suite.sheet_for)],
                report_evaluators=[SuccessRate(), PassK()],
            )
            report = await dataset.evaluate(
                _task(suite, suite.arm(arm), by_name, out, offline, progress, model),
                name=arm,
                max_concurrency=1,
                progress=False,
                repeat=need,
            )
            ungraded += len(report.failures)
            ungraded += sum(1 for case in report.cases if case.evaluator_failures)
            if progress.streak >= STOP_AFTER:
                break
        if progress.streak >= STOP_AFTER:
            break

    graded = await _grade_all(suite, run_folders(out), judge_for)
    _write_results(out, graded)
    for run in graded:
        if run.run_id in progress.done:
            print(run.line())
    if not progress.done:
        print(f"Every case already has {repeat} run(s) in {out}. Nothing was run.")
    if suite.report is not None:
        suite.report(out, graded, judge)
    if offline and suite.offline_note:
        print(suite.offline_note)
    if progress.streak >= STOP_AFTER:
        print(
            f"Stopped: {STOP_AFTER} runs in a row ended as an infrastructure error. "
            "Run the same command again to carry on."
        )
        return 2
    return 1 if ungraded else 0


def _task(
    suite: Suite,
    arm: Arm,
    by_name: dict[str, Case],
    out: Path,
    offline: bool,
    progress: _Progress,
    model: str | Model,
):
    async def task(case_name: str) -> RunRecord:
        if progress.streak >= STOP_AFTER:
            raise RunsStopped(f"{STOP_AFTER} runs in a row ended as an infrastructure error")
        sheet = suite.sheet_for(case_name)
        case = by_name[case_name]
        if arm.reads_files and suite.check_case is not None:
            suite.check_case(case)
        picker: Agent[None, Pick] = (
            scripted_picker(sheet) if offline else picker_agent(suite.picker_model)
        )
        record = await run_case(
            arm.build(case, sheet, ArmContext(offline=offline, model=model)),
            case,
            sheet,
            arm=arm.name,
            out=out,
            picker=picker,
            cap_usd=suite.cap_usd,
            time_limit_seconds=suite.time_limit_seconds,
            workspace_for=suite.workspace_for if arm.reads_files else None,
        )
        progress.streak = progress.streak + 1 if record.end == "infrastructure" else 0
        progress.done.append(f"{record.arm}/{record.case_name}/{record.repeat}")
        return record

    return task


def run_folders(out: Path) -> list[Path]:
    """Every run folder under ``out``, sorted by arm, case and repeat."""
    found = [path.parent for path in out.glob("*/*/*/record.json")]
    return sorted(found, key=lambda p: (p.parent.parent.name, p.parent.name, _number(p.name)))


def _number(name: str) -> int:
    return int(name) if name.isdigit() else 0


async def _grade_all(
    suite: Suite, folders: Sequence[Path], judge_for: JudgeFor | None
) -> list[GradedRun]:
    return [
        await grade_run(read_record(folder), folder, judge_for, sheet_for=suite.sheet_for)
        for folder in folders
    ]


def _write_results(out: Path, graded: Sequence[GradedRun]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    data = [run.summary() for run in graded]
    (out / RESULTS_FILE).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def scripted_judge_for(sheet: FactSheet) -> ChecklistJudge:
    """Each checklist item is its own key phrase, as the scripted systems write it."""
    return scripted_judge({item.item: item.item for item in sheet.checklist})
