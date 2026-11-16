"""The Getting started factory asks for prompt caching and reviews without delegating."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models.function import AgentInfo
from pytest_examples import find_examples
from scripted import PAGE, call, sample_models

ROOT = Path(__file__).resolve().parents[1]
GETTING_STARTED = ROOT / "docs" / "getting-started.md"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="Pydantic AI Harness's LocalWorkspace runs only on POSIX"
)


def _sample() -> str:
    for example in find_examples(str(GETTING_STARTED)):
        if example.prefix.split()[:1] == ["python"]:
            return example.source
    raise LookupError(f"{GETTING_STARTED} has no Python sample")


def test_the_reviewer_cannot_hand_the_review_to_a_sub_agent_and_the_designer_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, Any] = {}

    def reviewer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen["reviewer tools"] = {tool.name for tool in info.function_tools}
        return call("read_file", path="index.html")

    script = {
        "designer": ["One page, index.html."],
        "builder": [call("write_file", path="index.html", content=PAGE), "Built."],
        "reviewer": [reviewer, "Nothing is wrong."],
    }
    monkeypatch.chdir(tmp_path)

    with sample_models(script):
        namespace: dict[str, Any] = {"__name__": "getting_started"}
        exec(compile(_sample(), str(GETTING_STARTED), "exec"), namespace)

    assert "read_file" in seen["reviewer tools"]
    assert "delegate_task" not in seen["reviewer tools"]
    assert namespace["designer"].model_settings == {"cache": True}
