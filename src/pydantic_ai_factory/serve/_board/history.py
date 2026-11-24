"""A run's track and history, as the run panel shows them.

The track is every station of the factory's shape, in order, with where the work is.
The history is what happened to the run, newest first, read from its steps, its
answers, its evidence and its jobs.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from ...artifacts import ArtifactRef
from ...contracts import FactoryError
from ...factory import Answer, Factory, Job, Reply, Run, Store
from ...methodologies import GoldfishReport
from ...stations import StationResult
from .views import Card, CardState, HistoryEvent, TrackState, TrackStop

_LIVE: tuple[CardState, ...] = ("waiting", "running", "queued", "starting")

_WAITING_TITLE = {
    "approval": "Waiting for approval",
    "escalation": "Escalated to a person",
    "tool-calls": "An agent asks a person",
}

_GATE_TITLE = {
    "passed": "Gate passed",
    "failed": "Gate failed",
    "escalate": "Gate passed the work to a person",
}

_SHOWN_FINDINGS = 3


def current_attempt(run: Run, station: str) -> int:
    """The attempt ``run`` is on at ``station``, or ended with there."""
    last = run.steps[-1] if run.steps else None
    if last is not None and last.station == station:
        changed = bool(run.answers) and run.answers[-1].kind == "changes"
        if run.outcome != "running" or (last.outcome == "waiting" and not changed):
            return last.attempt
    return max((step.attempt for step in run.steps if step.station == station), default=0) + 1


def _waited_after(factory: Factory, step: StationResult, reworked: Counter[str]) -> bool:
    """Whether a run stopped for a person after ``step``, so the next answer answers it.

    A step that waits is answered. So is a failed step whose rework ran out and escalated.
    ``reworked`` counts each station's rework so far, as the runner counts it, and is
    moved on by each failed step in turn.
    """
    if step.outcome == "waiting":
        return True
    if step.outcome != "failed":
        return False
    rule = factory.rework_for(step.station)
    if rule is None:
        return False
    if reworked[step.station] < rule.limit:
        reworked[step.station] += 1
        return False
    return rule.on_limit == "escalate" and step.output_ref is not None


def track(stations: Sequence[str], run: Run | None, state: CardState) -> list[TrackStop]:
    """Every station in ``stations``, in order, with where ``run``'s work is.

    A run that is not saved yet has every station ahead. A run that passed has every
    station done. Otherwise the stations before the run's station are done, its own is
    ``"here"`` while the run is live and ``"stopped"`` once it has ended or stalled,
    and the rest are ahead.
    """
    if run is None:
        return [TrackStop(station=name, state="ahead") for name in stations]
    tried = {step.station: step.attempt for step in run.steps}
    if run.outcome == "passed":
        return [TrackStop(station=name, state="done", attempt=tried.get(name)) for name in stations]
    at = run.station or run.stopped_at or (run.steps[-1].station if run.steps else None)
    if at not in stations:
        # A run of an earlier shape stopped at a station this shape does not have.
        return [
            TrackStop(
                station=name, state="done" if name in tried else "ahead", attempt=tried.get(name)
            )
            for name in stations
        ]
    here: TrackState = "here" if state in _LIVE else "stopped"
    stops: list[TrackStop] = []
    reached = False
    for name in stations:
        if name == at:
            reached = True
            stops.append(TrackStop(station=name, state=here, attempt=current_attempt(run, name)))
        elif reached:
            stops.append(TrackStop(station=name, state="ahead"))
        else:
            stops.append(TrackStop(station=name, state="done", attempt=tried.get(name)))
    return stops


def _review_event(report: GoldfishReport, at: datetime, step: StationResult) -> HistoryEvent:
    """One round of Elephant Goldfish review, from its report."""
    findings = report.findings
    count = len(findings)
    if count == 0:
        title = f"Goldfish round {report.round}: clean"
    elif report.clean:
        title = f"Goldfish round {report.round}: clean, {count} minor"
    else:
        title = f"Goldfish round {report.round}: {count} finding{'' if count == 1 else 's'}"
    shown = sorted(findings, key=lambda finding: not finding.blocking)[:_SHOWN_FINDINGS]
    lines = [f"{finding.severity}: {finding.issue}" for finding in shown]
    if count > len(shown):
        lines.append(f"And {count - len(shown)} more.")
    return HistoryEvent(
        kind="review",
        title=title,
        detail=" ".join(lines) or None,
        station=step.station,
        attempt=step.attempt,
        at=at,
    )


def _step_events(step: StationResult) -> list[HistoryEvent]:
    """What one attempt's record says about its gate, or why it failed."""
    where: dict[str, Any] = {"station": step.station, "attempt": step.attempt}
    if step.decision is not None:
        return [
            HistoryEvent(
                kind="gate",
                title=_GATE_TITLE[step.decision.judgement],
                detail=" ".join(step.decision.reasons),
                at=step.decision.decided_at,
                **where,
            )
        ]
    if step.not_evaluated is not None:
        return [
            HistoryEvent(
                kind="gate", title="Gate not run", detail=step.not_evaluated.reason, **where
            )
        ]
    if step.outcome == "failed":
        return [
            HistoryEvent(kind="failed", title=f"{step.station} failed", detail=step.reason, **where)
        ]
    if step.outcome == "did_not_run":
        return [
            HistoryEvent(
                kind="failed", title=f"{step.station} did not run", detail=step.reason, **where
            )
        ]
    return []


