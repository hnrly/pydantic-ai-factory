"""The port contract hashes every delivery itself and limits the questions in one call."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from evals.contract import DesignPackage, brief_for, check_questions
from evals.version import MAX_QUESTIONS_PER_CALL


def test_a_package_carries_the_hash_of_its_document_computed_by_the_port() -> None:
    package = DesignPackage.of("# Seats\n\nHeld for 47 minutes.", "delivered")
    expected = hashlib.sha256(b"# Seats\n\nHeld for 47 minutes.").hexdigest()
    assert package.document_sha256 == expected


def test_a_brief_carries_the_request_unchanged_and_no_budget(tmp_path: Path) -> None:
    brief = brief_for("seats", "  Let people pick a seat.\n", tmp_path)
    assert brief.text == "  Let people pick a seat.\n"
    assert set(type(brief).model_fields) == {"case_name", "text", "snapshot"}


@pytest.mark.parametrize("count", [1, MAX_QUESTIONS_PER_CALL])
def test_a_stakeholder_call_of_one_to_four_questions_is_accepted(count: int) -> None:
    check_questions(["Why?"] * count)


@pytest.mark.parametrize("count", [0, MAX_QUESTIONS_PER_CALL + 1])
def test_a_stakeholder_call_with_no_questions_or_too_many_is_refused(count: int) -> None:
    with pytest.raises(ValueError, match="one to 4 questions"):
        check_questions(["Why?"] * count)
