"""The sandbox runner: what a system that reads files is given, and the check that it is clean.

A system under test that reads files sees one folder, the case's repository snapshot,
mounted read-only at ``/workspace`` in a Docker container of its own. Nothing else of this
machine is in that container: not the fact sheets, not the simulator, not the graders.

* :func:`snapshot_for` builds the snapshot. It copies the rung's base, which is nothing
  for ``"empty"`` and ``evals/reference/<rung>/`` otherwise, then lays the case's overlay,
  ``evals/cases/<split>/repo/<case>/``, on top. It leaves out ``node_modules``, build
  output and ``.git``, so a snapshot holds source only.
* :func:`scan` reads every file of a snapshot and reports each hidden value or canary, of
  every fact sheet in every split, that it finds. The only finds it allows are the case's
  own repo facts, in files its overlay placed there on purpose.
* :func:`container_args` gives the ``docker run`` options every arm's container uses.

This module is on the grader side. It imports nothing from the factory.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from evals import facts
from evals.cases import CASES, SPLITS, PublicCase, cases, split_of
from evals.facts import FactSheet, canary_in, values_in
from evals.version import CLAUDE_CODE_VERSION, SANDBOX_IMAGE

REFERENCE = Path(__file__).parent.parent / "reference"
"""Where the rung bases live: ``<rung>/`` holds the reference app a rung starts from."""

DOCKERFILE_FOLDER = Path(__file__).parent
"""The build context of the sandbox image: this folder, which holds only its Dockerfile."""

WORKSPACE = "/workspace"
"""Where a container sees the snapshot, and the folder its commands start in."""

SNAPSHOT_FOLDER = "snapshot"
"""The name of the snapshot folder inside a run's folder."""

LEFT_OUT: frozenset[str] = frozenset({".git", "node_modules", ".next", "dist", "build", "out"})
"""Folders a snapshot never holds: version control, installed packages and build output."""

CANARY = "canary"
"""What :func:`scan` reports in place of a fact id when it finds a canary."""


class SandboxError(RuntimeError):
    """A snapshot could not be built, or Docker could not do what was asked."""


class RungBaseMissing(SandboxError, FileNotFoundError):
    """A case starts from a rung whose reference app is not in this repository yet."""


class SnapshotLeak(SandboxError):
    """A snapshot holds a hidden value or canary it may not hold. It is a fault in the case."""

    def __init__(self, leaks: Sequence[Leak]) -> None:
        self.leaks = tuple(leaks)
        found = "; ".join(f"{leak.path}: {leak.case_name}/{leak.found}" for leak in self.leaks)
        super().__init__(f"the snapshot holds hidden values: {found}")


class Leak(NamedTuple):
    """One hidden value or canary found in a snapshot file.

    ``path`` is the file's path inside the snapshot, with forward slashes. ``found`` is
    the fact's id, or :data:`CANARY`. ``case_name`` names the sheet it belongs to, since
    two cases may use the same fact id.
    """

    path: str
    case_name: str
    found: str


@dataclass(frozen=True)
class Allowed:
    """The finds a case's snapshot may hold: its own repo facts, in its overlay's files only."""

    case_name: str
    facts: frozenset[str]
    paths: frozenset[str]

    def permits(self, leak: Leak) -> bool:
        """Whether ``leak`` is one the case placed in its overlay on purpose."""
        return (
            leak.case_name == self.case_name
            and leak.found in self.facts
            and leak.path in self.paths
        )


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


def overlay_of(case: PublicCase, *, root: Path = CASES) -> Path | None:
    """The case's overlay folder, or ``None`` when it has none."""
    overlay = root / split_of(case.name, root=root) / "repo" / case.name
    return overlay if overlay.is_dir() else None


def snapshot_for(
    case: PublicCase,
    folder: Path,
    *,
    root: Path = CASES,
    reference: Path = REFERENCE,
) -> Path:
    """Build the case's repository snapshot in ``<folder>/snapshot/`` and return its path.

    ``folder`` is the run's own folder, ``<out>/<arm>/<case>/<n>/``. The rung's base is
    copied first and the case's overlay over it, so an overlay file wins over the base
    file at the same path. A symbolic link in either is refused: it could point outside
    the snapshot.
    """
    base = rung_base(case.rung, reference=reference)
    snapshot = folder / SNAPSHOT_FOLDER
    snapshot.mkdir(parents=True)
    for source in (base, overlay_of(case, root=root)):
        if source is not None:
            _copy_source(source, snapshot)
    return snapshot


def allowed_for(case: PublicCase, sheet: FactSheet, *, root: Path = CASES) -> Allowed:
    """What :func:`scan` lets the case's own snapshot hold."""
    if sheet.case_name != case.name:
        raise ValueError(f"the fact sheet of {sheet.case_name!r} is not for {case.name!r}")
    overlay = overlay_of(case, root=root)
    paths = (
        frozenset(path for path, _ in _files(overlay)) if overlay is not None else frozenset[str]()
    )
    repo = frozenset(fact.id for fact in sheet.facts if fact.source == "repo")
    return Allowed(case_name=case.name, facts=repo, paths=paths)


