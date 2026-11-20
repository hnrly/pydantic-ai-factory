"""Check that a release tag matches the code, and print its release notes.

    uv run python scripts/release_check.py v0.1.0 [--notes FILE]

A tag can be released only when:

* it is ``v`` followed by the version in both ``pyproject.toml`` files;
* the software package pins the core, with its ``methodologies`` extra, at that same version;
* ``CHANGELOG.md`` has a ``## <version>`` heading with at least one line under it.

With ``--notes FILE`` the lines under that heading are written to FILE, for the GitHub
release. Exit codes: 0 the tag can be released, 1 it cannot, with each reason printed.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _project(pyproject: Path) -> dict[str, object]:
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]


def release_notes(changelog: str, version: str) -> str | None:
    """The lines under ``## <version>``, up to the next ``## `` heading, or None."""
    match = re.search(
        rf"^## {re.escape(version)}[ \t]*(?:\([^)\n]*\))?[ \t]*\n(.*?)(?=^## |\Z)",
        changelog,
        flags=re.MULTILINE | re.DOTALL,
    )
    if match is None:
        return None
    notes = match.group(1).strip()
    return notes or None


def problems(tag: str, root: Path = ROOT) -> list[str]:
    """Every reason ``tag`` cannot be released from ``root``. Empty means it can."""
    found: list[str] = []
    core = _project(root / "pyproject.toml")
    software = _project(root / "packages/software/pyproject.toml")
    version = str(core["version"])
    if tag != f"v{version}":
        found.append(f"the tag is {tag}, but pyproject.toml says version {version}")
    if software["version"] != version:
        found.append(
            f"the software package is version {software['version']}, the core is {version}"
        )
    pin = f"pydantic-ai-factory[methodologies]=={version}"
    if software["dependencies"] != [pin]:
        found.append(f"the software package does not pin {pin}")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    if release_notes(changelog, version) is None:
        found.append(f"CHANGELOG.md has no '## {version}' heading with lines under it")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check a release tag and print its notes.")
    parser.add_argument("tag", help="the tag being released, such as v0.1.0")
    parser.add_argument("--notes", type=Path, help="write the release notes to this file")
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root")
    args = parser.parse_args(argv)
    found = problems(args.tag, args.root)
    for problem in found:
        print(f"release: {problem}", file=sys.stderr)
    if found:
        return 1
    if args.notes is not None:
        version = args.tag.removeprefix("v")
        changelog = (args.root / "CHANGELOG.md").read_text(encoding="utf-8")
        args.notes.write_text(f"{release_notes(changelog, version)}\n", encoding="utf-8")
    print(f"release: {args.tag} can be released")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
