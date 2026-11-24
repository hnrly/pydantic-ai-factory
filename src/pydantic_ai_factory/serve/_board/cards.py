"""The card rules: where a run, or a mission not yet started, sits on the board.

These are pure functions of what was read: a run, its jobs and the time. Each card
falls under the first rule that fits.

1. The run passed: Done.
2. A factory of another shape made the run: Stopped, as an earlier version.
3. The run waits for a person: at its station, waiting.
4. The run failed or did not run: Stopped.
5. A start job with no saved run ended or lapsed: Stopped, as stalled.
6. A start job has no saved run yet: Missions, queued or starting.
7. The run is running, but the job driving it ended or lapsed: Stopped, as stalled.
8. The run is running: at its station.

Rules 1 to 4, 7 and 8 card a saved run, in :func:`run_card`. Rules 5 and 6 card a start
job whose run is not saved, in :func:`start_card`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from ...factory import Factory, Job, Run
from .views import Action, Card, ReworkCount, WaitingSummary


def first_line(text: str) -> str:
    """The first line of ``text`` that is not blank, which a card shows as its mission."""
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def answer_actions(run: Run) -> list[Action]:
    """The answers a person can give to what ``run`` waits for."""
    waiting = run.waiting
    if waiting is None:
        return []
    if waiting.kind == "tool-calls":
        return ["reply"]
    if waiting.kind == "escalation" and waiting.artifact is None:
        return ["changes", "reject"]
    return ["approve", "changes", "reject"]


def rework_count(factory: Factory, run: Run, at: str) -> ReworkCount | None:
    """How many reworks ``run`` has used of the rule that sends work back to ``at``."""
    rules = [rule for rule in factory.rework if rule.target.name == at] or [
        rule for rule in factory.rework if rule.station.name == at
    ]
    for rule in rules:
        count = run.reworked.get(rule.station.name, 0)
        if count:
            return ReworkCount(count=count, limit=rule.limit)
    return None


def run_card(factory: Factory, run: Run, mission: str, jobs: list[Job], now: datetime) -> Card:
    """Card a saved run, by rules 1 to 4, 7 and 8.

    ``jobs`` are the run's jobs that could be driving it or answering it.
    """
    driving = max(jobs, key=lambda job: job.created_at, default=None)
    open_answer = any(
        job.kind == "answer" and job.state in ("queued", "claimed") and not job.stalled(now)
        for job in jobs
    )
    base: dict[str, Any] = {
        "run_id": run.run_id,
        "mission": mission,
        "revision": run.revision,
        "updated_at": run.updated_at,
    }
    # 1. The run passed.
    if run.outcome == "passed":
        return Card(**base, column="done", state="passed")
    # 2. A factory of another shape made the run.
    if run.factory_digest != factory.digest:
        live = run.outcome in ("running", "waiting")
        return Card(
            **base,
            column="stopped",
            state="earlier_version",
            actions=["start_again"] if live else [],
        )
    # 3. The run waits for a person.
    if run.waiting is not None:
        return Card(
            **base,
            column="station",
            station=run.station,
            state="waiting",
            queued=open_answer,
            needs_you=not open_answer,
            waiting=WaitingSummary(
                kind=run.waiting.kind, station=run.waiting.station, since=run.updated_at
            ),
            rework=rework_count(factory, run, run.waiting.station),
            actions=answer_actions(run),
        )
    # 4. The run failed or did not run.
    if run.outcome in ("failed", "did_not_run"):
        reason = (
            run.abandoned.reason
            if run.abandoned
            else (driving.error if driving and driving.error else None)
            or next((step.reason for step in reversed(run.steps) if step.reason), None)
        )
        return Card(
            **base,
            column="stopped",
            state=run.outcome,
            reason=reason,
            actions=["start_again"],
        )
    # 7. The run is running, but the job driving it ended or lapsed.
    if driving is not None and (driving.state in ("done", "failed") or driving.stalled(now)):
        return Card(
            **base,
            column="stopped",
            state="stalled",
            reason=driving.error,
            actions=["start_again"],
        )
    # 8. The run is running.
    return Card(
        **base,
        column="station",
        station=run.station,
        state="running",
        queued=open_answer,
        rework=None if run.station is None else rework_count(factory, run, run.station),
    )


def start_card(job: Job, now: datetime) -> Card:
    """Card a start job whose run is not saved, by rules 5 and 6."""
    mission = first_line(job.mission.objective) if job.mission else ""
    base: dict[str, Any] = {"run_id": job.run_id, "mission": mission}
    # 5. A start job with no saved run ended or lapsed.
    if job.state in ("done", "failed") or job.stalled(now):
        return Card(
            **base,
            column="stopped",
            state="stalled",
            updated_at=job.updated_at,
            reason=job.error,
            actions=["start_again"],
        )
    # 6. A start job has no saved run yet: the card is in Missions. It is "queued"
    # while the job waits for a worker, and "starting" once a worker has claimed it
    # but not yet saved the run. When the run is saved, rule 3, 7 or 8 cards it at
    # its station instead.
    return Card(
        **base,
        column="missions",
        state="starting" if job.state == "claimed" else "queued",
        queued=True,
        updated_at=job.updated_at,
    )


def link_restarts(cards: Iterable[Card], restarts: Mapping[str, Job]) -> None:
    """Point each card started again at its new run, and take away its "Start again"."""
    for card in cards:
        if card.run_id in restarts:
            card.restarted_as = restarts[card.run_id].run_id
            card.actions = [action for action in card.actions if action != "start_again"]
