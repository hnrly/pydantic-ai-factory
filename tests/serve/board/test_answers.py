"""An answer is built from the run it answers, by the person who typed their name."""

from __future__ import annotations

from urllib.parse import quote

from fastapi.testclient import TestClient
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from pydantic_ai_factory import (
    AskPerson,
    Factory,
    Reply,
    SQLiteStore,
    station,
)
from pydantic_ai_factory.serve import SQLiteDispatcher, board_app

from .support import (
    ADA,
    BEN,
    MISSION,
    answer_jobs,
    built,
    serve,
    wait,
    waiting_run,
)

# Answers.


def test_an_answer_to_a_moved_on_run_gets_409_with_the_run_as_it_is_now(
    client: TestClient, factory: Factory, store: SQLiteStore, jobs: SQLiteDispatcher
) -> None:
    run = waiting_run(client, factory, store, jobs)
    wait(jobs.answer(run.approve(by="Ada")))
    serve(factory, store, jobs)

    response = client.post(
        f"/api/runs/{run.run_id}/answer",
        json={"kind": "changes", "revision": run.revision, "note": "Shorter, please."},
        headers=BEN,
    )

    assert response.status_code == 409
    assert response.json()["run"]["state"] == "passed"
    assert len(answer_jobs(jobs)) == 1


def test_an_answer_to_a_run_that_waits_again_at_a_later_revision_gets_409_and_is_not_queued(
    client: TestClient, factory: Factory, store: SQLiteStore, jobs: SQLiteDispatcher
) -> None:
    run = waiting_run(client, factory, store, jobs)
    changes = client.post(
        f"/api/runs/{run.run_id}/answer",
        json={"kind": "changes", "revision": run.revision, "note": "Shorter, please."},
        headers=ADA,
    )
    assert changes.status_code == 202
    serve(factory, store, jobs)
    again = wait(store.get_run(run.run_id))
    assert again.outcome == "waiting" and again.revision > run.revision

    response = client.post(
        f"/api/runs/{run.run_id}/answer",
        json={"kind": "approve", "revision": run.revision},
        headers=BEN,
    )

    assert response.status_code == 409
    assert response.json()["run"]["revision"] == again.revision
    assert len(answer_jobs(jobs)) == 1


def test_the_answer_body_cannot_set_the_station_or_the_work(
    client: TestClient, factory: Factory, store: SQLiteStore, jobs: SQLiteDispatcher
) -> None:
    run = waiting_run(client, factory, store, jobs)

    response = client.post(
        f"/api/runs/{run.run_id}/answer",
        json={
            "kind": "approve",
            "revision": run.revision,
            "station": "review",
            "work": {"type": "Review", "digest": "0" * 64},
        },
        headers=ADA,
    )

    assert response.status_code == 422
    assert answer_jobs(jobs) == []
    queued = client.post(
        f"/api/runs/{run.run_id}/answer",
        json={"kind": "approve", "revision": run.revision},
        headers=ADA,
    )
    assert queued.status_code == 202
    (job,) = answer_jobs(jobs)
    assert job.answer is not None and job.answer.station == "draft"
    assert run.waiting is not None and job.answer.work == run.waiting.artifact


def test_an_answer_with_no_name_gets_400(
    client: TestClient, factory: Factory, store: SQLiteStore, jobs: SQLiteDispatcher
) -> None:
    run = waiting_run(client, factory, store, jobs)

    responses = [
        client.post(
            f"/api/runs/{run.run_id}/answer", json={"kind": "approve", "revision": run.revision}
        ),
        client.post(
            f"/api/runs/{run.run_id}/answer",
            json={"kind": "approve", "revision": run.revision},
            headers={"X-Factory-Name": "  "},
        ),
        client.post("/api/runs", json={"mission": MISSION}),
        client.post(f"/api/runs/{run.run_id}/restart"),
    ]

    assert [response.status_code for response in responses] == [400, 400, 400, 400]
    assert {response.json()["detail"] for response in responses} == {"Type your name first"}
    assert answer_jobs(jobs) == []


# Identity.


def test_a_name_outside_latin_1_survives_the_header(
    client: TestClient, jobs: SQLiteDispatcher
) -> None:
    response = client.post(
        "/api/runs", json={"mission": MISSION}, headers={"X-Factory-Name": quote("Łukasz")}
    )

    assert response.status_code == 202
    assert wait(jobs.job(response.json()["job_id"])).by == "Łukasz"


def test_a_reply_to_an_agents_question_is_built_from_the_runs_wait_and_queued(
    store: SQLiteStore, jobs: SQLiteDispatcher
) -> None:
    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        question = ToolCallPart("ask_person", {"question": "Which colour?"}, "q1")
        return ModelResponse(parts=[question])

    asker = Agent(FunctionModel(model), name="asker", capabilities=[AskPerson()])
    factory = Factory("colours", [station(asker)])
    run = wait(factory.run(MISSION, store=store, run_id="asking"))
    with TestClient(board_app(built(factory), store, jobs)) as client:
        shown = client.get("/api/runs/asking").json()
        wrong = client.post(
            "/api/runs/asking/answer",
            json={"kind": "reply", "revision": run.revision, "replies": {"q9": "Blue."}},
            headers=ADA,
        )
        right = client.post(
            "/api/runs/asking/answer",
            json={"kind": "reply", "revision": run.revision, "replies": {"q1": "Blue."}},
            headers=ADA,
        )

    assert shown["actions"] == ["reply"]
    assert [call["question"] for call in shown["wait"]["questions"]] == ["Which colour?"]
    assert wrong.status_code == 422
    assert right.status_code == 202, right.text
    (job,) = answer_jobs(jobs)
    assert isinstance(job.answer, Reply)
    assert job.answer.results.calls == {"q1": "Blue."}
    assert job.answer.results.answered_by == {"q1": "Ada"}
