"""Only the Design suite's arms may import the factory, so the rest of the suite measures
any system the same way.

This is a test and not an import-linter contract because ``evals`` is not one of the
packages import-linter is configured to check.
"""

from __future__ import annotations

import ast
from pathlib import Path

SUITE = Path(__file__).parent.parent / "evals"
MAY_IMPORT_THE_FACTORY = frozenset({"design/arms.py"})


def factory_imports(source: str) -> list[str]:
    """Every module of the factory's packages, but the evaluation framework, ``source``
    imports."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            found.append(node.module)
    return [
        name
        for name in found
        if name.split(".")[0] in {"pydantic_ai_factory", "pydantic_ai_factory_software"}
        and not name.startswith("pydantic_ai_factory.evals")
    ]


def test_no_module_of_the_suite_but_its_arms_imports_the_factory() -> None:
    modules = sorted(SUITE.rglob("*.py"))
    assert modules

    importing = {
        path.relative_to(SUITE).as_posix(): factory_imports(path.read_text(encoding="utf-8"))
        for path in modules
        if "reference" not in path.parts
    }

    offending = {
        name: found
        for name, found in importing.items()
        if found and name not in MAY_IMPORT_THE_FACTORY
    }
    assert offending == {}
    assert importing["design/arms.py"]


def test_the_boundary_check_finds_an_import_of_the_factory_but_not_of_the_framework() -> None:
    source = (
        "import pydantic_ai_factory\nfrom pydantic_ai_factory_software import Design\n"
        "from pydantic_ai_factory.evals import Suite\n"
    )

    assert factory_imports(source) == ["pydantic_ai_factory", "pydantic_ai_factory_software"]
