"""The jobs table: the queue between the web process and the worker."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import jobs
from app.models import Answer


def test_jobs_are_claimed_oldest_first_and_each_only_once(db: Path) -> None:
    first = jobs.add_start(db, "A to-do list web page.", "Ada")
    second = jobs.add_answer(db, "run-1", Answer(choice="approve", by="Ada", revision=1))

    claimed = [jobs.claim_next(db), jobs.claim_next(db), jobs.claim_next(db)]

    assert [job.id if job else None for job in claimed] == [first.id, second.id, None]
    assert all(job is not None and job.state == "claimed" for job in claimed[:2])


def test_a_finished_job_is_done_or_failed_with_its_reason(db: Path) -> None:
    good = jobs.add_start(db, "A to-do list web page.", "Ada")
    bad = jobs.add_start(db, "A shopping list web page.", "Ada")

    assert jobs.finish(db, good.id).state == "done"
    failed = jobs.finish(db, bad.id, error="It went wrong.")

    assert (failed.state, failed.error) == ("failed", "It went wrong.")
    assert failed.finished_at is not None


def test_the_latest_job_of_each_kind_is_found_for_a_run(db: Path) -> None:
    start = jobs.add_start(db, "A to-do list web page.", "Ada")
    jobs.add_answer(db, start.run_id, Answer(choice="approve", by="Ada", revision=1))
    later = jobs.add_answer(
        db, start.run_id, Answer(choice="reject", by="Bo", note="No.", revision=1)
    )

    latest = jobs.latest_for_run(db, start.run_id)

    assert (latest["start"].id, latest["answer"].id) == (start.id, later.id)
    assert latest["answer"].body["note"] == "No."


def test_reading_a_job_that_does_not_exist_raises_job_not_found(db: Path) -> None:
    with pytest.raises(jobs.JobNotFound):
        jobs.get(db, "nothing")
