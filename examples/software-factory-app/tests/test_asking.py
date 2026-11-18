"""An agent's question, from the asking model, answered through the API."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from pydantic_ai_factory import SQLiteStore

from app.asking import QUESTION, asking_model
from app.factory import build_factory
from tests.support import answer, start, work


def test_a_person_answers_the_designers_question_and_the_run_then_waits_for_approval(
    client: TestClient, db: Path, store: SQLiteStore
) -> None:
    asking = build_factory(asking_model())
    run_id = start(client)
    work(db, asking, store)

    run = client.get(f"/api/runs/{run_id}").json()
    assert (run["state"], run["waiting"]["kind"], run["actions"]) == (
        "waiting",
        "tool-calls",
        ["reply"],
    )
    [question] = run["waiting"]["questions"]
    assert (question["tool"], question["question"]) == ("ask_person", QUESTION)

    missing = answer(client, run_id, choice="reply", by="Ada", replies={"other": "Yes."})
    assert missing.status_code == 422
    replied = answer(client, run_id, choice="reply", by="Ada", replies={question["id"]: "Yes."})
    assert replied.status_code == 202
    work(db, asking, store)

    run = client.get(f"/api/runs/{run_id}").json()
    assert (run["state"], run["waiting"]["kind"]) == ("waiting", "approval")
    assert run["output"].startswith("# To-do list")
