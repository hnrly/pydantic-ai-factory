"""Every Python sample in the documentation runs.

A sample is a fenced ``python`` block in ``docs/`` or a README. It is executed as written,
so a page cannot show code that does not work. A block that is only a sketch opts out with
``python test="skip"`` on its opening fence, and the page should say why.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pytest_examples import CodeExample, EvalExample, find_examples

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ("docs", "README.md", "packages/software/README.md")


def python_samples(*paths: str | Path) -> list[CodeExample]:
    """Find the Python samples under ``paths`` that are meant to run."""
    return [
        example
        for example in find_examples(*paths)
        if example.prefix_tags() <= {"python", "py"}
        and example.prefix.split()[:1] in (["python"], ["py"])
        and example.prefix_settings().get("test") != "skip"
    ]


DOC_SAMPLES = python_samples(*(str(ROOT / source) for source in SOURCES))


@pytest.mark.parametrize("example", DOC_SAMPLES, ids=str)
def test_a_documentation_sample_runs(example: CodeExample, eval_example: EvalExample) -> None:
    eval_example.run(example)


def test_the_harness_finds_runs_and_fails_samples(
    tmp_path: Path, eval_example: EvalExample
) -> None:
    page = tmp_path / "page.md"
    page.write_text(
        "# Page\n\n"
        "```python\nassert 1 + 1 == 2\n```\n\n"
        "```python\nraise RuntimeError('broken sample')\n```\n\n"
        '```python test="skip"\nthis is only a sketch\n```\n\n'
        "```bash\nexit 1\n```\n",
        encoding="utf-8",
    )

    working, broken = python_samples(page)

    eval_example.run(working)
    with pytest.raises(RuntimeError, match="broken sample"):
        eval_example.run(broken)