def _answer_event(answer: Answer) -> HistoryEvent:
    titles = {
        "approve": f"Approved by {answer.by}",
        "changes": f"{answer.by} asked for changes",
        "reject": f"Rejected by {answer.by}",
        "reply": f"{answer.by} replied",
    }
    return HistoryEvent(
        kind="answer",
        title=titles[answer.kind],
        detail=None if isinstance(answer, Reply) else answer.note,
        station=answer.station,
        by=answer.by,
        at=answer.at,
    )


def _stalled_event(job: Job) -> HistoryEvent:
    """Why the job driving a run stopped driving it."""
    if job.state == "failed":
        return HistoryEvent(
            kind="stalled",
            title="The worker's job failed",
            detail=job.error,
            at=job.updated_at,
        )
    if job.state == "done":
        return HistoryEvent(
            kind="stalled",
            title="The worker's job ended with the work part-way through",
            at=job.updated_at,
        )
    return HistoryEvent(
        kind="stalled",
        title="Claim lapsed. No word from the worker.",
        at=job.claimed_until,
    )


async def _evidence_events(
    store: Store, step: StationResult, seen: set[ArtifactRef]
) -> list[HistoryEvent]:
    """A step's evidence: each review round the core can read, then a count of the rest.

    ``seen`` holds the evidence already shown, since a resumed attempt names it again.
    """
    events: list[HistoryEvent] = []
    others = 0
    for ref in step.evidence:
        if ref in seen:
            continue
        seen.add(ref)
        if ref.type != GoldfishReport.__name__:
            others += 1
            continue
        try:
            report = await store.get(ref, GoldfishReport)
        except (FactoryError, ValidationError):
            others += 1
            continue
        events.append(_review_event(report.payload, report.created_at, step))
    if others:
        events.append(
            HistoryEvent(
                kind="evidence",
                title=f"{others} piece{'' if others == 1 else 's'} of evidence recorded",
                station=step.station,
                attempt=step.attempt,
            )
        )
    return events


def _attempt_event(station: str, attempt: int) -> HistoryEvent:
    return HistoryEvent(
        kind="attempt",
        title=f"{station} started, attempt {attempt}",
        station=station,
        attempt=attempt,
    )


async def _run_events(factory: Factory, store: Store, card: Card, run: Run) -> list[HistoryEvent]:
    """What a saved run's records say happened, oldest first."""
    events: list[HistoryEvent] = []
    tried: set[tuple[str, int]] = set()
    seen: set[ArtifactRef] = set()
    answers = list(run.answers)
    reworked: Counter[str] = Counter()
    for step in run.steps:
        if (step.station, step.attempt) not in tried:
            tried.add((step.station, step.attempt))
            events.append(_attempt_event(step.station, step.attempt))
        events += await _evidence_events(store, step, seen)
        events += _step_events(step)
        waited = _waited_after(factory, step, reworked)
        if waited and answers and answers[0].station == step.station:
            events.append(_answer_event(answers.pop(0)))
    events += [_answer_event(answer) for answer in answers]
    if run.outcome == "running" and run.station is not None:
        attempt = current_attempt(run, run.station)
        if (run.station, attempt) not in tried:
            events.append(_attempt_event(run.station, attempt))
    if run.waiting is not None:
        events.append(
            HistoryEvent(
                kind="waiting",
                title=_WAITING_TITLE[run.waiting.kind],
                station=run.waiting.station,
                attempt=current_attempt(run, run.waiting.station),
                at=run.updated_at,
            )
        )
    if run.abandoned is not None:
        events.append(
            HistoryEvent(
                kind="ended",
                title=f"Ended by {run.abandoned.by}",
                detail=run.abandoned.reason,
                by=run.abandoned.by,
                at=run.abandoned.at,
            )
        )
    elif run.outcome == "passed":
        events.append(HistoryEvent(kind="done", title="Done", at=run.updated_at))
    elif run.outcome in ("failed", "did_not_run"):
        stopped = run.stopped_at or (run.steps[-1].station if run.steps else None)
        verb = "Failed" if run.outcome == "failed" else "Did not run"
        events.append(
            HistoryEvent(
                kind="failed",
                title=f"{verb} at {stopped}" if stopped else verb,
                detail=card.reason,
                station=stopped,
                at=run.updated_at,
            )
        )
    return events


async def history(
    factory: Factory,
    store: Store,
    card: Card,
    run: Run | None,
    start: Job | None,
    driving: Job | None,
    restart: Job | None,
) -> list[HistoryEvent]:
    """What happened to a run or a queued mission, newest first."""
    by = start.by if start is not None else None
    events = [
        HistoryEvent(
            kind="mission",
            title=f"Mission added by {by}" if by else "Mission added",
            by=by,
            at=start.created_at if start is not None else (run.created_at if run else None),
        )
    ]
    if run is not None:
        events += await _run_events(factory, store, card, run)
    if card.state == "stalled" and driving is not None:
        events.append(_stalled_event(driving))
    if restart is not None:
        events.append(
            HistoryEvent(
                kind="restarted",
                title=f"Started again by {restart.by}",
                by=restart.by,
                at=restart.created_at,
                run_id=restart.run_id,
            )
        )
    events.reverse()
    return events
