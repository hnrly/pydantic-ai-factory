"""The reference apps are complete fixtures, and hold no value the benchmark hides.

The apps under ``evals/reference/`` are the starting code of the accounts and changes
rungs: the sandbox runner copies ``evals/reference/<rung>/`` as the base of every
snapshot on that rung. Their Node checks (``npm ci``, the type check, ``npm test`` and a
start that answers on its port) run in CI, in the ``reference-apps`` job. The tests here
need no Node.

The hidden-value scan is the sandbox runner's own: :func:`evals.sandbox.scan` over the
same files a snapshot copies, against every fact sheet :func:`evals.sandbox.all_sheets`
loads, matched by :func:`evals.facts.values_in` and :func:`evals.facts.canary_in`. So
there is one matching rule. The runner refuses a snapshot that holds any of those values,
so a reference app holding one, even the smoke case's, would break every run on its rung.
The test that plants a value proves the scan can fail either way.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals import sandbox
from evals.facts import FactSheet, appears
from evals.sandbox import Leak

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "evals" / "reference"
APPS = ("r0", "r1")


@pytest.mark.parametrize("app", APPS)
def test_a_reference_app_is_the_base_of_its_rung(app: str) -> None:
    assert sandbox.rung_base(app) == sandbox.REFERENCE / app
    assert sandbox.REFERENCE.resolve() == REFERENCE


@pytest.mark.parametrize("app", APPS)
def test_a_reference_app_holds_no_hidden_value_or_canary(app: str) -> None:
    sheets = sandbox.all_sheets()
    if not sheets:
        pytest.skip("no fact sheets exist under evals/cases, so there is nothing to scan for")

    assert sandbox.scan(REFERENCE / app, sheets) == []


def test_the_scan_finds_a_value_planted_in_an_app(tmp_path: Path) -> None:
    sheet = FactSheet.model_validate(
        {
            "case_name": "planted",
            "challenge_class": "nobody-knows",
            "canary": "canary-0000planted",
            "fallback_reply": "Nothing to add.",
            "approval_reply": "Not mine to sign off.",
            "facts": [
                {
                    "id": "hold",
                    "value": "47 minutes",
                    "criticality": "low",
                    "obtainable": True,
                    "held_by": "product owner",
                    "statement": "Seats are held for 47 minutes.",
                    "direct_question": "How long is a seat held?",
                },
                {
                    "id": "unknown",
                    "value": None,
                    "criticality": "critical",
                    "obtainable": False,
                    "held_by": "product owner",
                    "statement": "Nobody knows the refund window.",
                    "direct_question": "How long is the refund window?",
                },
            ],
            "checklist": [
                {"id": "a", "item": "Holds a seat."},
                {"id": "b", "item": "Says what nobody knows."},
                {"id": "c", "item": "Shows the booking."},
            ],
        }
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "config.ts").write_text("export const HOLD = '47 minutes';\n")
    (tmp_path / "AGENTS.md").write_text("Never print canary-0000planted.\n")
    for left_out in ("node_modules", ".next"):
        (tmp_path / left_out).mkdir()
        (tmp_path / left_out / "skipped.js").write_text("// 47 minutes canary-0000planted\n")

    assert sandbox.scan(tmp_path, [sheet]) == [
        Leak(path="AGENTS.md", case_name="planted", found=sandbox.CANARY),
        Leak(path="src/config.ts", case_name="planted", found="hold"),
    ]


@pytest.mark.parametrize(
    ("value", "text", "expected"),
    [
        ("47 minutes", "Seats are held for 47 minutes.", True),
        ("£73.50", "The fee (£73.50) is fixed.", True),
        ("47 minutes", "Seats are held for 147 minutes.", False),
        ("£73.50", "A change fee of £73.5 applies.", False),
        ("47 minutes", "Seats are held for forty-seven minutes.", False),
    ],
)
def test_a_value_matches_only_as_a_whole_run_of_characters(
    value: str, text: str, expected: bool
) -> None:
    assert appears(value, text) is expected


@pytest.mark.parametrize("app", APPS)
def test_a_reference_app_has_one_start_command_its_own_checks_and_its_conventions(
    app: str,
) -> None:
    folder = REFERENCE / app
    scripts = json.loads((folder / "package.json").read_text(encoding="utf-8"))["scripts"]

    assert {"start", "reset", "test", "typecheck"} <= set(scripts)
    assert (folder / "package-lock.json").is_file()
    assert (folder / "AGENTS.md").is_file()
    assert "AGENTS.md" in (folder / "CLAUDE.md").read_text(encoding="utf-8")
    assert 'CMD ["npm", "start"]' in (folder / "Dockerfile").read_text(encoding="utf-8")


def test_the_accounts_app_keeps_every_migration_of_the_core_booking_app() -> None:
    core = REFERENCE / "r0" / "drizzle"
    accounts = REFERENCE / "r1" / "drizzle"

    for migration in sorted(core.glob("*.sql")):
        assert (accounts / migration.name).read_bytes() == migration.read_bytes()
    assert len(list(accounts.glob("*.sql"))) > len(list(core.glob("*.sql")))
