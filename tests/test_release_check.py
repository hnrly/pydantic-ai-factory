"""A release tag is published only when it matches the code and the changelog."""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "release_check", ROOT / "scripts/release_check.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


release = _load()

CHANGELOG = """# Changelog

## Unreleased

- Something not yet released.

## 0.2.0 (2026-12-01)

- Add a station.
- Fix a gate.

## 0.1.0

- First release.
"""


def _repo(tmp_path: Path, *, changelog: str = CHANGELOG, software_version: str = "0.2.0") -> Path:
    (tmp_path / "packages/software").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "pydantic-ai-factory"\nversion = "0.2.0"\n', encoding="utf-8"
    )
    (tmp_path / "packages/software/pyproject.toml").write_text(
        f'[project]\nversion = "{software_version}"\n'
        f'dependencies = ["pydantic-ai-factory=={software_version}"]\n',
        encoding="utf-8",
    )
    (tmp_path / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    return tmp_path


def test_the_notes_are_the_lines_under_the_versions_heading_and_no_further() -> None:
    assert release.release_notes(CHANGELOG, "0.2.0") == "- Add a station.\n- Fix a gate."
    assert release.release_notes(CHANGELOG, "0.1.0") == "- First release."
    assert release.release_notes(CHANGELOG, "0.3.0") is None


def test_a_tag_that_matches_both_packages_and_the_changelog_can_be_released(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    notes = tmp_path / "notes.md"

    assert release.problems("v0.2.0", root) == []
    assert release.main(["v0.2.0", "--root", str(root), "--notes", str(notes)]) == 0
    assert notes.read_text(encoding="utf-8") == "- Add a station.\n- Fix a gate.\n"


def test_a_tag_is_refused_with_every_reason_it_cannot_be_released(tmp_path: Path) -> None:
    root = _repo(
        tmp_path, changelog="# Changelog\n\n## Unreleased\n\n- x\n", software_version="0.1.0"
    )

    assert release.problems("v0.3.0", root) == [
        "the tag is v0.3.0, but pyproject.toml says version 0.2.0",
        "the software package is version 0.1.0, the core is 0.2.0",
        "the software package does not pin pydantic-ai-factory==0.2.0",
        "CHANGELOG.md has no '## 0.2.0' heading with lines under it",
    ]
    assert release.main(["v0.3.0", "--root", str(root)]) == 1


def test_this_repository_can_release_the_version_it_declares(tmp_path: Path) -> None:
    for name in ("pyproject.toml", "packages/software/pyproject.toml", "CHANGELOG.md"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / name, tmp_path / name)
    version = release._project(ROOT / "pyproject.toml")["version"]

    assert release.problems(f"v{version}", tmp_path) == []
