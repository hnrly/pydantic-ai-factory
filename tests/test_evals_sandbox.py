"""The sandbox runner: repository snapshots, the leak scan and the container options.

Everything that can be checked without Docker is checked here without it. The tests that
start a container are skipped, with the reason, when Docker is not running or the sandbox
image has not been built.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from evals import experiment, sandbox
from evals.__main__ import main
from evals.cases import CASES, PublicCase
from evals.contract import Brief, Ports, RunReport
from evals.facts import FactSheet, load
from evals.harness import run_case
from evals.sandbox import (
    CANARY,
    Leak,
    RungBaseMissing,
    SandboxError,
    all_sheets,
    allowed_for,
    container_args,
    scan,
    snapshot_for,
)
from evals.simulator import scripted_picker
from evals.version import SANDBOX_IMAGE

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
    "source": "repo",
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
        (IN_REPO, "in-the-repo", [HOLD], IN_REPO_CANARY),
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
    (base / "app" / "page.tsx").write_text("export default function Page() {}\n")
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
    smoke = PublicCase.model_validate_json((CASES / "smoke" / "todo.json").read_text())

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
    (reference / "r0" / "app" / "holds.ts").write_text("const HOLD = '47 minutes';\n")
    planted = snapshot_for(case, tmp_path / "planted", root=cases_root, reference=reference)

    assert scan(clean, sheets, allowed) == []
    assert scan(planted, sheets, allowed) == [
        Leak(path="app/holds.ts", case_name=IN_REPO, found="hold")
    ]
    assert scan(clean, sheets) == [Leak(path="config/holds.ts", case_name=IN_REPO, found="hold")]


def test_the_smoke_case_snapshot_holds_no_hidden_value_of_any_case(tmp_path: Path) -> None:
    smoke = PublicCase.model_validate_json((CASES / "smoke" / "todo.json").read_text())
    allowed = allowed_for(smoke, load("smoke", smoke.name))

    assert scan(snapshot_for(smoke, tmp_path), all_sheets(), allowed) == []


def test_the_container_mounts_only_the_snapshot_read_only_at_workspace_with_no_network(
    tmp_path: Path,
) -> None:
    args = container_args(tmp_path)

    assert args == [
        "--rm",
        "--mount",
        f"type=bind,source={tmp_path.resolve()},target=/workspace,readonly",
        "--workdir",
        "/workspace",
        "--network",
        "none",
    ]
    assert not {"--volume", "-v", "--privileged"} & set(args)


def test_container_args_leave_the_network_to_a_caller_that_sets_its_own(tmp_path: Path) -> None:
    assert "--network" not in container_args(tmp_path, network=None)


def test_container_args_refuse_a_snapshot_path_a_mount_option_cannot_hold(
    tmp_path: Path,
) -> None:
    odd = tmp_path / "a,b"
    odd.mkdir()

    with pytest.raises(SandboxError, match="--mount"):
        container_args(odd)


class ReadsFiles:
    """A system that reads its snapshot, then delivers what it found."""

    name = "reads-files"

    def __init__(self) -> None:
        self.seen: dict[str, str] | None = None

    async def run(self, brief: Brief, ports: Ports) -> RunReport:
        self.seen = files_in(brief.snapshot)
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
        reads_files=True,
        cases_root=cases_root,
        reference_root=reference,
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
    (reference / "r0" / "app" / "fees.ts").write_text("// waive up to £73.50\n")
    system = ReadsFiles()

    record = await run_reading(system, roots, tmp_path / "out", IN_REPO)

    assert system.seen is None
    assert record.end == "infrastructure"
    assert record.package is None
    assert "app/fees.ts" in record.detail
    assert f"{ELSEWHERE}/fee" in record.detail


@pytest.mark.anyio
async def test_a_system_that_reads_files_on_a_missing_rung_is_refused_before_any_run_folder(
    tmp_path: Path, roots: tuple[Path, Path]
) -> None:
    cases_root, _ = roots
    sheet = load("dev", IN_REPO, root=cases_root)

    with pytest.raises(RungBaseMissing):
        await run_case(
            ReadsFiles(),
            case_named(IN_REPO),
            sheet,
            arm="reads-files",
            out=tmp_path / "out",
            picker=scripted_picker(sheet),
            cap_usd=0.5,
            time_limit_seconds=60.0,
            reads_files=True,
            cases_root=cases_root,
            reference_root=tmp_path / "none",
        )
    assert not (tmp_path / "out").exists()


def test_an_arm_that_reads_files_is_refused_when_docker_is_not_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(experiment, "docker_available", lambda: False)

    refusal = experiment.docker_refusal(["factory"], sandbox_arms=frozenset({"factory"}))

    assert refusal is not None
    assert "Docker is needed" in refusal
    assert experiment.docker_refusal(["scripted-correct"]) is None


def test_build_image_without_docker_prints_that_docker_is_needed_and_returns_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sandbox, "docker_available", lambda executable="docker": False)

    assert main(["--build-image"]) == 2
    assert "Docker is needed" in capsys.readouterr().out


def _docker_skip_reason() -> str | None:
    if not sandbox.docker_available():
        return "Docker is not running here, so no container can be started"
    inspect = subprocess.run(
        ["docker", "image", "inspect", SANDBOX_IMAGE], capture_output=True, check=False
    )
    if inspect.returncode != 0:
        return f"the image {SANDBOX_IMAGE} is not built; run python -m evals --build-image"
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
