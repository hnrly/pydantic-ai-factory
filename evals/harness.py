"""Run one case through one system, and keep everything that crossed a port.

Each run gets its own folder, ``<out>/<arm>/<case>/<n>/``, where ``n`` is the repeat: the
lowest whole number from 1 not yet used. The path without ``<out>`` is the run id. The
folder holds ``events.jsonl``, written as events happen, and ``record.json``, the whole
:class:`~evals.contract.RunRecord`, written when the run ends.

How a run ends:

* the time limit stops it: ``timeout``;
* the system reports that it reached its own cap, or finishes with a cost over the cap:
  ``budget``;
* the case's snapshot holds a hidden value it may not hold: ``infrastructure``, before
  the system is called, with each file and fact in ``detail``. It is a fault in the case;
* the system reports an infrastructure failure, or the system or harness raises:
  ``infrastructure``, with the exception's name in ``detail``. The run is then moved into
  ``attempt-1/`` and run once more in the same folder. Whatever the second attempt gives
  is the run's record, with ``reruns`` set to 1. The rule is the same for every arm;
* otherwise: ``finished``, and grading decides the outcome.

A system that reads files gets the case's repository snapshot, built by
:func:`~evals.sandbox.snapshot_for` in ``<run folder>/snapshot/`` and scanned by
:func:`~evals.sandbox.scan` before it is called. The scripted systems read no file, so
they skip both and are given an empty folder.

The harness never reads a claim a system makes about itself, beyond its cost and how it
says it stopped.
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
import time
from pathlib import Path

import anyio
from pydantic_ai import Agent

from evals.cases import CASES, PublicCase
from evals.contract import (
    Approved,
    DesignPackage,
    RecordEnd,
    Reply,
    RunRecord,
    RunReport,
    Status,
    SystemAdapter,
    brief_for,
)
from evals.events import EventLog
from evals.facts import FactSheet
from evals.sandbox import (
    REFERENCE,
    SnapshotLeak,
    all_sheets,
    allowed_for,
    check_clean,
    rung_base,
    snapshot_for,
)
from evals.simulator import Pick, Stakeholder

RERUN_FOLDER = "attempt-1"
"""Where the first attempt of a rerun run is kept."""

_STATUSES: frozenset[str] = frozenset({"delivered", "blocked"})


class RunPorts:
    """The stakeholder and delivery ports of one run. Every call is logged.

    It is also the run's :class:`~evals.contract.ApprovalLog`, where the factory adapter
    notes the approvals it gives in place of a person.
    """

    def __init__(self, stakeholder: Stakeholder, log: EventLog) -> None:
        self._stakeholder = stakeholder
        self._log = log
        self.package: DesignPackage | None = None

    async def ask_stakeholder(self, questions: list[str]) -> list[Reply]:
        """Ask one to four questions; get one reply for each, in order."""
        return await self._stakeholder.ask(questions)

    async def deliver(self, document: str, status: Status) -> None:
        """Deliver the design. Only the first delivery counts."""
        if status not in _STATUSES:
            raise ValueError(f"a status is delivered or blocked, not {status!r}")
        package = DesignPackage.of(document, status)
        if self._log.deliver(package.document_sha256, status):
            self.package = package

    def adapter_approved(self, what: Approved, reasons: list[str]) -> None:
        """Log an approval the adapter gave in place of a person. It is not a port call."""
        self._log.append("adapter_approved", what=what, reasons=list(reasons))


async def run_case(
    adapter: SystemAdapter,
    case: PublicCase,
    sheet: FactSheet,
    *,
    arm: str,
    out: Path,
    picker: Agent[None, Pick],
    cap_usd: float,
    time_limit_seconds: float,
    reads_files: bool = False,
    cases_root: Path = CASES,
    reference_root: Path = REFERENCE,
) -> RunRecord:
    """Run ``case`` through ``adapter`` once, write its files and return its record.

    With ``reads_files``, the adapter is given the case's repository snapshot, built from
    its rung's base under ``reference_root`` and its overlay under ``cases_root``. A rung
    whose base is missing raises :class:`~evals.sandbox.RungBaseMissing` before any run
    folder is made.
    """
    if sheet.case_name != case.name:
        raise ValueError(f"the fact sheet of {sheet.case_name!r} is not for {case.name!r}")
    if reads_files:
        rung_base(case.rung, reference=reference_root)
    folder, repeat = _new_run_folder(out / arm / case.name)
    run_id = folder.relative_to(out).as_posix()

    async def attempt() -> RunRecord:
        return await _attempt(
            adapter,
            case,
            sheet,
            arm=arm,
            repeat=repeat,
            folder=folder,
            run_id=run_id,
            picker=picker,
            cap_usd=cap_usd,
            time_limit_seconds=time_limit_seconds,
            reads_files=reads_files,
            cases_root=cases_root,
            reference_root=reference_root,
        )

    record = await attempt()
    if record.end == "infrastructure":
        first = folder / RERUN_FOLDER
        first.mkdir()
        for path in list(folder.iterdir()):
            if path != first:
                shutil.move(path, first / path.name)
        record = (await attempt()).model_copy(update={"reruns": 1})
        _write_record(folder, record)
    return record


def _new_run_folder(case_folder: Path) -> tuple[Path, int]:
    case_folder.mkdir(parents=True, exist_ok=True)
    repeat = 1
    while True:
        folder = case_folder / str(repeat)
        try:
            folder.mkdir()
        except FileExistsError:
            repeat += 1
            continue
        return folder, repeat


async def _attempt(
    adapter: SystemAdapter,
    case: PublicCase,
    sheet: FactSheet,
    *,
    arm: str,
    repeat: int,
    folder: Path,
    run_id: str,
    picker: Agent[None, Pick],
    cap_usd: float,
    time_limit_seconds: float,
    reads_files: bool,
    cases_root: Path,
    reference_root: Path,
) -> RunRecord:
    log = EventLog(folder / "events.jsonl", run=run_id)
    ports: RunPorts | None = None
    report: RunReport | None = None
    end: RecordEnd = "finished"
    detail = ""
    started = time.monotonic()
    try:
        with contextlib.ExitStack() as stack:
            if reads_files:
                snapshot = snapshot_for(case, folder, root=cases_root, reference=reference_root)
                allowed = allowed_for(case, sheet, root=cases_root)
                check_clean(snapshot, all_sheets(root=cases_root), allowed)
            else:
                snapshot = Path(
                    stack.enter_context(tempfile.TemporaryDirectory(prefix="evals-snapshot-"))
                )
            brief = brief_for(case.name, case.request, snapshot)
            ports = RunPorts(Stakeholder(sheet, picker, log), log)
            log.append("brief_sent", case_name=case.name)
            with anyio.move_on_after(time_limit_seconds) as scope:
                report = await adapter.run(brief, ports)
            if scope.cancelled_caught:
                end = "timeout"
    except SnapshotLeak as error:
        end = "infrastructure"
        detail = f"{type(error).__name__}: {error}"
    except Exception as error:
        end = "infrastructure"
        detail = type(error).__name__
    seconds = time.monotonic() - started

    if report is not None and end == "finished":
        if report.end == "infrastructure":
            end = "infrastructure"
            detail = report.detail
        elif report.end == "budget" or (report.cost_usd is not None and report.cost_usd > cap_usd):
            end = "budget"
            detail = report.detail

    record = RunRecord(
        arm=arm,
        case_name=case.name,
        repeat=repeat,
        package=ports.package if ports is not None else None,
        events=log.events,
        cost_usd=report.cost_usd if report is not None else None,
        input_tokens=report.input_tokens if report is not None else 0,
        output_tokens=report.output_tokens if report is not None else 0,
        seconds=seconds,
        cap_usd=cap_usd,
        time_limit_seconds=time_limit_seconds,
        end=end,
        detail=detail,
    )
    _write_record(folder, record)
    return record


def _write_record(folder: Path, record: RunRecord) -> None:
    (folder / "record.json").write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")


def read_record(folder: Path) -> RunRecord:
    """The record of the run in ``folder``."""
    return RunRecord.model_validate_json((folder / "record.json").read_text(encoding="utf-8"))
