"""The repository a Design case's systems may read, built as a snapshot for each run.

A case's snapshot starts from its rung's base, which is nothing for ``"empty"`` and the
reference app ``evals/design/reference/<rung>/`` otherwise, then lays the case's overlay,
``evals/design/cases/<split>/repo/<case>/``, on top. From the base it copies only the
files git tracks, so an ignored file, such as a reference app's local database in
``data/``, never enters a snapshot. It leaves out ``node_modules``, build output and
``.git``, so a snapshot holds source only.

The snapshot is then scanned against every fact sheet of every split. The only finds
allowed are the case's own repo facts, in files its overlay placed there on purpose.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_ai_factory.evals import (
    Allowed,
    Case,
    FactSheet,
    SandboxError,
    WorkspaceFor,
    allowed_for,
    build_image,
    build_snapshot,
    check_clean,
)

from .cases import CASES, PublicCase, all_sheets, split_of
from .version import CLAUDE_CODE_VERSION, SANDBOX_IMAGE

REFERENCE = Path(__file__).parent / "reference"
"""Where the rung bases live: ``<rung>/`` holds the reference app a rung starts from."""

DOCKERFILE_FOLDER = Path(__file__).parent / "sandbox"
"""The build context of the sandbox image: a folder that holds only its Dockerfile."""

LEFT_OUT: frozenset[str] = frozenset({".git", "node_modules", ".next", "dist", "build", "out"})
"""Folders a snapshot never holds: version control, installed packages and build output."""


class RungBaseMissing(SandboxError, FileNotFoundError):
    """A case starts from a rung whose reference app is not in this repository yet."""


def rung_base(rung: str, *, reference: Path = REFERENCE) -> Path | None:
    """The folder a rung starts from, or ``None`` for the empty rung.

    Raises :class:`RungBaseMissing` when that rung's reference app is not here yet.
    """
    if rung == "empty":
        return None
    base = reference / rung
    if not base.is_dir():
        raise RungBaseMissing(
            f"the rung {rung!r} starts from {base}, which does not exist yet; "
            "its reference app has not been added"
        )
    return base


def overlay_of(case: Case, *, root: Path = CASES) -> Path | None:
    """The case's overlay folder, or ``None`` when it has none."""
    overlay = root / split_of(case.name, root=root) / "repo" / case.name
    return overlay if overlay.is_dir() else None


def snapshot_for(
    case: PublicCase, folder: Path, *, root: Path = CASES, reference: Path = REFERENCE
) -> Path:
    """Build the case's repository snapshot in ``<folder>/snapshot/`` and return its path.

    ``folder`` is the run's own folder, ``<out>/<arm>/<case>/<n>/``. The rung's base is
    copied first and the case's overlay over it, so an overlay file wins over the base
    file at the same path.
    """
    return build_snapshot(
        folder,
        base=rung_base(case.rung, reference=reference),
        overlay=overlay_of(case, root=root),
        left_out=LEFT_OUT,
    )


def case_allowed(case: Case, sheet: FactSheet, *, root: Path = CASES) -> Allowed:
    """What the scan lets the case's own snapshot hold: its repo facts, in its overlay."""
    if sheet.case_name != case.name:
        raise ValueError(f"the fact sheet of {sheet.case_name!r} is not for {case.name!r}")
    return allowed_for(sheet, overlay_of(case, root=root))


def workspace_builder(*, root: Path = CASES, reference: Path = REFERENCE) -> WorkspaceFor:
    """Builds each run's workspace: the case's snapshot, scanned clean against every sheet.

    ``root`` and ``reference`` are where the cases and the reference apps are; a test
    passes its own.
    """

    def build(case: Case, sheet: FactSheet, folder: Path) -> Path:
        if not isinstance(case, PublicCase):
            raise TypeError(f"{case!r} is not a Design case")
        snapshot = snapshot_for(case, folder, root=root, reference=reference)
        check_clean(snapshot, all_sheets(root=root), case_allowed(case, sheet, root=root))
        return snapshot

    return build


workspace_for = workspace_builder()
"""The suite's workspace for one run, from this repository's cases and reference apps."""


def check_case(case: Case, *, reference: Path = REFERENCE) -> None:
    """Refuse a case whose rung's reference app is missing, before its run folder is made."""
    if isinstance(case, PublicCase):
        rung_base(case.rung, reference=reference)


def build_sandbox_image(executable: str = "docker") -> str:
    """Build the sandbox image, tag it :data:`SANDBOX_IMAGE` and return its image id."""
    return build_image(
        SANDBOX_IMAGE,
        DOCKERFILE_FOLDER,
        build_args={"CLAUDE_CODE_VERSION": CLAUDE_CODE_VERSION},
        executable=executable,
    )
