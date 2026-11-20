"""Elephant Goldfish's skills: where they live, how a goldfish reads one, the author's catalogue.

The method's guidance is written as Agent Skills, one folder each with a ``SKILL.md``, in
``skills/`` beside this module. Two are for the author, ``design-interview`` and
``design-resolve``, offered through Pydantic AI Harness's ``Skills``. Two are goldfish,
``goldfish-comprehension`` and ``goldfish-critique``: a goldfish's skill body is its
instructions, read once when it is built.

The catalogues are read from the package's own files through :class:`PackageFolder`, not
Pydantic AI's ``LocalWorkspaceBackend``, which runs only on POSIX, so they load on Windows too.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Never, cast

import yaml
from pydantic import BaseModel, Field
from pydantic_ai.workspaces import (
    FileEntry,
    ReadOnlyWorkspace,
    Workspace,
    WorkspaceReadOnlyError,
    WorkspaceRef,
)
from pydantic_ai_harness.skills import DuplicateNames, Skills
from pydantic_ai_harness.skills._loader import parse_skill

METHOD_SKILLS = Path(__file__).parent / "skills"
"""The folder of the method's own skills."""

AUTHOR_SKILLS = ("design-interview", "design-resolve")
"""The method's skills for the author, offered by name and description, loaded on demand."""

AUTHOR_SENTENCE = (
    "Load design-interview before you ask a person, and design-resolve when review findings "
    "come back."
)
"""What the author is told about its skills, with or without review."""


class ExplainBack(BaseModel):
    """How a comprehension goldfish explains the work back, from the work alone."""

    problem: str = Field(description="The problem the work addresses.")
    who: str = Field(description="Who has that problem.")
    done_when: list[str] = Field(
        default_factory=lambda: [],
        description="What will be true when the work is done, one item each.",
    )
    approach: str = Field(description="How the work goes about it.")
    not_doing: list[str] = Field(
        default_factory=lambda: [],
        description="What the work says it will not do, one item each.",
    )

    def to_text(self) -> str:
        """The explain-back as the paraphrase the author and the critic read."""

        def listed(items: list[str]) -> str:
            return "".join(f"\n- {item}" for item in items) or " Nothing is said."

        return (
            f"Problem: {self.problem}\n"
            f"Who has it: {self.who}\n"
            f"Done when:{listed(self.done_when)}\n"
            f"Approach: {self.approach}\n"
            f"Not doing:{listed(self.not_doing)}"
        )


@dataclass(frozen=True)
class GoldfishSkill:
    """What a goldfish needs from one ``SKILL.md``: its name, job, standard and metadata."""

    name: str
    job: str
    standard_name: str | None
    standard_text: str | None
    kinds: tuple[str, ...]
    max_findings: int | None


_SECTION = re.compile(r"^## +(.+?)\s*$", re.MULTILINE)
_LEFT_OUT = {"sources"}
"""Sections of a goldfish's skill that are for people, not its instructions."""


def _sections(body: str) -> tuple[str, dict[str, str]]:
    """The body before its first ``##`` heading, and each ``##`` section by lower-case title."""
    marks = list(_SECTION.finditer(body))
    lead = body[: marks[0].start()] if marks else body
    sections: dict[str, str] = {}
    for n, mark in enumerate(marks):
        end = marks[n + 1].start() if n + 1 < len(marks) else len(body)
        sections[mark.group(1).strip().lower()] = body[mark.end() : end].strip()
    return lead.strip(), sections


def _metadata(text: str) -> dict[str, Any]:
    """The ``metadata`` map of a ``SKILL.md``'s frontmatter, read as plain strings."""
    lines = text.splitlines()
    closing = next((n for n, line in enumerate(lines[1:], start=1) if line == "---"), None)
    if not lines or lines[0] != "---" or closing is None:
        return {}
    parsed: object = yaml.load("\n".join(lines[1:closing]), Loader=yaml.BaseLoader)
    if not isinstance(parsed, dict):
        return {}
    metadata = cast("dict[str, Any]", parsed).get("metadata")
    return cast("dict[str, Any]", metadata) if isinstance(metadata, dict) else {}


def _words(value: object) -> tuple[str, ...]:
    """A list from frontmatter: a YAML list, or one string of words or comma-separated names."""
    if isinstance(value, list):
        return tuple(str(item).strip() for item in cast("list[object]", value) if str(item).strip())
    if isinstance(value, str):
        return tuple(word for word in re.split(r"[\s,]+", value) if word)
    return ()


def skill_file(path: str | Path) -> Path:
    """The ``SKILL.md`` of a skill, given its folder or the file itself."""
    path = Path(path)
    return path / "SKILL.md" if path.is_dir() else path