def all_sheets(*, root: Path = CASES) -> list[FactSheet]:
    """Every fact sheet of every split, which a snapshot is scanned against."""
    return [
        facts.load(split, case.name, root=root)
        for split in SPLITS
        for case in cases(split, root=root)
    ]


def scan(snapshot: Path, sheets: Iterable[FactSheet], allowed: Allowed | None = None) -> list[Leak]:
    """Every hidden value or canary in ``snapshot`` that ``allowed`` does not permit.

    Each file is read whole, as UTF-8 with undecodable bytes replaced, and matched with
    :func:`~evals.facts.values_in` and :func:`~evals.facts.canary_in`. The list is in
    path order, then sheet order. An empty list means the snapshot is clean.
    """
    sheets = list(sheets)
    found: list[Leak] = []
    for path, file in _files(snapshot):
        text = file.read_bytes().decode("utf-8", errors="replace")
        for sheet in sheets:
            hits = sorted(values_in(text, sheet))
            if canary_in(text, sheet):
                hits.append(CANARY)
            for hit in hits:
                leak = Leak(path=path, case_name=sheet.case_name, found=hit)
                if allowed is None or not allowed.permits(leak):
                    found.append(leak)
    return found


def check_clean(snapshot: Path, sheets: Iterable[FactSheet], allowed: Allowed | None) -> None:
    """Raise :class:`SnapshotLeak` when :func:`scan` finds anything in ``snapshot``."""
    leaks = scan(snapshot, sheets, allowed)
    if leaks:
        raise SnapshotLeak(leaks)


def container_args(snapshot: Path, *, network: str | None = "none") -> list[str]:
    """The ``docker run`` options every arm's container uses, for ``snapshot``.

    The snapshot is bind-mounted read-only at ``/workspace``, the one mount, and is the
    working folder. The container is removed when it stops. ``network`` is ``"none"`` by
    default: no network at all, which is right when the agents run in the grader's
    process and only their file tools reach into the container. Pass ``None`` to leave
    the network option to the caller, such as Pydantic AI Harness's ``DockerSandbox``
    with ``allow_internet_access=False``, since Docker refuses two ``--network`` options.
    A system that runs inside the container, such as Claude Code, will pass the network
    that reaches only the port tools and the model provider.
    """
    source = snapshot.resolve()
    if not source.is_dir():
        raise SandboxError(f"the snapshot {source} is not a folder")
    if "," in str(source) or "=" in str(source):
        raise SandboxError(f"the snapshot path {source} cannot be written in a --mount option")
    args = [
        "--rm",
        "--mount",
        f"type=bind,source={source},target={WORKSPACE},readonly",
        "--workdir",
        WORKSPACE,
    ]
    if network is not None:
        args += ["--network", network]
    return args


def docker_available(executable: str = "docker") -> bool:
    """Whether ``docker info`` answers: the Docker command is installed and its daemon runs."""
    if shutil.which(executable) is None:
        return False
    try:
        result = subprocess.run([executable, "info"], capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def build_image(executable: str = "docker") -> str:
    """Build the sandbox image, tag it :data:`SANDBOX_IMAGE` and return its image id.

    Raises :class:`SandboxError` when Docker is not running or the build fails.
    """
    if not docker_available(executable):
        raise SandboxError("Docker is needed to build the sandbox image, and it is not running")
    build = [
        executable,
        "build",
        "--build-arg",
        f"CLAUDE_CODE_VERSION={CLAUDE_CODE_VERSION}",
        "--tag",
        SANDBOX_IMAGE,
        str(DOCKERFILE_FOLDER),
    ]
    if subprocess.run(build, check=False).returncode != 0:
        raise SandboxError(f"docker build of {SANDBOX_IMAGE} failed")
    inspect = subprocess.run(
        [executable, "image", "inspect", "--format", "{{.Id}}", SANDBOX_IMAGE],
        capture_output=True,
        text=True,
        check=False,
    )
    if inspect.returncode != 0:
        raise SandboxError(f"could not read the id of {SANDBOX_IMAGE}: {inspect.stderr.strip()}")
    return inspect.stdout.strip()


def _copy_source(source: Path, snapshot: Path) -> None:
    for path, file in _files(source):
        target = snapshot / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(file, target)


def _files(folder: Path) -> list[tuple[str, Path]]:
    """Every file under ``folder`` a snapshot holds, as (path with forward slashes, file).

    Folders in :data:`LEFT_OUT` are skipped. A symbolic link is refused.
    """
    found: list[tuple[str, Path]] = []
    for path in sorted(folder.rglob("*")):
        relative = path.relative_to(folder)
        if any(part in LEFT_OUT for part in relative.parts):
            continue
        if path.is_symlink():
            raise SandboxError(f"{path} is a symbolic link; a snapshot holds plain files only")
        if path.is_file():
            found.append((PurePosixPath(*relative.parts).as_posix(), path))
    return found
