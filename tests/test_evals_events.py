"""The event log only grows, keeps its order, and logs each question of a call apart."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from evals.events import EventLog


def log(tmp_path: Path) -> EventLog:
    return EventLog(tmp_path / "events.jsonl", run="run-1")


def test_events_come_back_in_the_order_appended_with_rising_sequence_numbers(
    tmp_path: Path,
) -> None:
    events = log(tmp_path)
    events.append("brief_sent", case_name="seats")
    events.append("question_asked", call=1, question="How long is a hold?")
    events.append("delivered", document_sha256="abc", status="delivered")

    assert [e.kind for e in events.events] == ["brief_sent", "question_asked", "delivered"]
    assert [e.seq for e in events.events] == [1, 2, 3]
    assert {e.run for e in events.events} == {"run-1"}


def test_every_event_is_written_as_one_line_of_json(tmp_path: Path) -> None:
    events = log(tmp_path)
    events.append("brief_sent", case_name="seats")
    events.append("delivered", document_sha256="abc", status="blocked")

    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["kind"] for line in lines] == ["brief_sent", "delivered"]


def test_an_event_cannot_be_changed(tmp_path: Path) -> None:
    event = log(tmp_path).append("brief_sent", case_name="seats")
    with pytest.raises(ValidationError):
        event.kind = "delivered"  # pyright: ignore[reportAttributeAccessIssue]


def test_an_event_with_the_wrong_data_for_its_kind_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="carries"):
        log(tmp_path).append("question_asked", question="How long is a hold?")


def test_a_second_delivery_is_logged_as_delivered_again(tmp_path: Path) -> None:
    events = log(tmp_path)
    assert events.deliver("first", "delivered") is True
    assert events.deliver("second", "blocked") is False

    assert [(e.kind, e.data["document_sha256"]) for e in events.events] == [
        ("delivered", "first"),
        ("delivered_again", "second"),
    ]


def test_a_call_of_three_questions_logs_three_question_and_reply_pairs_with_one_call_number(
    tmp_path: Path,
) -> None:
    events = log(tmp_path)
    call = events.next_call()
    for question in ("How long is a hold?", "What is the fee?", "Which database?"):
        events.append("question_asked", call=call, question=question)
        events.append(
            "reply_sent",
            call=call,
            question=question,
            picked=[],
            released=[],
            reply="I don't have anything on that.",
            asked_for_approval=False,
        )

    kinds = [e.kind for e in events.events]
    assert kinds == ["question_asked", "reply_sent"] * 3
    assert {e.data["call"] for e in events.events} == {1}
    assert events.next_call() == 2
