"""The Design suite's workspace: repository snapshots, the leak scan and the image.

Everything that can be checked without Docker is checked here without it. The tests that
start a container are skipped, with the reason, when Docker is not running or the sandbox
image has not been built.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from evals.design import workspace
from evals.design.__main__ import main
from evals.design.cases import CASES, PublicCase, all_sheets, load_sheet
from evals.design.version import SANDBOX_IMAGE
from evals.design.workspace import RungBaseMissing, snapshot_for, workspace_builder
from pydantic_ai_factory.evals import (
    Brief,
    FactSheet,
    Ports,
    RunReport,
    SandboxError,
    container_args,
    docker_available,
    run_case,
    scan,
    scripted_picker,
)
from pydantic_ai_factory.evals._sandbox import CANARY, Leak


def allowed_for(case: PublicCase, sheet: FactSheet, *, root: Path = CASES) -> Any:
    return workspace.case_allowed(case, sheet, root=root)


def load(split: str, name: str, *, root: Path = CASES) -> FactSheet:
    return load_sheet(split, name, root=root)


CHECKLIST = [
    {"id": "reuse", "item": "Reuses the existing bookings table."},
    {"id": "migrate", "item": "Adds a database migration."},
    {"id": "clash", "item": "Says what happens when two people pick the same seat."},
]
HOLD = {
    "id": "hold",
    "value": "47 minutes",
    "criticality": "critical",
    "obtainable": True,
    "source": "environment",
    "held_by": "engineering",
    "statement": "Seats are held for 47 minutes.",
    "direct_question": "How long are seats held?",
    "keywords": ["held"],
}
FEE = {
    "id": "fee",
    "value": "£73.50",
    "criticality": "critical",
    "obtainable": True,
    "held_by": "product owner",
    "statement": "Staff may waive fees up to £73.50.",
    "direct_question": "How much may staff waive?",
    "keywords": ["waive"],
}

IN_REPO = "seat-hold"
ELSEWHERE = "fee-waiver"
IN_REPO_CANARY = "canary-seat-0f9e8d7c"
ELSEWHERE_CANARY = "canary-fee-1a2b3c4d"


def write_cases(root: Path) -> Path:
    """Two dev cases on the core booking rung: one keeps its hold time in its overlay."""
    split = root / "dev"
    (split / "facts").mkdir(parents=True)
    sheets = [
        (IN_REPO, "in-the-environment", [HOLD], IN_REPO_CANARY),
        (ELSEWHERE, "critical-ambiguity", [FEE], ELSEWHERE_CANARY),
    ]
    for name, challenge_class, case_facts, canary in sheets:
        case = PublicCase(name=name, request="Let people hold a seat.", rung="r0")
        (split / f"{name}.json").write_text(case.model_dump_json(), encoding="utf-8")
        sheet = FactSheet.model_validate(
            {
                "case_name": name,
                "challenge_class": challenge_class,
                "canary": canary,
                "fallback_reply": "I don't have anything to add on that.",
                "approval_reply": "I can't sign anything off here.",
                "checklist": CHECKLIST,
                "facts": case_facts,
            }
        )
        (split / "facts" / f"{name}.json").write_text(sheet.model_dump_json(), encoding="utf-8")
    overlay = split / "repo" / IN_REPO
    (overlay / "config").mkdir(parents=True)
    (overlay / "config" / "holds.ts").write_text(
        "export const HOLD = '47 minutes';\n", encoding="utf-8"
    )
    (overlay / "README.md").write_text("# Booking, with seat holds\n", encoding="utf-8")
    return root


def write_reference(root: Path) -> Path:
    """A stand-in for the core booking reference app."""
    base = root / "r0"
    (base / "app").mkdir(parents=True)
    (base / "app" / "page.tsx").write_text("export default function Page() {}\n", encoding="utf-8")
    (base / "README.md").write_text("# Booking\n", encoding="utf-8")
    for left_out in ("node_modules/react", ".git", ".next/server", "dist", "build", "out"):
        (base / left_out).mkdir(parents=True)
        (base / left_out / "file.js").write_text("ignored\n", encoding="utf-8")
    return root


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path]:
    return write_cases(tmp_path / "cases"), write_reference(tmp_path / "reference")


def case_named(name: str) -> PublicCase:
    return PublicCase(name=name, request="Let people hold a seat.", rung="r0")


def files_in(folder: Path) -> dict[str, str]:
    return {
        path.relative_to(folder).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


def test_a_snapshot_of_the_smoke_case_is_empty(tmp_path: Path) -> None:
    smoke = PublicCase.model_validate_json(
        (CASES / "smoke" / "todo.json").read_text(encoding="utf-8")
    )

    snapshot = snapshot_for(smoke, tmp_path)

    assert snapshot == tmp_path / "snapshot"
    assert snapshot.is_dir()
    assert list(snapshot.iterdir()) == []


def test_a_snapshot_holds_the_base_and_the_overlay_with_the_overlay_file_winning(
    tmp_path: Path, roots: tuple[Path, Path]
) -> None:
    cases_root, reference = roots

    snapshot = snapshot_for(
        case_named(IN_REPO), tmp_path / "run", root=cases_root, reference=reference
    )

    assert files_in(snapshot) == {
        "README.md": "# Booking, with seat holds\n",
        "app/page.tsx": "export default function Page() {}\n",
        "config/holds.ts": "export const HOLD = '47 minutes';\n",
    }


def test_node_modules_build_output_and_git_are_left_out_of_a_snapshot(
    tmp_path: Path, roots: tuple[Path, Path]
) -> None:
    cases_root, reference = roots

    snapshot = snapshot_for(
        case_named(ELSEWHERE), tmp_path / "run", root=cases_root, reference=reference
    )

    assert sorted(files_in(snapshot)) == ["README.md", "app/page.tsx"]


def test_a_file_git_ignores_in_a_rung_base_never_reaches_the_snapshot(
    tmp_path: Path, roots: tuple[Path, Path]
) -> None:
    cases_root, _ = roots
    reference = tmp_path / "tracked"
    base = reference / "r0"
    (base / "app").mkdir(parents=True)
    (base / "app" / "page.tsx").write_text("export default function Page() {}\n", encoding="utf-8")
    (base / ".gitignore").write_text("data/\n", encoding="utf-8")
    (base / "data").mkdir()
    (base / "data" / "app.db").write_bytes(b"SQLite format 3\x00 a local database")
    git = ["git", "-c", "init.defaultBranch=main"]
    subprocess.run([*git, "init", "--quiet"], cwd=reference, check=True, capture_output=True)
    subprocess.run([*git, "add", "--all"], cwd=reference, check=True, capture_output=True)

    snapshot = snapshot_for(
        case_named(ELSEWHERE), tmp_path / "run", root=cases_root, reference=reference
    )

    assert sorted(files_in(snapshot)) == [".gitignore", "app/page.tsx"]


def test_a_rung_whose_reference_app_is_missing_raises_a_clear_error(
    tmp_path: Path, roots: tuple[Path, Path]
) -> None:
    cases_root, _ = roots
    accounts = PublicCase(name=IN_REPO, request="Add accounts.", rung="r1")

    with pytest.raises(RungBaseMissing, match=r"rung 'r1'.*reference app has not been added"):
        snapshot_for(accounts, tmp_path / "run", root=cases_root, reference=tmp_path / "none")


def test_a_symbolic_link_in_a_rung_base_is_refused(
    tmp_path: Path, roots: tuple[Path, Path]
) -> None:
    cases_root, reference = roots
    (reference / "r0" / "facts").symlink_to(cases_root / "dev" / "facts")

    with pytest.raises(SandboxError, match="symbolic link"):
        snapshot_for(case_named(ELSEWHERE), tmp_path / "run", root=cases_root, reference=reference)


def test_a_value_from_another_cases_sheet_planted_in_a_snapshot_is_found(
    tmp_path: Path, roots: tuple[Path, Path]
) -> None:
    cases_root, reference = roots
    case = case_named(IN_REPO)
    snapshot = snapshot_for(case, tmp_path / "run", root=cases_root, reference=reference)
    (snapshot / "app" / "fees.ts").write_text("// waive up to £73.50\n", encoding="utf-8")
    allowed = allowed_for(case, load("dev", IN_REPO, root=cases_root), root=cases_root)

    leaks = scan(snapshot, all_sheets(root=cases_root), allowed)

    assert leaks == [Leak(path="app/fees.ts", case_name=ELSEWHERE, found="fee")]


def test_a_canary_planted_in_a_snapshot_is_found_even_in_its_own_overlay(
    tmp_path: Path, roots: tuple[Path, Path]
) -> None:
    cases_root, reference = roots
    case = case_named(IN_REPO)
    snapshot = snapshot_for(case, tmp_path / "run", root=cases_root, reference=reference)
    with (snapshot / "README.md").open("a", encoding="utf-8") as readme:
        readme.write(f"{IN_REPO_CANARY}\n")
    allowed = allowed_for(case, load("dev", IN_REPO, root=cases_root), root=cases_root)

    leaks = scan(snapshot, all_sheets(root=cases_root), allowed)

    assert leaks == [Leak(path="README.md", case_name=IN_REPO, found=CANARY)]


def test_a_repo_facts_value_in_its_own_overlay_is_allowed_and_in_the_base_is_not(
    tmp_path: Path, roots: tuple[Path, Path]
) -> None:
    cases_root, reference = roots
    case = case_named(IN_REPO)
    sheets = all_sheets(root=cases_root)
    allowed = allowed_for(case, load("dev", IN_REPO, root=cases_root), root=cases_root)
    clean = snapshot_for(case, tmp_path / "clean", root=cases_root, reference=reference)
    (reference / "r0" / "app" / "holds.ts").write_text(
        "const HOLD = '47 minutes';\n", encoding="utf-8"
    )
    planted = snapshot_for(case, tmp_path / "planted", root=cases_root, reference=reference)

    assert scan(clean, sheets, allowed) == []
    assert scan(planted, sheets, allowed) == [
        Leak(path="app/holds.ts", case_name=IN_REPO, found="hold")
    ]
    assert scan(clean, sheets) == [Leak(path="config/holds.ts", case_name=IN_REPO, found="hold")]


def test_the_smoke_case_snapshot_holds_no_hidden_value_of_any_case(tmp_path: Path) -> None:
    smoke = PublicCase.model_validate_json(
        (CASES / "smoke" / "todo.json").read_text(encoding="utf-8")
    )
    allowed = allowed_for(smoke, load("smoke", smoke.name))

    assert scan(snapshot_for(smoke, tmp_path), all_sheets(), allowed) == []


class ReadsFiles:
    """A system that reads its snapshot, then delivers what it found."""

    name = "reads-files"

    def __init__(self) -> None:
        self.seen: dict[str, str] | None = None

    async def run(self, brief: Brief, ports: Ports) -> RunReport:
        assert brief.workspace is not None
        self.seen = files_in(brief.workspace)
        await ports.deliver("# Design\n", "delivered")
        return RunReport(cost_usd=0.0, input_tokens=0, output_tokens=0, end="finished")


async def run_reading(adapter: Any, roots: tuple[Path, Path], out: Path, name: str) -> Any:
    cases_root, reference = roots
    sheet = load("dev", name, root=cases_root)
    return await run_case(
        adapter,
        case_named(name),
        sheet,
        arm="reads-files",
        out=out,
        picker=scripted_picker(sheet),
        cap_usd=0.5,
        time_limit_seconds=60.0,
        workspace_for=workspace_builder(root=cases_root, reference=reference),
    )


@pytest.mark.anyio
async def test_a_system_that_reads_files_is_given_the_snapshot_in_its_run_folder(
    tmp_path: Path, roots: tuple[Path, Path]
) -> None:
    system = ReadsFiles()

    record = await run_reading(system, roots, tmp_path / "out", IN_REPO)

    assert record.end == "finished"
    assert system.seen is not None
    assert system.seen["config/holds.ts"] == "export const HOLD = '47 minutes';\n"
    assert (tmp_path / "out" / "reads-files" / IN_REPO / "1" / "snapshot").is_dir()


@pytest.mark.anyio
async def test_a_planted_value_ends_the_run_as_infrastructure_before_the_system_is_called(
    tmp_path: Path, roots: tuple[Path, Path]
) -> None:
    _, reference = roots
    (reference / "r0" / "app" / "fees.ts").write_text("// waive up to £73.50\n", encoding="utf-8")
    system = ReadsFiles()

    record = await run_reading(system, roots, tmp_path / "out", IN_REPO)

    assert system.seen is None
    assert record.end == "infrastructure"
    assert record.delivery is None
    assert "app/fees.ts" in record.detail
    assert f"{ELSEWHERE}/fee" in record.detail


def test_a_case_on_a_missing_rung_is_refused_before_any_run_folder(tmp_path: Path) -> None:
    with pytest.raises(RungBaseMissing):
        workspace.check_case(case_named(IN_REPO), reference=tmp_path / "none")


def test_build_image_without_docker_prints_that_docker_is_needed_and_returns_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "pydantic_ai_factory.evals._sandbox.docker_available", lambda executable="docker": False
    )

    assert main(["--build-image"]) == 2
    assert "Docker is needed" in capsys.readouterr().out


def _docker_skip_reason() -> str | None:
    if not docker_available():
        return "Docker is not running here, so no container can be started"
    inspect = subprocess.run(
        ["docker", "image", "inspect", SANDBOX_IMAGE], capture_output=True, check=False
    )
    if inspect.returncode != 0:
        return f"the image {SANDBOX_IMAGE} is not built; run python -m evals.design --build-image"
    return None


def test_in_a_container_the_snapshot_reads_but_cannot_be_written_and_the_facts_are_absent(
    tmp_path: Path,
) -> None:
    reason = _docker_skip_reason()
    if reason is not None:
        pytest.skip(reason)
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "hello.txt").write_text("hello from the snapshot\n", encoding="utf-8")
    facts_folder = (CASES / "smoke" / "facts").resolve()
    script = (
        "cat hello.txt; "
        "if touch /workspace/written 2>/dev/null; then echo WROTE; else echo REFUSED; fi; "
        f"if [ -e '{facts_folder}' ]; then echo FACTS; else echo NO-FACTS; fi"
    )

    result = subprocess.run(
        ["docker", "run", *container_args(snapshot), SANDBOX_IMAGE, "sh", "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["hello from the snapshot", "REFUSED", "NO-FACTS"]
    assert not (snapshot / "written").exists()
