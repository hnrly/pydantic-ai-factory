"""The port contract hashes every delivery itself and limits the questions in one call."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from pydantic_ai_factory.evals import MAX_QUESTIONS_PER_CALL, Brief, Delivery, check_questions


def test_a_delivery_carries_the_hash_of_its_text_computed_by_the_port() -> None:
    delivery = Delivery.of("# Seats\n\nHeld for 47 minutes.", "delivered")
    expected = hashlib.sha256(b"# Seats\n\nHeld for 47 minutes.").hexdigest()
    assert delivery.sha256 == expected
    assert delivery.output is None


def test_a_delivery_may_carry_the_systems_typed_output_as_json() -> None:
    delivery = Delivery.of("Held for 47 minutes.", "delivered", output={"minutes": 47})
    assert delivery.output == {"minutes": 47}


def test_a_brief_carries_the_request_unchanged_and_no_budget(tmp_path: Path) -> None:
    brief = Brief(case_name="seats", text="  Let people pick a seat.\n", workspace=tmp_path)
    assert brief.text == "  Let people pick a seat.\n"
    assert set(type(brief).model_fields) == {"case_name", "text", "workspace"}


def test_a_brief_needs_no_workspace() -> None:
    assert Brief(case_name="seats", text="Let people pick a seat.").workspace is None


@pytest.mark.parametrize("count", [1, MAX_QUESTIONS_PER_CALL])
def test_a_stakeholder_call_of_one_to_four_questions_is_accepted(count: int) -> None:
    check_questions(["Why?"] * count)


@pytest.mark.parametrize("count", [0, MAX_QUESTIONS_PER_CALL + 1])
def test_a_stakeholder_call_with_no_questions_or_too_many_is_refused(count: int) -> None:
    with pytest.raises(ValueError, match="one to 4 questions"):
        check_questions(["Why?"] * count)
