"""The API with the worker behind it, offline: starting, listing and answering runs."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic_ai_factory import Factory, SQLiteStore
from pydantic_ai_factory_software import SoftwareFactory, offline_model

from app import jobs
from app.models import Answer
from app.worker import ANSWERED_FIRST
from tests.support import MISSION, answer, start, work


def test_a_person_approves_a_design_and_the_run_passes_in_their_name(
    client: TestClient, db: Path, software: Factory, store: SQLiteStore
) -> None:
    response = client.post("/api/runs", json={"mission": MISSION, "by": "Ada"})
    assert response.status_code == 202
    run_id, job_id = response.json()["run_id"], response.json()["job_id"]
    assert client.get(f"/api/runs/{run_id}").json()["state"] == "queued"

    work(db, software, store)

    assert client.get(f"/api/jobs/{job_id}").json()["state"] == "done"
    run = client.get(f"/api/runs/{run_id}").json()
    assert (run["state"], run["waiting"]["kind"]) == ("waiting", "approval")
    assert run["mission"] == MISSION
    assert run["actions"] == ["approve", "changes", "reject"]
    assert run["output"].startswith("# To-do list")
    assert "Reviewed in 2 rounds by 3 goldfish" in run["output"]

    assert answer(client, run_id, choice="approve", by="Ada").status_code == 202
    work(db, software, store)

    run = client.get(f"/api/runs/{run_id}").json()
    assert (run["state"], run["outcome"]) == ("passed", "passed")
    assert [(d["decision"], d["by"]) for d in run["approvals"]] == [("approved", "Ada")]
    assert run["jobs"]["answer"]["state"] == "done"


def test_the_inbox_lists_only_waiting_runs_longest_wait_first_and_says_when_more_matched(
    client: TestClient, db: Path, software: Factory, store: SQLiteStore
) -> None:
    first = start(client, "A to-do list web page.")
    second = start(client, "A shopping list web page.")
    third = start(client, "A reading list web page.")
    work(db, software, store)
    answer(client, third, choice="approve", by="Ada")
    work(db, software, store)

    inbox = client.get("/api/runs", params={"outcome": "waiting", "order": "oldest"}).json()
    assert [run["run_id"] for run in inbox["runs"]] == [first, second]
    assert inbox["more"] is False

    page = client.get("/api/runs", params={"outcome": "waiting", "order": "oldest", "limit": 1})
    assert [run["run_id"] for run in page.json()["runs"]] == [first]
    assert page.json()["more"] is True

    everything = client.get("/api/runs").json()
    assert [run["run_id"] for run in everything["runs"]] == [third, second, first]


def test_asking_for_changes_sends_the_design_back_and_it_waits_again_with_the_note(
    client: TestClient, db: Path, software: Factory, store: SQLiteStore
) -> None:
    run_id = start(client)
    work(db, software, store)
    before = client.get(f"/api/runs/{run_id}").json()
    assert "Blank tasks should not be added." not in before["output"]

    note = "Blank tasks should not be added."
    assert answer(client, run_id, choice="changes", by="Ada", note=note).status_code == 202
    work(db, software, store)

    after = client.get(f"/api/runs/{run_id}").json()
    assert (after["state"], after["waiting"]["kind"]) == ("waiting", "approval")
    assert after["revision"] > before["revision"]
    assert note in after["output"]
    assert [d["decision"] for d in after["approvals"]] == ["changes_requested"]


def test_an_answer_to_a_page_that_has_moved_on_gets_409_with_the_run_as_it_is_now(
    client: TestClient, db: Path, software: Factory, store: SQLiteStore
) -> None:
    run_id = start(client)
    work(db, software, store)
    seen = client.get(f"/api/runs/{run_id}").json()["revision"]

    stale = client.post(
        f"/api/runs/{run_id}/answer", json={"choice": "approve", "by": "Bo", "revision": seen - 1}
    )
    assert stale.status_code == 409
    assert stale.json()["run"]["revision"] == seen

    assert answer(client, run_id, choice="approve", by="Ada").status_code == 202
    second = client.post(
        f"/api/runs/{run_id}/answer",
        json={"choice": "reject", "by": "Bo", "note": "Not now.", "revision": seen},
    )
    assert second.status_code == 409
    assert "answered first" in second.json()["detail"]
    assert second.json()["run"]["state"] == "waiting"


def test_two_answers_queued_for_one_revision_leave_the_second_failed_and_shown_with_the_run(
    client: TestClient, db: Path, software: Factory, store: SQLiteStore
) -> None:
    run_id = start(client)
    work(db, software, store)
    revision = client.get(f"/api/runs/{run_id}").json()["revision"]
    # Both arrive together, so both pass the web process's check and are queued.
    jobs.add_answer(db, run_id, Answer(choice="approve", by="Ada", revision=revision))
    jobs.add_answer(
        db, run_id, Answer(choice="reject", by="Bo", note="Not now.", revision=revision)
    )

    first, second = work(db, software, store)

    assert first.state == "done"
    assert second.state == "failed"
    assert second.error is not None and second.error.startswith(ANSWERED_FIRST)
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["state"] == "passed"
    assert run["jobs"]["answer"]["id"] == second.id
    assert run["jobs"]["answer"]["error"] == second.error


def test_a_run_made_by_a_factory_of_another_shape_is_an_earlier_version_with_no_actions(
    client: TestClient, db: Path, store: SQLiteStore
) -> None:
    older = SoftwareFactory(offline_model(), repairs=1)
    run_id = asyncio.run(older.run(MISSION, store=store)).run_id

    run = client.get(f"/api/runs/{run_id}").json()
    assert (run["state"], run["actions"]) == ("earlier_version", [])
    inbox = client.get("/api/runs", params={"outcome": "waiting", "order": "oldest"}).json()
    assert inbox["runs"] == []
    assert answer(client, run_id, choice="approve", by="Ada").status_code == 409


def test_a_waiting_run_whose_job_is_still_claimed_is_shown_as_waiting(
    client: TestClient, db: Path, software: Factory, store: SQLiteStore
) -> None:
    run_id = start(client)
    job = jobs.claim_next(db)
    assert job is not None
    # The worker saved the waiting run, then died before it could finish the job.
    asyncio.run(software.run(MISSION, store=store, run_id=job.run_id))

    run = client.get(f"/api/runs/{run_id}").json()
    assert run["state"] == "waiting"
    assert run["jobs"]["start"]["state"] == "claimed"
    assert run["actions"] == ["approve", "changes", "reject"]


def test_approve_on_an_escalation_that_offers_no_work_gets_422(
    client: TestClient, db: Path, software: Factory, store: SQLiteStore
) -> None:
    run_id = start(client)
    work(db, software, store)
    run = asyncio.run(store.get_run(run_id))
    assert run.waiting is not None
    nothing = run.waiting.model_copy(
        update={"kind": "escalation", "artifact": None, "output": None, "reasons": ("stuck",)}
    )
    escalated = run.model_copy(update={"waiting": nothing, "revision": run.revision + 1})
    asyncio.run(store.put_run(escalated, replaces=run))

    shown = client.get(f"/api/runs/{run_id}").json()
    assert shown["waiting"]["offers_work"] is False
    assert shown["actions"] == ["changes", "reject"]
    refused = answer(client, run_id, choice="approve", by="Ada")
    assert refused.status_code == 422
    assert "no work to accept" in refused.json()["detail"]


def test_an_answer_that_does_not_fit_the_choice_gets_422(
    client: TestClient, db: Path, software: Factory, store: SQLiteStore
) -> None:
    run_id = start(client)
    work(db, software, store)

    assert answer(client, run_id, choice="changes", by="Ada").status_code == 422
    reply = answer(client, run_id, choice="reply", by="Ada", replies={"x": "Yes."})
    assert reply.status_code == 422
    assert "waits for approval" in reply.json()["detail"]


def test_a_mission_must_not_be_blank(client: TestClient) -> None:
    assert client.post("/api/runs", json={"mission": "  ", "by": "Ada"}).status_code == 422


def test_an_unknown_run_or_job_gets_404(client: TestClient) -> None:
    assert client.get("/api/runs/nothing").status_code == 404
    assert client.get("/api/jobs/nothing").status_code == 404
