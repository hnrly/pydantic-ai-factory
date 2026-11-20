"""The worker in ``__main__.py``: how it starts, and the offline asking flow it offers."""

from __future__ import annotations

import runpy
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from asking import QUESTION
from tests.conftest import Served
from tests.support import answer, run, start

MAIN = Path(__file__).resolve().parents[1] / "__main__.py"


def worker() -> Callable[[Sequence[str]], int]:
    """The worker's ``main``, loaded from ``__main__.py`` without running it."""
    return runpy.run_path(str(MAIN), run_name="worker")["main"]


def test_the_worker_with_no_model_and_no_offline_says_how_to_start(
    served: Served, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("FACTORY_MODEL", raising=False)

    assert worker()([]) == 2
    assert "set FACTORY_MODEL, or add --offline" in capsys.readouterr().err


def test_the_offline_worker_does_a_queued_mission_and_stops_when_idle(served: Served) -> None:
    run_id = start(served.client)

    assert worker()(["--offline", "--until-idle"]) == 0
    assert run(served.client, run_id)["state"] == "waiting"


def test_a_person_answers_the_designers_question_and_the_run_then_waits_for_approval(
    served: Served,
) -> None:
    client = served.client
    run_id = start(client)
    worker()(["--offline=asking", "--until-idle"])

    asked = run(client, run_id)
    assert (asked["state"], asked["wait"]["kind"], asked["actions"]) == (
        "waiting",
        "tool-calls",
        ["reply"],
    )
    [question] = asked["wait"]["questions"]
    assert (question["tool"], question["question"]) == ("ask_person", QUESTION)

    assert answer(client, run_id, "reply", replies={"other": "Yes."}) == 422
    assert answer(client, run_id, "reply", replies={question["id"]: "Yes."}) == 202
    worker()(["--offline=asking", "--until-idle"])

    designed = run(client, run_id)
    assert (designed["state"], designed["wait"]["kind"]) == ("waiting", "approval")
    assert designed["output"].startswith("# To-do list")
