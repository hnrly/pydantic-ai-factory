"""Helpers the tests share: calling the board's API as a person would."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

MISSION = "A to-do list web page where I can add tasks and tick them off."
ADA = {"X-Factory-Name": "Ada"}


def start(client: TestClient, mission: str = MISSION) -> str:
    """Start a mission through the API, as Ada, and return its run's id."""
    response = client.post("/api/runs", json={"mission": mission}, headers=ADA)
    assert response.status_code == 202, response.text
    return response.json()["run_id"]


def run(client: TestClient, run_id: str) -> dict[str, Any]:
    """One card in full, as the run page reads it."""
    response = client.get(f"/api/runs/{run_id}")
    assert response.status_code == 200, response.text
    return response.json()


def answer(client: TestClient, run_id: str, kind: str, **body: object) -> int:
    """Answer a run through the API, as Ada, at the revision it is at now. Return the status."""
    revision = run(client, run_id)["revision"]
    response = client.post(
        f"/api/runs/{run_id}/answer",
        json={"kind": kind, "revision": revision, **body},
        headers=ADA,
    )
    return response.status_code


def column(client: TestClient, run_id: str) -> str | None:
    """The column the board shows the run's card in: missions, a station's name, done or stopped."""
    for card in client.get("/api/board").json()["cards"]:
        if card["run_id"] == run_id:
            return card["station"] if card["column"] == "station" else card["column"]
    return None