def read_goldfish_skill(path: str | Path) -> GoldfishSkill:
    """Read one goldfish's ``SKILL.md`` with the harness's ``parse_skill``.

    The body, less its ``## Standard`` and ``## Sources`` sections, is the job. The
    ``## Standard`` section is the standard's text, and ``metadata.standard`` its name.
    ``metadata.kinds`` names the kinds of finding, and ``metadata.max_findings`` the most
    it may report. Raises ``ValueError`` when the file cannot be read or parsed.
    """
    file = skill_file(path)
    try:
        text = file.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"cannot read {file}: {error.strerror or error}") from error
    skill = parse_skill(text, file.as_posix())
    metadata = _metadata(text)
    lead, sections = _sections(skill.body)
    standard = sections.pop("standard", None)
    kept = [lead] + [
        f"## {title.capitalize()}\n\n{section}"
        for title, section in sections.items()
        if title not in _LEFT_OUT
    ]
    count = metadata.get("max_findings")
    return GoldfishSkill(
        name=skill.name,
        job="\n\n".join(part for part in kept if part),
        standard_name=str(metadata["standard"]) if "standard" in metadata else None,
        standard_text=" ".join(standard.split()) if standard is not None else None,
        kinds=_words(metadata.get("kinds")),
        max_findings=int(count) if isinstance(count, str) and count.isdigit() else None,
    )


class PackageFolder:
    """A folder of files shipped in a package, as a workspace backend that only reads.

    It lists, reads and checks files with :mod:`pathlib`, so it works on every platform,
    unlike Pydantic AI's ``LocalWorkspaceBackend``, which runs only on POSIX. It runs no
    commands and refuses every change. Its working directory is ``/``, the folder itself:
    the workspace path ``/design-doc/SKILL.md`` is the file ``design-doc/SKILL.md`` in it.
    """

    def __init__(self, folder: Path) -> None:
        self._folder = folder

    @property
    def ref(self) -> WorkspaceRef:
        """The folder's identity: provider ``package``, and the folder's path."""
        return WorkspaceRef(provider="package", id=self._folder.as_posix())

    async def working_dir(self) -> str:
        return "/"

    def _file(self, path: str) -> Path:
        """The file at a workspace path. ``..`` cannot climb out of the folder."""
        if not posixpath.isabs(path):
            raise ValueError(f"path must be absolute, got {path!r}")
        return self._folder.joinpath(
            *(part for part in posixpath.normpath(path).split("/") if part)
        )

    async def read_bytes(self, path: str) -> bytes:
        file = self._file(path)
        if file.is_dir():
            raise IsADirectoryError(path)
        return file.read_bytes()

    async def stat(self, path: str) -> FileEntry:
        file = self._file(path)
        size = file.stat().st_size
        is_dir = file.is_dir()
        where = posixpath.normpath(path)
        return FileEntry(
            name=posixpath.basename(where), path=where, is_dir=is_dir, size=None if is_dir else size
        )

    async def list_dir(self, path: str) -> Sequence[FileEntry]:
        where = posixpath.normpath(path)
        return [
            FileEntry(
                name=child.name,
                path=posixpath.join(where, child.name),
                is_dir=child.is_dir(),
                size=None if child.is_dir() else child.stat().st_size,
            )
            for child in sorted(self._file(path).iterdir(), key=lambda child: child.name)
        ]

    async def exists(self, path: str) -> bool:
        return self._file(path).exists()

    async def write_bytes(self, path: str, data: bytes) -> Never:
        raise WorkspaceReadOnlyError(f"{path} is in a package, which is read-only")

    async def make_dir(self, path: str) -> Never:
        raise WorkspaceReadOnlyError(f"{path} is in a package, which is read-only")

    async def remove(self, path: str) -> Never:
        raise WorkspaceReadOnlyError(f"{path} is in a package, which is read-only")


def packaged_skills(
    folder: Path, *, include: Collection[str], duplicate_names: DuplicateNames = "error"
) -> Skills[Any]:
    """A catalogue of the named skills in ``folder``, a skill library shipped in a package.

    The folder is read through a read-only workspace of its own, a :class:`PackageFolder`,
    so it works on every platform, and whether the run's workspace is the person's files,
    a sandbox or none.
    """
    workspace = ReadOnlyWorkspace(Workspace(PackageFolder(folder)))
    return Skills(".", include=include, duplicate_names=duplicate_names, workspace=workspace)


def author_skills() -> Skills[Any]:
    """The author's catalogue: the method's two author skills, read from the method's own folder."""
    return packaged_skills(METHOD_SKILLS, include=AUTHOR_SKILLS)
