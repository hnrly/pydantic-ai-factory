"""Every Python sample in the documentation runs.

A sample is a fenced ``python`` block in a Markdown page under ``docs/`` or in a README.
It is executed as written, and every ``#>`` comment is checked against what the
line above printed, so a page cannot show code or output that is not true. Samples name
a real model; while one runs, each agent gets the scripted replies listed for its page in
``tests/scripted.py``, so no sample calls a model. A block that is
only a sketch opts out with ``python test="skip"`` on its opening fence, and the page
should say why.

Only ``.md`` files are searched, so an ``.mdx`` page must not hold a Python sample.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pytest_examples import CodeExample, EvalExample, find_examples
from scripted import SCRIPTS, sample_models

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ("docs", "README.md", "packages/software/README.md")


def python_samples(*paths: str | Path) -> list[CodeExample]:
    """Find the Python samples under ``paths`` that are meant to run."""
    return [
        example
        for example in find_examples(*paths)
        if example.prefix.split()[:1] in (["python"], ["py"])
        and example.prefix_settings().get("test") != "skip"
    ]


DOC_SAMPLES = python_samples(*(str(ROOT / source) for source in SOURCES))


@pytest.mark.parametrize("example", DOC_SAMPLES, ids=str)
def test_a_documentation_sample_runs_and_prints_what_it_says(
    example: CodeExample, eval_example: EvalExample
) -> None:
    # Each `#>` comment in a sample is compared with what the line above really printed.
    # Run `uv run pytest --update-examples` to rewrite them after an intended change.
    # Printed lines are compared whole, however long, as a reader sees them.
    eval_example.set_config(line_length=200)
    page = Path(example.path).relative_to(ROOT).as_posix()
    with sample_models(SCRIPTS.get(page, {})):
        if eval_example.update_examples:
            eval_example.run_print_update(example)
        else:
            eval_example.run_print_check(example)


def test_the_harness_finds_runs_and_fails_samples(
    tmp_path: Path, eval_example: EvalExample
) -> None:
    page = tmp_path / "page.md"
    page.write_text(
        "# Page\n\n"
        "```python\nassert 1 + 1 == 2\n```\n\n"
        '```python title="named.py"\nassert True\n```\n\n'
        "```python\nraise RuntimeError('broken sample')\n```\n\n"
        "```pycon\n>>> not run\n```\n\n"
        '```python test="skip"\nthis is only a sketch\n```\n\n'
        "```bash\nexit 1\n```\n",
        encoding="utf-8",
    )

    working, titled, broken = python_samples(page)

    eval_example.run(working)
    eval_example.run(titled)
    with pytest.raises(RuntimeError, match="broken sample"):
        eval_example.run(broken)


def test_no_mdx_page_holds_a_python_sample_that_would_go_untested() -> None:
    offenders = [
        page.relative_to(ROOT).as_posix()
        for page in (ROOT / "docs").rglob("*.mdx")
        if "```py" in page.read_text(encoding="utf-8")
    ]

    assert offenders == []
