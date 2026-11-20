"""Run every arm on every case of a split, grade each run and write ``results.json``.

For each arm, one Pydantic Evals ``Dataset`` holds one ``Case`` for each case of the
split. The case's name and its input are both the case name, so every arm's runs line up
for paired analysis. The task builds the system, calls :func:`~evals.harness.run_case` and
returns the record; the only evaluator is :class:`~evals.evaluators.Graded`. Runs go one
at a time.

An experiment continues where it stopped: before each arm it counts the records already
in ``<out>/<arm>/<case>/`` and runs only what is missing to reach ``repeat``. It stops,
and returns 2, after three runs in a row end as ``infrastructure``.

This change registers only the scripted systems, which run offline. They read no file, so
they need neither Docker nor a repository snapshot. An arm that reads files, registered in
:data:`SANDBOX_ARMS` by a later change, runs in a Docker container: the experiment refuses
to start one when ``docker info`` fails.
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
from evals.adapters.scripted import ARM_PREFIX, KINDS, Kind, scripted_system
from evals.cases import PublicCase, cases
from evals.checklist import JudgeAnswer, judge_agent, scripted_judge
from evals.contract import RunRecord
from evals.evaluators import Graded, GradedRun, JudgeFor, grade_run
from evals.harness import read_record, run_case
from evals.sandbox import docker_available
from evals.simulator import Pick, haiku_picker, scripted_picker
from evals.version import CAP_USD, TIME_LIMIT_SECONDS

Judge = Literal["real", "scripted"]

SCRIPTED_ARMS: dict[str, Kind] = {ARM_PREFIX + kind: kind for kind in KINDS}
"""The arms of the scripted systems. They are test fixtures and run only offline."""

SANDBOX_ARMS: frozenset[str] = frozenset()
"""The arms that read files, each in its own Docker container. The factory and Claude Code
arms join it when their adapters are added."""

DOCKER_NEEDED = "Docker is needed to run the {arms} arm(s), and `docker info` failed."

STOP_AFTER = 3
"""An experiment stops after this many runs in a row end as an infrastructure error."""

RESULTS_FILE = "results.json"

OFFLINE_NOTE = (
    "Offline: scripted systems on a scripted picker. These numbers show the harness "
    "works. They say nothing about real models."
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
    ``model`` is for the factory arms, which a later change registers.
    """
    if repeat < 1:
        print(f"--repeat is at least 1, not {repeat}")
        return 2
    chosen = list(arms) or (list(SCRIPTED_ARMS) if offline else [])
    if not chosen:
        print("No arm runs on real models yet. Run the scripted systems with --offline.")
        return 2
    for arm in chosen:
        if arm not in SCRIPTED_ARMS:
            print(f"There is no arm called {arm!r}. The arms are {', '.join(SCRIPTED_ARMS)}.")
            return 2
        if not offline:
            print(f"The {arm} arm is a scripted test fixture and runs only with --offline.")
            return 2
    refusal = docker_refusal(chosen)
    if refusal is not None:
        print(refusal)
        return 2
    usereal_judge_for = (judge or ("scripted" if offline else "real")) == "real"
    judge_for: JudgeFor = real_judge_for if usereal_judge_for else scripted_judge_for
    return anyio.run(_run, chosen, split, repeat, out, offline, judge_for)


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
    arms: list[str], split: str, repeat: int, out: Path, offline: bool, judge_for: JudgeFor
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
                _task(arm, split, by_name, out, offline, progress),
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
):
    async def task(case_name: str) -> RunRecord:
        if progress.streak >= STOP_AFTER:
            raise RunsStopped(f"{STOP_AFTER} runs in a row ended as an infrastructure error")
        sheet = facts.load(split, case_name)
        picker: Agent[None, Pick] = scripted_picker(sheet) if offline else haiku_picker()
        record = await run_case(
            scripted_system(SCRIPTED_ARMS[arm], sheet),
            by_name[case_name],
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
