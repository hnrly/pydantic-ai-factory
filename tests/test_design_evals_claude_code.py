"""The Design suite's Claude Code arm: the framework's baseline with this suite's values.

The ``live`` test runs the smoke case through the real program in its container, and is
skipped unless asked for.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic_ai import models

from evals.design.__main__ import main
from evals.design.arms import CLAUDE_CODE_ARM, claude_code_system
from evals.design.cases import cases, load_sheet
from evals.design.suite import SUITE
from evals.design.version import (
    CAP_USD,
    CLAUDE_CODE_MODEL,
    DELIVER_DESCRIPTION,
    PICKER_MODEL,
    SANDBOX_IMAGE,
    TIME_LIMIT_SECONDS,
)
from evals.design.workspace import workspace_for
from pydantic_ai_factory.evals import docker_available, grade, picker_agent, run_case
from pydantic_ai_factory.evals._claude_code import claude_options

pytestmark = pytest.mark.anyio


def test_the_arm_runs_the_pinned_model_with_the_suites_cap_image_and_words(tmp_path: Path) -> None:
    system = claude_code_system()
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()

    argv = system.argv(snapshot, tmp_path, name="evals-claude-code-1")

    assert system.name == CLAUDE_CODE_ARM == "claude-code"
    assert CLAUDE_CODE_MODEL == "claude-opus-5-5"
    assert SANDBOX_IMAGE in argv
    assert argv[argv.index(SANDBOX_IMAGE) + 1 :] == [
        "claude",
        *claude_options(model=CLAUDE_CODE_MODEL, cap_usd=CAP_USD, config="/ports/mcp.json"),
    ]
    assert system._deliver_description == DELIVER_DESCRIPTION  # pyright: ignore[reportPrivateUsage]
    assert DELIVER_DESCRIPTION == (
        "Deliver your design document, in markdown, with a status of delivered or blocked. "
        "Call it once, when you are done."
    )


def test_the_claude_code_arm_reads_files_and_is_refused_offline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert SUITE.arm(CLAUDE_CODE_ARM).reads_files

    code = main(["--offline", "--arm", "claude-code", "--out", str(tmp_path)])

    assert code == 2
    assert "not with --offline" in capsys.readouterr().out
    assert not (tmp_path / "claude-code").exists()


def test_naming_the_claude_code_arm_without_docker_returns_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("pydantic_ai_factory.evals._experiment.docker_available", lambda: False)

    code = main(["--arm", "claude-code", "--out", str(tmp_path)])

    assert code == 2
    assert "Docker is needed to run the claude-code arm(s)" in capsys.readouterr().out


# The real program, by hand.


def _live_skip_reason() -> str | None:
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("PYDANTIC_AI_GATEWAY_API_KEY")):
        return (
            "neither ANTHROPIC_API_KEY nor PYDANTIC_AI_GATEWAY_API_KEY is set, so the "
            "stakeholder's picking model cannot answer"
        )
    if shutil.which("claude") is None:
        return "Claude Code is not on the path"
    status = subprocess.run(
        ["claude", "auth", "status"], capture_output=True, text=True, timeout=60, check=False
    )
    if status.returncode != 0:
        return "`claude auth status` does not report a signed-in account"
    if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return "CLAUDE_CODE_OAUTH_TOKEN is not set, so the container cannot sign in"
    if not docker_available():
        return "Docker is not running"
    image = subprocess.run(
        ["docker", "image", "inspect", SANDBOX_IMAGE], capture_output=True, check=False
    )
    if image.returncode != 0:
        return (
            f"the sandbox image {SANDBOX_IMAGE} is not built; "
            "run python -m evals.design --build-image"
        )
    return None


@pytest.mark.live
async def test_the_smoke_case_runs_through_the_real_claude_code_in_its_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reason = _live_skip_reason()
    if reason is not None:
        pytest.skip(reason)
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", True)
    [case] = cases("smoke")
    sheet = load_sheet("smoke", case.name)

    record = await run_case(
        claude_code_system(),
        case,
        sheet,
        arm=CLAUDE_CODE_ARM,
        out=tmp_path,
        picker=picker_agent(PICKER_MODEL),
        cap_usd=CAP_USD,
        time_limit_seconds=TIME_LIMIT_SECONDS,
        workspace_for=workspace_for,
    )

    assert record.end in ("finished", "budget"), record.detail
    assert record.cost_usd is not None
    kinds = [event.kind for event in record.events]
    assert "question_asked" not in kinds or "reply_sent" in kinds, "the stakeholder never answered"
    outcome = grade(record, sheet).outcome
    assert outcome != "infrastructure_error", record.detail
    print(f"claude-code on the smoke case: {outcome}, ${record.cost_usd}")
