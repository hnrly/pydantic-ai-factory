"""Both distributions import, agree on a version and stay typed."""

from __future__ import annotations

import tomllib
from importlib import resources
from pathlib import Path

import pydantic_ai_factory
import pydantic_ai_factory_software

ROOT = Path(__file__).resolve().parents[1]


def _version(pyproject: Path) -> str:
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]


def test_each_package_reports_the_version_it_is_built_with() -> None:
    assert pydantic_ai_factory.__version__ == _version(ROOT / "pyproject.toml")
    assert pydantic_ai_factory_software.__version__ == _version(
        ROOT / "packages/software/pyproject.toml"
    )


def test_the_software_package_pins_the_core_it_was_built_with() -> None:
    software = tomllib.loads(
        (ROOT / "packages/software/pyproject.toml").read_text(encoding="utf-8")
    )

    assert software["project"]["dependencies"] == [
        f"pydantic-ai-factory=={pydantic_ai_factory.__version__}"
    ]


def test_the_core_declares_no_dependency_on_a_domain_package() -> None:
    core = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    extras: dict[str, list[str]] = core.get("optional-dependencies", {})
    declared: list[str] = list(core["dependencies"])
    for names in extras.values():
        declared.extend(names)

    assert not [name for name in declared if name.startswith("pydantic-ai-factory")]


def test_both_packages_ship_type_information() -> None:
    for package in ("pydantic_ai_factory", "pydantic_ai_factory_software"):
        assert resources.files(package).joinpath("py.typed").is_file()
