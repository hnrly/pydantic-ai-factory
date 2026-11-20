"""The scorecard and the run card, built from hand-made results. No model is called."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from evals import experiment
from evals.__main__ import main
from evals.checklist import ItemVerdict
from evals.contract import DesignPackage, RunRecord, Status
from evals.evaluators import GradedRun
from evals.facts import FactSheet
from evals.grading import Grade, Outcome, QuestionMeasures
from evals.report import (
    NOT_AVAILABLE,
    RUN_CARD_FILE,
    SCORECARD_FILE,
    SKILL_FOLDERS,
    ArmRow,
    RunCard,
    Scorecard,
    run_card,
    scorecard,
    scorecard_text,
)
from evals.version import PICKER_PROMPT

CHECKLIST = [
    {"id": "expiry", "item": "Says what happens to a task when it expires."},
    {"id": "undo", "item": "Lets a person undo a tick."},
    {"id": "order", "item": "Keeps tasks in the order they were added."},
]


def fact(fact_id: str, value: str | None, **fields: Any) -> dict[str, Any]:
    return {
        "id": fact_id,
        "value": value,
        "criticality": fields.get("criticality", "critical"),
        "obtainable": value is not None,
        "source": fields.get("source", "stakeholder"),
        "held_by": "product owner",
        "statement": f"The {fact_id} is {value}.",
        "direct_question": f"What is the {fact_id}?",
    }


COLOUR = fact("colour", "teal-green", criticality="low")
FACTS: dict[str, list[dict[str, Any]]] = {
    "critical-ambiguity": [fact("hold", "47 minutes"), fact("fee", "73.50 pounds"), COLOUR],
    "in-the-repo": [fact("db", "SQLite 3.46", source="repo"), COLOUR],
    "complete": [COLOUR],
    "nobody-knows": [fact("airline", None), COLOUR],
}
CASES = {
    "hold": ("dev", "critical-ambiguity"),
    "db": ("dev", "in-the-repo"),
    "blank": ("dev", "complete"),
    "airline": ("dev", "nobody-knows"),
    "later": ("test", "critical-ambiguity"),
}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A cases folder with a dev case of each class and one test case."""
    folder = tmp_path / "cases"
    for name, (split, _) in CASES.items():
        (folder / split / "facts").mkdir(parents=True, exist_ok=True)
        (folder / split / f"{name}.json").write_text(
            json.dumps({"name": name, "request": "Build it.", "rung": "empty"})
        )
        (folder / split / "facts" / f"{name}.json").write_text(sheet(name).model_dump_json())
    return folder


def sheet(name: str) -> FactSheet:
    return FactSheet.model_validate(
        {
            "case_name": name,
            "challenge_class": CASES[name][1],
            "canary": "canary-1234abcd5678",
            "fallback_reply": "I don't have anything to add on that.",
            "approval_reply": "I can't sign anything off here.",
            "checklist": CHECKLIST,
            "facts": FACTS[CASES[name][1]],
        }
    )


SHEETS = {name: sheet(name) for name in CASES}


def measures(**counts: int) -> QuestionMeasures:
    names = QuestionMeasures.model_fields
    return QuestionMeasures(**{name: counts.get(name, 0) for name in names})


def graded(
    arm: str,
    case: str,
    repeat: int = 1,
    *,
    outcome: Outcome = "verified_design",
    delivered: bool = True,
    status: Status = "delivered",
    cost: float | None = 0.10,
    coverage: float | None = None,
    present: Sequence[bool] = (),
    unexplained: tuple[str, ...] = (),
    reruns: int = 0,
    turns: int = 0,
    **counts: int,
) -> GradedRun:
    package = DesignPackage.of("A design.", status) if delivered else None
    record = RunRecord(
        arm=arm,
        case_name=case,
        repeat=repeat,
        package=package,
        events=(),
        cost_usd=cost,
        input_tokens=0,
        output_tokens=0,
        seconds=1.0,
        cap_usd=0.5,
        time_limit_seconds=60.0,
        end="finished",
        reruns=reruns,
    )
    grade = Grade(
        outcome=outcome,
        cells=(),
        gate_violations=(),
        measures=measures(questions_asked=turns, **counts),
        unexplained_values=unexplained,
        turns=turns,
        cost_usd=cost,
    )
    verdicts = tuple(
        ItemVerdict(item_id=item["id"], document_sha256="x", present=found, quote=None)
        for item, found in zip(CHECKLIST, present, strict=False)
    )
    return GradedRun(record=record, grade=grade, coverage=coverage, verdicts=verdicts)


