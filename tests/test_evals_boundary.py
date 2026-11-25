"""Only the factory and station systems may import the rest of the core, so any system
can be plugged in.

This is a test and not an import-linter contract: import-linter checks packages, and the
rule is about modules inside one.
"""

from __future__ import annotations

import ast
from pathlib import Path

EVALS = Path(__file__).parent.parent / "src" / "pydantic_ai_factory" / "evals"
MAY_IMPORT_THE_FACTORY = frozenset({"_systems.py"})


def factory_imports(source: str) -> list[str]:
    """Every module of the core outside ``evals`` that ``source`` imports."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [
                alias.name for alias in node.names if alias.name.startswith("pydantic_ai_factory")
            ]
        elif isinstance(node, ast.ImportFrom):
            if node.level >= 2:
                found.append("." * node.level + (node.module or ""))
            elif node.level == 0 and node.module and node.module.startswith("pydantic_ai_factory"):
                found.append(node.module)
    return [name for name in found if not name.startswith("pydantic_ai_factory.evals")]


def test_no_module_of_the_framework_but_the_systems_imports_the_factory() -> None:
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
    assert importing["_systems.py"]


def test_the_boundary_check_finds_an_import_of_the_factory() -> None:
    source = "from ..factory import Factory\nimport pydantic_ai_factory.stations\n"

    assert factory_imports(source) == ["..factory", "pydantic_ai_factory.stations"]
    assert factory_imports("from ._contract import Brief\nfrom pydantic_ai import Agent\n") == []
