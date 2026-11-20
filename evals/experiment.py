"""Run every arm on every case of a split, grade each run and write ``results.json``.

For each arm, one Pydantic Evals ``Dataset`` holds one ``Case`` for each case of the
split. The case's name and its input are both the case name, so every arm's runs line up
for paired analysis. The task builds the system, calls :func:`~evals.harness.run_case` and
returns the record; the only evaluator is :class:`~evals.evaluators.Graded`. Runs go one
at a time.

An experiment continues where it stopped: before each arm it counts the records already
in ``<out>/<arm>/<case>/`` and runs only what is missing to reach ``repeat``. It stops,
and returns 2, after three runs in a row end as ``infrastructure``.

The scripted systems run offline only. They read no file, so they need neither Docker
nor a repository snapshot. The two factory arms, ``factory`` and ``factory-simple``, read
the case's repository, so each run is in a Docker container: the experiment refuses to
start one when ``docker info`` fails. Offline they run on the scripted designer; otherwise
on ``--model``, by default :data:`~evals.version.PINNED_MODEL`. With no arm named, an
offline experiment runs every arm it can: the factory arms too when Docker is running.

The ``claude-code`` arm starts the real Claude Code program in a Docker container, on
the pinned model, so it runs only on real models and never offline. ``--model`` does not
change it.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import anyio
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_evals import Case, Dataset

from evals import facts
from evals.adapters.claude_code import CLAUDE_CODE_ARM, ClaudeCodeAdapter
from evals.adapters.factory import FACTORY_ARM, SIMPLE_ARM, FactoryAdapter
from evals.adapters.scripted import ARM_PREFIX, KINDS, Kind, scripted_designer, scripted_system
from evals.cases import PublicCase, cases
from evals.checklist import JudgeAnswer, judge_agent, scripted_judge
from evals.contract import RunRecord, SystemAdapter
from evals.evaluators import Graded, GradedRun, JudgeFor, grade_run
from evals.harness import read_record, run_case
from evals.sandbox import docker_available, overlay_of
from evals.simulator import Pick, haiku_picker, scripted_picker
from evals.version import CAP_USD, PINNED_MODEL, TIME_LIMIT_SECONDS

Judge = Literal["real", "scripted"]

SCRIPTED_ARMS: dict[str, Kind] = {ARM_PREFIX + kind: kind for kind in KINDS}
"""The arms of the scripted systems. They are test fixtures and run only offline."""

FACTORY_ARMS: dict[str, bool] = {FACTORY_ARM: True, SIMPLE_ARM: False}
"""The two factory arms, and whether each has the review. They run offline and on real models."""

REAL_ONLY_ARMS: frozenset[str] = frozenset({CLAUDE_CODE_ARM})
"""The arms that start a real program on a real model, so never run offline."""

SANDBOX_ARMS: frozenset[str] = frozenset({*FACTORY_ARMS, CLAUDE_CODE_ARM})
"""The arms that read files, each in its own Docker container."""

DOCKER_NEEDED = "Docker is needed to run the {arms} arm(s), and `docker info` failed."

LEFT_OUT = "Docker is not running, so the {arms} arm(s) were left out. Start Docker to run them."

STOP_AFTER = 3
"""An experiment stops after this many runs in a row end as an infrastructure error."""

RESULTS_FILE = "results.json"

OFFLINE_NOTE = (
    "Offline: scripted systems and a scripted designer on a scripted picker. These numbers "
    "show the harness works. They say nothing about real models."
)


class RunsStopped(RuntimeError):
    """Too many runs in a row ended as an infrastructure error."""


@dataclass
class _Progress:
    streak: int = 0
    done: list[str] = field(default_factory=list[str])


def run_experiment(
    arms: Sequence[str],
    split: str,
    repeat: int,
    out: Path,
    *,
    offline: bool,
    model: str | Model | None = None,
    judge: Judge | None = None,
) -> int:
    """Run ``arms`` on every case of ``split`` until each has ``repeat`` runs.

    Returns 0 when every run was graded, whatever the outcomes; 1 when a run could not be
    graded; and 2 when the arms cannot run in this mode or the experiment stopped.
    ``model``, a name or a model, runs the factory arms on real models; by default
    :data:`~evals.version.PINNED_MODEL`. Offline they run on the scripted designer.
    """
    if repeat < 1:
        print(f"--repeat is at least 1, not {repeat}")
        return 2
    chosen, left_out = (list(arms), []) if arms else default_arms(offline)
    known = [*SCRIPTED_ARMS, *FACTORY_ARMS, CLAUDE_CODE_ARM]
    for arm in chosen:
        if arm not in known:
            print(f"There is no arm called {arm!r}. The arms are {', '.join(known)}.")
            return 2
        if arm in SCRIPTED_ARMS and not offline:
            print(f"The {arm} arm is a scripted test fixture and runs only with --offline.")
            return 2
        if arm in REAL_ONLY_ARMS and offline:
            print(f"The {arm} arm starts the real program on a real model, so not with --offline.")
            return 2
    refusal = docker_refusal(chosen)
    if refusal is not None:
        print(refusal)
        return 2
    usereal_judge_for = (judge or ("scripted" if offline else "real")) == "real"
    judge_for: JudgeFor = real_judge_for if usereal_judge_for else scripted_judge_for
    code = anyio.run(_run, chosen, split, repeat, out, offline, judge_for, model or PINNED_MODEL)
    if left_out:
        print(LEFT_OUT.format(arms=", ".join(left_out)))
    return code


def default_arms(offline: bool) -> tuple[list[str], list[str]]:
    """Every arm the mode can run here, for an experiment that names none, and those left out.

    Offline, that is the scripted systems, and the factory arms when Docker is running;
    when it is not, they are left out, and the experiment says so when it ends. On real
    models it is the Claude Code arm and the factory arms.
    """
    if not offline:
        return [CLAUDE_CODE_ARM, *FACTORY_ARMS], []
    if docker_available():
        return [*SCRIPTED_ARMS, *FACTORY_ARMS], []
    return list(SCRIPTED_ARMS), list(FACTORY_ARMS)


def docker_refusal(
    arms: Sequence[str], *, sandbox_arms: frozenset[str] = SANDBOX_ARMS
) -> str | None:
    """Why ``arms`` cannot start here, or ``None`` when they can.

    An arm in ``sandbox_arms`` needs Docker. ``docker info`` is asked only when one is
    chosen, so the scripted systems run without Docker.
    """
    needing = [arm for arm in arms if arm in sandbox_arms]
    if needing and not docker_available():
        return DOCKER_NEEDED.format(arms=", ".join(needing))
    return None


def regrade(out: Path) -> int:
    """Grade every record in ``out`` again with its stored verdicts, and call no model."""
    folders = _run_folders(out)
    if not folders:
        print(f"There are no runs to regrade in {out}.")
        return 2
    graded = anyio.run(_grade_all, folders, None)
    _write_results(out, graded)
    for run in graded:
        print(run.line())
    return 0


async def _run(
    arms: list[str],
    split: str,
    repeat: int,
    out: Path,
    offline: bool,
    judge_for: JudgeFor,
    model: str | Model,
) -> int:
    split_cases = cases(split)
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
                cases=[Case(name=name, inputs=name) for name in names],
                evaluators=[Graded(out=out, judge_for=judge_for)],
            )
            report = await dataset.evaluate(
                _task(arm, split, by_name, out, offline, progress, model),
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

    graded = await _grade_all(_run_folders(out), judge_for)
    _write_results(out, graded)
    for run in graded:
        if run.run_id in progress.done:
            print(run.line())
    if not progress.done:
        print(f"Every case already has {repeat} run(s) in {out}. Nothing was run.")
    if offline:
        print(OFFLINE_NOTE)
    if progress.streak >= STOP_AFTER:
        print(
            f"Stopped: {STOP_AFTER} runs in a row ended as an infrastructure error. "
            "Run the same command again to carry on."
        )
        return 2
    return 1 if ungraded else 0


def _task(
    arm: str,
    split: str,
    by_name: dict[str, PublicCase],
    out: Path,
    offline: bool,
    progress: _Progress,
    model: str | Model,
):
    async def task(case_name: str) -> RunRecord:
        if progress.streak >= STOP_AFTER:
            raise RunsStopped(f"{STOP_AFTER} runs in a row ended as an infrastructure error")
        sheet = facts.load(split, case_name)
        case = by_name[case_name]
        picker: Agent[None, Pick] = scripted_picker(sheet) if offline else haiku_picker()
        record = await run_case(
            _system(arm, case, sheet, offline, model),
            case,
            sheet,
            arm=arm,
            out=out,
            picker=picker,
            cap_usd=CAP_USD,
            time_limit_seconds=TIME_LIMIT_SECONDS,
            reads_files=arm in SANDBOX_ARMS,
        )
        progress.streak = progress.streak + 1 if record.end == "infrastructure" else 0
        progress.done.append(f"{record.arm}/{record.case_name}/{record.repeat}")
        return record

    return task


def _system(
    arm: str, case: PublicCase, sheet: facts.FactSheet, offline: bool, model: str | Model
) -> SystemAdapter:
    """The system an arm runs on one case."""
    if arm in SCRIPTED_ARMS:
        return scripted_system(SCRIPTED_ARMS[arm], sheet)
    if arm == CLAUDE_CODE_ARM:
        return ClaudeCodeAdapter(cap_usd=CAP_USD)
    designer = scripted_designer(sheet, overlay=overlay_of(case)) if offline else model
    return FactoryAdapter(designer, review=FACTORY_ARMS[arm])


def _run_folders(out: Path) -> list[Path]:
    """Every run folder under ``out``, sorted by arm, case and repeat."""
    found = [path.parent for path in out.glob("*/*/*/record.json")]
    return sorted(found, key=lambda p: (p.parent.parent.name, p.parent.name, _number(p.name)))


def _number(name: str) -> int:
    return int(name) if name.isdigit() else 0


async def _grade_all(folders: Sequence[Path], judge_for: JudgeFor | None) -> list[GradedRun]:
    return [await grade_run(read_record(folder), folder, judge_for) for folder in folders]


def _write_results(out: Path, graded: Sequence[GradedRun]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    data = [run.summary() for run in graded]
    (out / RESULTS_FILE).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def real_judge_for(sheet: facts.FactSheet) -> Agent[None, JudgeAnswer]:
    """The checklist judge on the pinned judge model, the same for every case."""
    return judge_agent()


def scripted_judge_for(sheet: facts.FactSheet) -> Agent[None, JudgeAnswer]:
    """Each checklist item is its own key phrase, as the scripted systems write it."""
    return scripted_judge({item.item: item.item for item in sheet.checklist})