def build(runs: Sequence[GradedRun], root: Path, **options: Any) -> Scorecard:
    return scorecard(runs, SHEETS, cases_root=root, judge_scripted=True, **options)


def row(card: Scorecard, arm: str, split: str = "dev") -> ArmRow:
    table = next(t for t in card.tables if t.split == split)
    return next(r for r in table.rows if r.arm == arm)


def every_class(arm: str, repeat: int = 1, **extra: Any) -> list[GradedRun]:
    """One run of ``arm`` on each dev case, built to give a known value for every metric."""
    return [
        graded(
            arm,
            "hold",
            repeat,
            turns=4,
            questions_releasing_critical=2,
            questions_releasing_nothing=1,
            critical_stakeholder_facts=2,
            critical_stakeholder_released=2,
            critical_stakeholder_used=1,
            critical_stakeholder_ignored=1,
            low_facts=1,
            low_facts_released=1,
            critical_facts=2,
            coverage=2 / 3,
            present=(True, True, False),
            outcome="design_failure",
            **extra,
        ),
        graded(
            arm,
            "db",
            repeat,
            turns=1,
            questions_releasing_critical=1,
            repo_facts=1,
            repo_facts_released=1,
            low_facts=1,
            critical_facts=1,
            coverage=1.0,
            present=(True, True, True),
            **extra,
        ),
        graded(arm, "blank", repeat, turns=2, low_facts=1, coverage=1 / 3, **extra),
        graded(
            arm,
            "airline",
            repeat,
            turns=1,
            questions_releasing_nothing=1,
            low_facts=1,
            critical_facts=1,
            status="blocked",
            outcome="correct_block",
            coverage=0.0,
            **extra,
        ),
    ]


def test_a_scorecard_built_from_hand_made_results_holds_every_metric(root: Path) -> None:
    runs = [*every_class("factory", 1), *every_class("factory", 2), *every_class("factory", 3)]

    found = row(build(runs, root), "factory")

    assert found.runs == 12 and found.cases == 4 and found.repeats == 3
    assert found.verified_design_rate.mean == pytest.approx(0.75)
    assert found.policy_failures == 0
    assert found.reliability == pytest.approx(0.75)
    assert found.cost_per_verified_design == pytest.approx(1.20 / 9)
    assert found.obtained_by_asking is not None and found.obtained_by_asking.mean == 1.0
    assert found.question_precision is not None
    assert found.question_precision.mean == pytest.approx((0.5 + 1.0 + 0.0 + 0.0) / 4)
    assert found.asked_and_used is not None and found.asked_and_used.mean == 0.5
    assert found.asked_and_ignored is not None and found.asked_and_ignored.mean == 0.5
    assert found.silent_assumptions is not None and found.silent_assumptions.mean == 0.0
    assert found.asked_what_the_code_says is not None
    assert found.asked_what_the_code_says.mean == 1.0
    assert found.needless_questions is not None
    assert found.needless_questions.mean == pytest.approx(0.25)
    assert found.released_nothing is not None
    assert found.released_nothing.mean == pytest.approx((0.25 + 0 + 0 + 1) / 4)
    assert found.questions_per_case == pytest.approx(2.0)
    assert found.minutes_per_case == pytest.approx(4.0)
    assert found.correct_blocks is not None and found.correct_blocks.mean == 1.0
    assert found.delivered_on_nobody_knows is not None
    assert found.delivered_on_nobody_knows.mean == 0.0
    assert found.questions_on_complete_cases == pytest.approx(2.0)
    assert found.checklist_coverage is not None
    assert found.checklist_coverage.mean == pytest.approx(0.5)
    assert found.item_coverage["hold/undo"] == 1.0
    assert found.item_coverage["hold/order"] == 0.0


