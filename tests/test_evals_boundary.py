"""Only the factory adapter may import the factory, so any system can be plugged in.

This is a test and not an import-linter contract because ``evals`` is not one of the
packages import-linter is configured to check.
"""

from __future__ import annotations

import ast
from pathlib import Path

EVALS = Path(__file__).parent.parent / "evals"
FACTORY_PACKAGES = frozenset({"pydantic_ai_factory", "pydantic_ai_factory_software"})
MAY_IMPORT_THE_FACTORY = frozenset(
    {
        "adapters/factory.py",
        # The old evaluation, until it is retired.
        "design.py",
    }
)


def factory_imports(source: str) -> list[str]:
    """Every module from the factory's packages that ``source`` imports."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            found.append(node.module)
    return [name for name in found if name.split(".")[0] in FACTORY_PACKAGES]


def test_no_module_in_evals_but_the_factory_adapter_imports_the_factory() -> None:
    modules = sorted(EVALS.rglob("*.py"))
    assert modules

    importing = {
        path.relative_to(EVALS).as_posix(): factory_imports(path.read_text(encoding="utf-8"))
        for path in modules
    }

    offending = {
        name: found
        for name, found in importing.items()
        if found and name not in MAY_IMPORT_THE_FACTORY
    }
    assert offending == {}
    assert importing["adapters/factory.py"]


def test_the_boundary_check_finds_an_import_of_the_factory() -> None:
    source = "import pydantic_ai_factory\nfrom pydantic_ai_factory_software import Design\n"

    assert factory_imports(source) == ["pydantic_ai_factory", "pydantic_ai_factory_software"]
    assert factory_imports("from pydantic_ai import Agent\n") == []
