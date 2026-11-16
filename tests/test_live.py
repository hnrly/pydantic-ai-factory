"""The getting-started path, against a real model.

These tests make real, paid requests to ``anthropic:claude-opus-5-5``, so they are
skipped unless selected with ``-m live``. The maintainer runs them by hand before a
release; see "Against a real model" on the Contributing page.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai import models
from pytest_examples import find_examples

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "docs" / "getting-started.md"

pytestmark = pytest.mark.live


def _the_getting_started_sample() -> str:
    for example in find_examples(str(PAGE)):
        if example.prefix.split()[:1] == ["python"]:
            return example.source
    raise LookupError(f"{PAGE} has no Python sample")


def test_the_getting_started_factory_designs_builds_and_reviews_a_to_do_list_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY is not set, so the real model cannot be called")
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", True)
    # The builder writes the app into the folder it runs in.
    monkeypatch.chdir(tmp_path)
    namespace: dict[str, Any] = {"__name__": "getting_started"}

    exec(compile(_the_getting_started_sample(), str(PAGE), "exec"), namespace)

    run, done = namespace["run"], namespace["done"]
    assert run.waiting is not None and run.waiting.kind == "approval"
    assert done.outcome == "passed"
    assert done.output.review.strip()
    assert "+++ b/" in done.output.diff
    assert any(tmp_path.iterdir()), "the builder wrote no files"