def test_with_three_repeats_a_rate_has_an_interval_from_the_spread_between_cases(
    root: Path,
) -> None:
    runs = [*every_class("factory", 1), *every_class("factory", 2), *every_class("factory", 3)]

    rate = row(build(runs, root), "factory").verified_design_rate

    assert rate.low is not None and rate.high is not None
    assert rate.low < 0.75 < rate.high
    assert rate.no_interval is None


def test_an_arm_with_an_unknown_cost_has_no_cost_per_verified_design(root: Path) -> None:
    runs = [*every_class("factory"), graded("claude-code", "hold", cost=None)]

    card = build(runs, root)

    assert row(card, "claude-code").cost_per_verified_design is None
    assert not row(card, "claude-code").cost_known
    assert row(card, "factory").cost_per_verified_design is not None
    assert "Unknown cost, so no cost per verified design: claude-code" in scorecard_text(card)


def test_reliability_is_left_out_with_two_repeats(root: Path) -> None:
    runs = [*every_class("factory", 1), *every_class("factory", 2)]

    assert row(build(runs, root), "factory").reliability is None


def test_the_paired_difference_appears_only_when_both_arms_are_present(root: Path) -> None:
    factory = [*every_class("factory", 1), *every_class("factory", 2)]
    claude = [
        graded("claude-code", name, repeat, outcome="design_failure")
        for name in ("hold", "db", "blank", "airline")
        for repeat in (1, 2)
    ]
    simple = [graded("factory-simple", name, 1) for name in ("hold", "db", "blank", "airline")]

    alone = build(factory, root)
    together = build([*factory, *claude, *simple], root)

    assert alone.tables[0].comparisons == ()
    primary, ablation = together.tables[0].comparisons
    assert (primary.label, primary.first, primary.second) == (
        "Primary comparison",
        "factory",
        "claude-code",
    )
    assert primary.difference.mean == pytest.approx(0.75)
    assert primary.difference.low is not None
    assert ablation.label == "Ablation, exploratory"
    assert ablation.difference.no_interval == "one repeat"
    text = scorecard_text(together)
    assert "Primary comparison: factory minus claude-code" in text
    assert "Ablation, exploratory: factory minus factory-simple" in text


def test_an_unexplained_value_is_listed_with_its_run_id(root: Path) -> None:
    runs = [graded("factory", "hold", 2, unexplained=("fee",), outcome="design_failure")]

    text = scorecard_text(build(runs, root))

    assert "Unexplained value, to read by hand: factory/hold/2: fee" in text


def test_question_precision_on_a_worked_example(root: Path) -> None:
    # On hold: four questions, one critical; then two, both critical: 3 of 6 is 0.5. Its
    # run that asked nothing does not count. On db: one critical in four questions, 0.25.
    # blank asked nothing, so it is left out. Across the two cases: 0.375.
    runs = [
        graded("factory", "hold", 1, turns=4, questions_releasing_critical=1),
        graded("factory", "hold", 2, turns=2, questions_releasing_critical=2),
        graded("factory", "hold", 3, turns=0),
        graded("factory", "db", 1, turns=4, questions_releasing_critical=1),
        graded("factory", "blank", 1, turns=0),
    ]

    precision = row(build(runs, root), "factory").question_precision

    assert precision is not None
    assert precision.cases == 2
    assert precision.mean == pytest.approx(0.375)


