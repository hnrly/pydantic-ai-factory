"""Every case keeps its hidden half hidden, and the splits hold the cases they should.

A case is a request and a repository, which a system sees, and a fact sheet, which only
the grader side loads. These tests read every case in every split and check:

* its fact sheet loads, so it passes every refusal in :mod:`evals.facts`;
* its request holds no value from its fact sheet and no canary;
* its assembled snapshot passes the sandbox runner's scan;
* every repo fact's value is in the case's own overlay;
* every case but the smoke case has three to six checklist items;
* no checklist item's text is in the request or the snapshot;
* its name is unique across splits.

The dev split has one case of each class. The test split will have two of each, and no
in-the-repo case on the empty rung; the author writes those cases, so until they exist
the tests about the test split are skipped and say why. Two more tests plant a value and
a checklist item, to prove the hidden-value checks can fail.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import get_args

import pytest

from evals.design import cases, workspace
from evals.design.cases import SPLITS, PublicCase, load_sheet
from pydantic_ai_factory.evals import (
    ChallengeClass,
    FactSheet,
    canary_in,
    scan,
    values_in,
)
from pydantic_ai_factory.evals._facts import MAX_CHECKLIST_ITEMS, MIN_CHECKLIST_ITEMS
from pydantic_ai_factory.evals._sandbox import CANARY

ALL_CASES: list[tuple[str, PublicCase]] = [
    (split, case) for split in SPLITS for case in cases.cases(split)
]
SCORED_CASES = [(split, case) for split, case in ALL_CASES if load_sheet(split, case.name).scored]
CLASSES: tuple[ChallengeClass, ...] = get_args(ChallengeClass)
AUTHOR_WRITES_TEST = "the test cases are written by the author"


def _id(split_case: tuple[str, PublicCase]) -> str:
    split, case = split_case
    return f"{split}/{case.name}"


def hidden_in_request(case: PublicCase, sheet: FactSheet) -> set[str]:
    """The ids of the facts whose value the request states, plus ``canary`` if it holds it."""
    found = values_in(case.request, sheet)
    if canary_in(case.request, sheet):
        found.add(CANARY)
    return found


def items_in(text: str, sheet: FactSheet) -> set[str]:
    """The ids of the checklist items whose text is in ``text``, word for word."""
    return {item.id for item in sheet.checklist if item.item in text}


def snapshot_text(snapshot: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(snapshot.rglob("*"))
        if path.is_file()
    )


def test_there_are_cases_to_check() -> None:
    assert [case.name for split, case in ALL_CASES if split == "smoke"] == ["todo"]
    assert len([case for split, case in ALL_CASES if split == "dev"]) == 4


@pytest.mark.parametrize("split_case", ALL_CASES, ids=_id)
def test_a_case_has_a_fact_sheet_that_loads(split_case: tuple[str, PublicCase]) -> None:
    split, case = split_case

    assert load_sheet(split, case.name).case_name == case.name


@pytest.mark.parametrize("split_case", ALL_CASES, ids=_id)
def test_a_request_holds_no_hidden_value_and_no_canary(split_case: tuple[str, PublicCase]) -> None:
    split, case = split_case

    assert hidden_in_request(case, load_sheet(split, case.name)) == set()


@pytest.mark.parametrize("split_case", ALL_CASES, ids=_id)
def test_an_assembled_snapshot_passes_the_scan(
    split_case: tuple[str, PublicCase], tmp_path: Path
) -> None:
    split, case = split_case
    sheet = load_sheet(split, case.name)
    snapshot = workspace.snapshot_for(case, tmp_path)

    assert scan(snapshot, cases.all_sheets(), workspace.case_allowed(case, sheet)) == []


@pytest.mark.parametrize("split_case", ALL_CASES, ids=_id)
def test_every_repo_fact_is_in_the_cases_own_overlay(split_case: tuple[str, PublicCase]) -> None:
    split, case = split_case
    sheet = load_sheet(split, case.name)
    repo = {fact.id for fact in sheet.facts if fact.source == "environment"}
    overlay = workspace.overlay_of(case)
    found = values_in(snapshot_text(overlay), sheet) if overlay is not None else set[str]()

    assert repo <= found


@pytest.mark.parametrize("split_case", SCORED_CASES, ids=_id)
def test_a_scored_case_has_three_to_six_checklist_items(
    split_case: tuple[str, PublicCase],
) -> None:
    split, case = split_case
    items = load_sheet(split, case.name).checklist

    assert MIN_CHECKLIST_ITEMS <= len(items) <= MAX_CHECKLIST_ITEMS


@pytest.mark.parametrize("split_case", ALL_CASES, ids=_id)
def test_no_checklist_item_is_in_the_request_or_the_snapshot(
    split_case: tuple[str, PublicCase], tmp_path: Path
) -> None:
    split, case = split_case
    sheet = load_sheet(split, case.name)
    snapshot = workspace.snapshot_for(case, tmp_path)

    assert items_in(case.request, sheet) == set()
    assert items_in(snapshot_text(snapshot), sheet) == set()


def test_a_case_name_is_unique_across_splits() -> None:
    names = Counter(case.name for _, case in ALL_CASES)

    assert [name for name, count in names.items() if count > 1] == []


def test_the_dev_split_has_one_case_of_each_class() -> None:
    found = Counter(load_sheet("dev", case.name).challenge_class for case in cases.cases("dev"))

    assert found == Counter(CLASSES)


def test_the_test_split_has_two_cases_of_each_class() -> None:
    if not cases.cases("test"):
        pytest.skip(AUTHOR_WRITES_TEST)
    found = Counter(load_sheet("test", case.name).challenge_class for case in cases.cases("test"))

    assert found == Counter(CLASSES * 2)


def test_the_test_split_has_eight_cases() -> None:
    if not cases.cases("test"):
        pytest.skip(AUTHOR_WRITES_TEST)

    assert len(cases.cases("test")) == 8


def test_no_in_the_repo_case_of_the_test_split_starts_from_an_empty_repository() -> None:
    if not cases.cases("test"):
        pytest.skip(AUTHOR_WRITES_TEST)
    in_the_repo = [
        case
        for case in cases.cases("test")
        if load_sheet("test", case.name).challenge_class == "in-the-environment"
    ]

    assert [case.name for case in in_the_repo if case.rung == "empty"] == []


def test_no_in_the_repo_case_of_any_split_starts_from_an_empty_repository() -> None:
    empty = [
        _id((split, case))
        for split, case in ALL_CASES
        if case.rung == "empty"
        and load_sheet(split, case.name).challenge_class == "in-the-environment"
    ]

    assert empty == []


@pytest.mark.parametrize("split_case", SCORED_CASES, ids=_id)
def test_the_request_check_finds_a_value_planted_in_a_request(
    split_case: tuple[str, PublicCase],
) -> None:
    split, case = split_case
    sheet = load_sheet(split, case.name)
    fact = next(fact for fact in sheet.facts if fact.value is not None)
    planted = case.model_copy(update={"request": f"{case.request} {fact.statement}"})
    with_canary = case.model_copy(update={"request": f"{case.request} ({sheet.canary})"})

    assert fact.id in hidden_in_request(planted, sheet)
    assert hidden_in_request(with_canary, sheet) == {CANARY}


@pytest.mark.parametrize("split_case", SCORED_CASES, ids=_id)
def test_the_item_check_finds_a_checklist_item_planted_in_a_request_or_a_snapshot(
    split_case: tuple[str, PublicCase], tmp_path: Path
) -> None:
    split, case = split_case
    sheet = load_sheet(split, case.name)
    item = sheet.checklist[0]
    snapshot = workspace.snapshot_for(case, tmp_path)
    (snapshot / "NOTES.md").write_text(f"Remember: {item.item}\n", encoding="utf-8")

    assert items_in(f"{case.request} {item.item}", sheet) == {item.id}
    assert items_in(snapshot_text(snapshot), sheet) == {item.id}
