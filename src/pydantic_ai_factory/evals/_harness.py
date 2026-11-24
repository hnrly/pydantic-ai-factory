"""Run one case through one system, and keep everything that crossed a port.

Each run gets its own folder, ``<out>/<arm>/<case>/<n>/``, where ``n`` is the repeat: the
lowest whole number from 1 not yet used. The path without ``<out>`` is the run id. The
folder holds ``events.jsonl``, written as events happen, and ``record.json``, the whole
:class:`RunRecord`, written when the run ends.

How a run ends:

* the time limit stops it: ``timeout``;
* the system reports that it reached its own cap, or finishes with a cost over the cap:
  ``budget``;
* the case's workspace cannot be built, or holds a hidden value it may not hold:
  ``infrastructure``, before the system is called, with the reason in ``detail``. It is a
  fault in the case;
* the system reports an infrastructure failure, or the system or harness raises:
  ``infrastructure``, with the exception's name in ``detail``. The run is then moved into
  ``attempt-1/`` and run once more in the same folder. Whatever the second attempt gives
  is the run's record, with ``reruns`` set to 1. The rule is the same for every arm;
* otherwise: ``finished``, and grading decides the outcome.

A suite that gives systems files to read passes ``workspace_for``, which builds the
case's workspace in ``<run folder>/`` and checks it before the system is called. Without
it, the brief carries no workspace.

The harness never reads a claim a system makes about itself, beyond its cost and how it
says it stopped.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from pathlib import Path

import anyio
from pydantic import BaseModel, ConfigDict, JsonValue
from pydantic_ai import Agent

from ._contract import (
    Approved,
    Brief,
    Delivery,
    RecordEnd,
    Reply,
    RunRecord,
    RunReport,
    Status,
    SystemAdapter,
    check_questions,
)
from ._events import EventLog
from ._facts import FactSheet
from ._simulator import Pick, Stakeholder

RERUN_FOLDER = "attempt-1"
"""Where the first attempt of a rerun run is kept."""

_STATUSES: frozenset[str] = frozenset({"delivered", "blocked"})


class Case(BaseModel):
    """What a system is given for a case: its name and the request, unchanged.

    A suite may subclass it to say more about the case, such as what its workspace holds.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    request: str


class WorkspaceError(RuntimeError):
    """A case's workspace could not be built, or holds what it may not. A fault in the case."""


WorkspaceFor = Callable[[Case, FactSheet, Path], Path]
"""Builds a case's workspace inside a run's folder, checks it, and returns its path.

It raises :class:`WorkspaceError` when the workspace cannot be used.
"""


class RunPorts:
    """The stakeholder and delivery ports of one run. Every call is logged.

    It is also the run's :class:`ApprovalLog`, where the factory and station adapters note
    the approvals they give in place of a person.
    """

    def __init__(self, stakeholder: Stakeholder, log: EventLog) -> None:
        self._stakeholder = stakeholder
        self._log = log
        self.delivery: Delivery | None = None
        self.failure: str | None = None
        """Why the simulated stakeholder could not answer, if it once could not."""

    async def ask_stakeholder(self, questions: list[str]) -> list[Reply]:
        """Ask one to four questions; get one reply for each, in order.

        A list of the wrong length is the system's mistake and raises ``ValueError``. Any
        other failure is the harness's own, such as the picking model's provider failing:
        it is noted in :attr:`failure`, which makes the run an infrastructure error, and
        raised so the system stops waiting for an answer.
        """
        check_questions(questions)
        try:
            return await self._stakeholder.ask(questions)
        except Exception as error:
            if self.failure is None:
                self.failure = f"the stakeholder could not answer: {type(error).__name__}: {error}"
            raise

    async def deliver(self, text: str, status: Status, *, output: JsonValue = None) -> None:
        """Deliver the work. Only the first delivery counts."""
        if status not in _STATUSES:
            raise ValueError(f"a status is delivered or blocked, not {status!r}")
        delivery = Delivery.of(text, status, output=output)
        if self._log.deliver(delivery.sha256, status):
            self.delivery = delivery

    def adapter_approved(self, what: Approved, reasons: list[str]) -> None:
        """Log an approval the adapter gave in place of a person. It is not a port call."""
        self._log.append("adapter_approved", what=what, reasons=list(reasons))


async def run_case(
    adapter: SystemAdapter,
    case: Case,
    sheet: FactSheet,
    *,
    arm: str,
    out: Path,
    picker: Agent[None, Pick],
    cap_usd: float,
    time_limit_seconds: float,
    workspace_for: WorkspaceFor | None = None,
) -> RunRecord:
    """Run ``case`` through ``adapter`` once, write its files and return its record.

    With ``workspace_for``, the adapter is given the case's workspace, built in the run's
    folder.
    """
    if sheet.case_name != case.name:
        raise ValueError(f"the fact sheet of {sheet.case_name!r} is not for {case.name!r}")
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
            workspace_for=workspace_for,
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
    case: Case,
    sheet: FactSheet,
    *,
    arm: str,
    repeat: int,
    folder: Path,
    run_id: str,
    picker: Agent[None, Pick],
    cap_usd: float,
    time_limit_seconds: float,
    workspace_for: WorkspaceFor | None,
) -> RunRecord:
    log = EventLog(folder / "events.jsonl", run=run_id)
    ports: RunPorts | None = None
    report: RunReport | None = None
    end: RecordEnd = "finished"
    detail = ""
    started = time.monotonic()
    try:
        workspace = workspace_for(case, sheet, folder) if workspace_for is not None else None
        brief = Brief(case_name=case.name, text=case.request, workspace=workspace)
        ports = RunPorts(Stakeholder(sheet, picker, log), log)
        log.append("brief_sent", case_name=case.name)
        with anyio.move_on_after(time_limit_seconds) as scope:
            report = await adapter.run(brief, ports)
        if scope.cancelled_caught:
            end = "timeout"
    except WorkspaceError as error:
        end = "infrastructure"
        detail = f"{type(error).__name__}: {error}"
    except Exception as error:
        end = "infrastructure"
        detail = type(error).__name__
    seconds = time.monotonic() - started

    if ports is not None and ports.failure is not None:
        # The stakeholder's own failure explains the run better than what it led to.
        end = "infrastructure"
        detail = ports.failure
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
        delivery=ports.delivery if ports is not None else None,
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