def test_the_question_measures_follow_the_headline_under_their_own_heading(root: Path) -> None:
    text = scorecard_text(build(every_class("factory"), root))

    headline = text.index("Verified design rate")
    asked = text.index("How the session asked")
    quality = text.index("Design quality: beside the outcome, never deciding it")
    assert headline < asked < text.index("Precision") < quality
    assert quality < text.index("Checklist coverage")


def test_a_run_with_no_delivery_is_left_out_of_checklist_coverage_and_counted_beside_it(
    root: Path,
) -> None:
    runs = [
        graded("factory", "hold", 1, coverage=1.0),
        graded("factory", "hold", 2, delivered=False, outcome="design_failure"),
    ]

    found = row(build(runs, root), "factory")

    assert found.checklist_coverage is not None
    assert found.checklist_coverage.mean == 1.0
    assert found.coverage_left_out == 1


def test_checklist_coverage_is_withheld_when_the_judge_agrees_below_its_minimum(
    root: Path,
) -> None:
    runs = every_class("factory")

    below = scorecard(runs, SHEETS, cases_root=root, judge_agreement=0.85)
    unknown = scorecard(runs, SHEETS, cases_root=root, judge_agreement=None)
    met = scorecard(runs, SHEETS, cases_root=root, judge_agreement=0.90)

    assert not below.coverage_shown
    assert "below the minimum of 90%" in (below.coverage_note or "")
    assert "withheld" in scorecard_text(below)
    assert "Item coverage" not in scorecard_text(below)
    assert not unknown.coverage_shown
    assert met.coverage_shown


def test_dev_and_test_cases_are_reported_in_separate_tables(root: Path) -> None:
    runs = [*every_class("factory"), graded("factory", "later")]

    card = build(runs, root)

    assert [t.split for t in card.tables] == ["dev", "test"]
    assert row(card, "factory", "test").cases == 1


def test_a_rate_from_a_single_repeat_has_no_interval_and_says_so(root: Path) -> None:
    card = build(every_class("factory"), root)

    assert row(card, "factory").verified_design_rate.low is None
    assert "No interval: one repeat, for factory." in scorecard_text(card)


def test_the_scorecard_states_its_limits(root: Path) -> None:
    text = scorecard_text(build(every_class("factory"), root))

    assert "roughly 30 points either way" in text
    assert "the cases are public" in text.lower()


def nothing_answers(command: Sequence[str]) -> str | None:
    """A probe for a machine where no command can run."""
    return None


def card_for(tmp_path: Path, root: Path, **options: Any) -> RunCard:
    defaults: dict[str, Any] = {
        "judge": "scripted",
        "cases_root": root,
        "labels": tmp_path / "labels",
        "reference": tmp_path / "reference",
        "probe": nothing_answers,
        "now": datetime(2026, 11, 20, tzinfo=UTC),
    }
    arms = options.pop("arms", ["factory"])
    return run_card(["dev"], 3, arms, **{**defaults, **options})


def test_an_item_whose_source_is_missing_says_not_available(tmp_path: Path, root: Path) -> None:
    card = card_for(tmp_path, root)

    for item in (
        "Sandbox image digest",
        "Factory commit",
        "Reference app r0",
        "Checklist judge's agreement with the author's labels",
        "Simulator pass rates, leak test and answer test",
        "Simulator error rate on the labelled sample",
        "Claude Code sign-in plan",
    ):
        assert card.value(item) == NOT_AVAILABLE
    assert card.value("Date") == "2026-11-20T00:00:00+00:00"
    assert card.value("Case dev/hold") != NOT_AVAILABLE
    assert card.value("Designer's cap on rounds of questions, and on questions in a round") == (
        "3 rounds, 4 questions"
    )


