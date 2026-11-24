"""The framework's sandbox: snapshots, the leak scan and the container options.

Everything here runs without Docker. A suite's own tests start containers.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pydantic_ai_factory.evals import (
    Brief,
    Case,
    FactSheet,
    Ports,
    RunReport,
    SandboxError,
    allowed_for,
    build_image,
    build_snapshot,
    check_clean,
    container_args,
    run_case,
    scan,
    scripted_picker,
)
from pydantic_ai_factory.evals._sandbox import CANARY, Leak, SnapshotLeak

SHEET = FactSheet.model_validate(
    {
        "case_name": "seat-hold",
        "challenge_class": "in-the-environment",
        "canary": "canary-seat-0f9e8d7c",
        "fallback_reply": "I don't have anything to add on that.",
        "approval_reply": "I can't sign anything off here.",
        "scored": False,
        "facts": [
            {
                "id": "hold",
                "value": "47 minutes",
                "criticality": "critical",
                "obtainable": True,
                "source": "environment",
                "held_by": "engineering",
                "statement": "Seats are held for 47 minutes.",
                "direct_question": "How long are seats held?",
            }
        ],
    }
)


def files_in(folder: Path) -> dict[str, str]:
    return {
        path.relative_to(folder).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


def write(folder: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(text, encoding="utf-8")
    return folder


def test_a_snapshot_holds_the_base_and_the_overlay_with_the_overlay_file_winning(
    tmp_path: Path,
) -> None:
    base = write(tmp_path / "base", {"README.md": "# Base\n", "app/page.tsx": "page\n"})
    overlay = write(tmp_path / "overlay", {"README.md": "# Overlay\n", "config/holds.ts": "x\n"})

    snapshot = build_snapshot(tmp_path / "run", base=base, overlay=overlay)

    assert snapshot == tmp_path / "run" / "snapshot"
    assert files_in(snapshot) == {
        "README.md": "# Overlay\n",
        "app/page.tsx": "page\n",
        "config/holds.ts": "x\n",
    }


def test_a_snapshot_with_no_base_and_no_overlay_is_empty(tmp_path: Path) -> None:
    assert files_in(build_snapshot(tmp_path)) == {}


def test_the_folders_a_suite_names_are_left_out_of_a_snapshot(tmp_path: Path) -> None:
    base = write(tmp_path / "base", {"a.txt": "a\n", "cache/b.txt": "b\n", ".git/c": "c\n"})

    snapshot = build_snapshot(tmp_path / "run", base=base, left_out=frozenset({"cache", ".git"}))

    assert sorted(files_in(snapshot)) == ["a.txt"]


def test_a_file_git_ignores_in_a_base_never_reaches_the_snapshot(tmp_path: Path) -> None:
    base = write(tmp_path / "tracked", {"app.txt": "app\n", ".gitignore": "data/\n"})
    (base / "data").mkdir()
    (base / "data" / "app.db").write_bytes(b"a local database")
    git = ["git", "-c", "init.defaultBranch=main"]
    subprocess.run([*git, "init", "--quiet"], cwd=base, check=True, capture_output=True)
    subprocess.run([*git, "add", "--all"], cwd=base, check=True, capture_output=True)

    snapshot = build_snapshot(tmp_path / "run", base=base)

    assert sorted(files_in(snapshot)) == [".gitignore", "app.txt"]


def test_a_symbolic_link_in_a_base_is_refused(tmp_path: Path) -> None:
    base = write(tmp_path / "base", {"a.txt": "a\n"})
    (base / "elsewhere").symlink_to(tmp_path)

    with pytest.raises(SandboxError, match="symbolic link"):
        build_snapshot(tmp_path / "run", base=base)


def test_an_environment_fact_in_the_overlay_is_allowed_and_elsewhere_is_not(
    tmp_path: Path,
) -> None:
    overlay = write(tmp_path / "overlay", {"config/holds.ts": "HOLD = '47 minutes'\n"})
    base = write(tmp_path / "base", {"app/holds.ts": "HOLD = '47 minutes'\n"})
    allowed = allowed_for(SHEET, overlay)

    clean = build_snapshot(tmp_path / "clean", overlay=overlay)
    planted = build_snapshot(tmp_path / "planted", base=base, overlay=overlay)

    assert scan(clean, [SHEET], allowed) == []
    assert scan(planted, [SHEET], allowed) == [
        Leak(path="app/holds.ts", case_name="seat-hold", found="hold")
    ]
    assert scan(clean, [SHEET]) == [
        Leak(path="config/holds.ts", case_name="seat-hold", found="hold")
    ]


def test_a_canary_is_found_even_in_the_cases_own_overlay(tmp_path: Path) -> None:
    overlay = write(tmp_path / "overlay", {"README.md": "canary-seat-0f9e8d7c\n"})
    snapshot = build_snapshot(tmp_path / "run", overlay=overlay)

    with pytest.raises(SnapshotLeak) as raised:
        check_clean(snapshot, [SHEET], allowed_for(SHEET, overlay))

    assert raised.value.leaks == (Leak(path="README.md", case_name="seat-hold", found=CANARY),)


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


def test_building_an_image_without_docker_says_docker_is_needed(tmp_path: Path) -> None:
    with pytest.raises(SandboxError, match="Docker is needed"):
        build_image("evals-test:1", tmp_path, executable="no-such-docker")


class ReadsFiles:
    """A system that reads its workspace, then delivers."""

    name = "reads-files"

    def __init__(self) -> None:
        self.seen: dict[str, str] | None = None

    async def run(self, brief: Brief, ports: Ports) -> RunReport:
        assert brief.workspace is not None
        self.seen = files_in(brief.workspace)
        await ports.deliver("Done.", "delivered")
        return RunReport(cost_usd=0.0, input_tokens=0, output_tokens=0, end="finished")


@pytest.mark.anyio
@pytest.mark.parametrize("planted", [False, True])
async def test_a_system_is_given_a_clean_workspace_or_never_called(
    tmp_path: Path, planted: bool
) -> None:
    overlay = write(
        tmp_path / "overlay", {"notes.md": "canary-seat-0f9e8d7c\n" if planted else "ok\n"}
    )

    def workspace_for(case: Case, sheet: FactSheet, folder: Path) -> Path:
        snapshot = build_snapshot(folder, overlay=overlay)
        check_clean(snapshot, [sheet], allowed_for(sheet, overlay))
        return snapshot

    system = ReadsFiles()
    record = await run_case(
        system,
        Case(name="seat-hold", request="Let people hold a seat."),
        SHEET,
        arm="reads-files",
        out=tmp_path / "out",
        picker=scripted_picker(SHEET),
        cap_usd=0.5,
        time_limit_seconds=60.0,
        workspace_for=workspace_for,
    )

    if planted:
        assert system.seen is None
        assert record.end == "infrastructure"
        assert "notes.md" in record.detail
    else:
        assert system.seen == {"notes.md": "ok\n"}
        assert record.end == "finished"