def test_the_run_card_asks_claude_for_its_sign_in_only_when_the_claude_code_arm_ran(
    tmp_path: Path, root: Path
) -> None:
    asked: list[Sequence[str]] = []

    def probe(command: Sequence[str]) -> str | None:
        asked.append(command)
        return "answered"

    without = card_for(tmp_path, root, probe=probe)
    asked_without = [list(command) for command in asked]
    with_claude = card_for(tmp_path, root, probe=probe, arms=["claude-code"])

    assert ["claude", "auth", "status"] not in asked_without
    assert ["claude", "auth", "status"] in [list(command) for command in asked]
    assert without.value("Factory commit") == "answered"
    assert without.value("Claude Code sign-in plan") == NOT_AVAILABLE
    assert with_claude.value("Claude Code sign-in plan") == "answered"


def test_the_run_card_reads_the_author_s_labels(tmp_path: Path, root: Path) -> None:
    labels = tmp_path / "labels"
    labels.mkdir()
    judged = [{"judge": True, "author_label": True}] * 9 + [{"judge": True, "author_label": False}]
    (labels / "checklist-judge.json").write_text(json.dumps(judged))
    (labels / "simulator-fidelity.json").write_text(
        json.dumps(
            [{"all_asked_picked": True, "nothing_extra": True}] * 3
            + [{"all_asked_picked": True, "nothing_extra": False}]
        )
    )
    tests = {"leak": {"rate": 1.0}, "answer": {"rate": 0.97}}
    stale = {"prompt_sha256": "an old prompt", "tests": tests}
    (labels / "simulator-tests.json").write_text(json.dumps(stale))

    card = card_for(tmp_path, root)

    assert card.value("Checklist judge's agreement with the author's labels") == (
        "0.90 on 10 pairs"
    )
    assert card.value("Simulator error rate on the labelled sample") == "0.25 on 4 entries"
    assert card.value("Simulator pass rates, leak test and answer test") == NOT_AVAILABLE

    current = hashlib.sha256(PICKER_PROMPT.encode()).hexdigest()
    (labels / "simulator-tests.json").write_text(
        json.dumps({"prompt_sha256": current, "tests": tests})
    )
    assert card_for(tmp_path, root).value("Simulator pass rates, leak test and answer test") == (
        "answer 0.97, leak 1.00"
    )


def test_the_skill_library_hash_covers_both_skill_folders(tmp_path: Path, root: Path) -> None:
    method, software = tmp_path / "method", tmp_path / "software"
    for folder, name in ((method, "design-interview"), (software, "design-doc")):
        (folder / name).mkdir(parents=True)
        (folder / name / "SKILL.md").write_text(f"# {name}\n")
        (folder / name / "notes.txt").write_text("not a skill\n")

    def library_hash() -> str:
        card = card_for(tmp_path, root, skill_folders=(method, software))
        return card.value("Factory skill library hash")

    first = library_hash()
    (method / "design-interview" / "notes.txt").write_text("changed, but not a SKILL.md\n")
    assert library_hash() == first
    (software / "design-doc" / "SKILL.md").write_text("# design-doc, changed\n")
    second = library_hash()
    assert second != first
    (method / "design-interview" / "SKILL.md").write_text("# design-interview, changed\n")
    assert library_hash() != second
    assert all((folder.is_dir() and any(folder.rglob("SKILL.md"))) for folder in SKILL_FOLDERS)


def test_the_command_prints_and_writes_the_scorecard_and_the_run_card(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(experiment, "docker_available", lambda: False)

    code = main(["--offline", "--split", "smoke", "--out", str(tmp_path)])
    printed = capsys.readouterr().out
    regraded = main(["--offline", "--regrade", "--out", str(tmp_path)])

    assert code == 0 and regraded == 0
    assert "Scorecard: Software Factory Evals 0.1" in printed
    assert "How the session asked" in printed
    assert "Run card" in printed
    assert "Splits: smoke" in printed
    score = json.loads((tmp_path / SCORECARD_FILE).read_text())
    card = json.loads((tmp_path / RUN_CARD_FILE).read_text())
    assert score["tables"][0]["split"] == "smoke"
    assert len(score["tables"][0]["rows"]) == 4
    assert {item["item"] for item in card["items"]} >= {"Benchmark", "Factory skill library hash"}
