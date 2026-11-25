"""The sandbox: what a system that reads files is given, and the check that it is clean.

A system under test that reads files sees one folder, the case's workspace snapshot,
mounted read-only at ``/workspace`` in a Docker container of its own. Nothing else of this
machine is in that container: not the fact sheets, not the simulator, not the graders.

* :func:`build_snapshot` builds a snapshot from a base folder and an overlay. From the
  base it copies only the files git tracks, so an ignored file never enters a snapshot.
  It leaves out the folders a suite names, such as version control and build output.
* :func:`scan` reads every file of a snapshot and reports each hidden value or canary, of
  every fact sheet given, that it finds. The only finds it allows are the case's own
  environment facts, in files its overlay placed there on purpose.
* :func:`container_args` gives the ``docker run`` options every container uses.

A suite decides what goes into a snapshot. This module is on the grader side. It imports
nothing from the factory.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from ._facts import FactSheet, canary_in, values_in
from ._harness import WorkspaceError

WORKSPACE = "/workspace"
"""Where a container sees the snapshot, and the folder its commands start in."""

SNAPSHOT_FOLDER = "snapshot"
"""The name of the snapshot folder inside a run's folder."""

LEFT_OUT: frozenset[str] = frozenset({".git"})
"""Folders a snapshot never holds unless a suite says otherwise: version control."""

CANARY = "canary"
"""What :func:`scan` reports in place of a fact id when it finds a canary."""


class SandboxError(WorkspaceError):
    """A snapshot could not be built, or Docker could not do what was asked."""


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
    """The finds a case's snapshot may hold: its environment facts, in its overlay's files."""

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


def build_snapshot(
    folder: Path,
    *,
    base: Path | None = None,
    overlay: Path | None = None,
    left_out: frozenset[str] = LEFT_OUT,
) -> Path:
    """Build a snapshot in ``<folder>/snapshot/`` and return its path.

    ``folder`` is the run's own folder. ``base`` is copied first and ``overlay`` over it,
    so an overlay file wins over the base file at the same path. Only the base's files
    that git tracks are copied; a base outside a git repository, as in a test's temporary
    folder, is walked instead. A symbolic link in either is refused: it could point
    outside the snapshot. A folder named in ``left_out`` is never copied.
    """
    snapshot = folder / SNAPSHOT_FOLDER
    snapshot.mkdir(parents=True)
    if base is not None:
        _copy_files(_base_files(base, left_out), snapshot)
    if overlay is not None:
        _copy_files(files_of(overlay, left_out), snapshot)
    return snapshot


def allowed_for(sheet: FactSheet, overlay: Path | None) -> Allowed:
    """What :func:`scan` lets a case's own snapshot hold: its environment facts, in
    ``overlay``'s files."""
    paths = (
        frozenset(path for path, _ in files_of(overlay))
        if overlay is not None
        else frozenset[str]()
    )
    held = frozenset(fact.id for fact in sheet.facts if fact.source == "environment")
    return Allowed(case_name=sheet.case_name, facts=held, paths=paths)


def scan(
    snapshot: Path,
    sheets: Iterable[FactSheet],
    allowed: Allowed | None = None,
    *,
    left_out: frozenset[str] = LEFT_OUT,
) -> list[Leak]:
    """Every hidden value or canary in ``snapshot`` that ``allowed`` does not permit.

    A folder named in ``left_out`` is skipped. Each file is read whole, as UTF-8 with
    undecodable bytes replaced, and matched with :func:`values_in` and :func:`canary_in`.
    The list is in path order, then sheet order. An empty list means the snapshot is clean.
    """
    sheets = list(sheets)
    found: list[Leak] = []
    for path, file in files_of(snapshot, left_out):
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
    """The ``docker run`` options every container uses, for ``snapshot``.

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


def build_image(
    tag: str,
    context: Path,
    *,
    build_args: Mapping[str, str] | None = None,
    executable: str = "docker",
) -> str:
    """Build the image in ``context``, tag it ``tag`` and return its image id.

    Raises :class:`SandboxError` when Docker is not running or the build fails.
    """
    if not docker_available(executable):
        raise SandboxError("Docker is needed to build the sandbox image, and it is not running")
    build = [executable, "build"]
    for name, value in (build_args or {}).items():
        build += ["--build-arg", f"{name}={value}"]
    build += ["--tag", tag, str(context)]
    if subprocess.run(build, check=False).returncode != 0:
        raise SandboxError(f"docker build of {tag} failed")
    inspect = subprocess.run(
        [executable, "image", "inspect", "--format", "{{.Id}}", tag],
        capture_output=True,
        text=True,
        check=False,
    )
    if inspect.returncode != 0:
        raise SandboxError(f"could not read the id of {tag}: {inspect.stderr.strip()}")
    return inspect.stdout.strip()


def _copy_files(files: Iterable[tuple[str, Path]], snapshot: Path) -> None:
    for path, file in files:
        target = snapshot / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(file, target)


def _base_files(base: Path, left_out: frozenset[str]) -> list[tuple[str, Path]]:
    """The files of a base a snapshot holds: those git tracks, as :func:`files_of` gives.

    Git's own list leaves out every ignored file, such as a local database. When ``base``
    is not in a git repository, or git is not installed, the folder is walked by
    :func:`files_of` instead.
    """
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z", "--", "."], cwd=base, capture_output=True, check=False
        )
    except OSError:
        return files_of(base, left_out)
    if listed.returncode != 0:
        return files_of(base, left_out)
    found: list[tuple[str, Path]] = []
    for name in sorted(listed.stdout.decode("utf-8").split("\0")):
        if not name:
            continue
        relative = PurePosixPath(name)
        found.extend(_kept(base, relative.parts, left_out))
    return found


def files_of(folder: Path, left_out: frozenset[str] = LEFT_OUT) -> list[tuple[str, Path]]:
    """Every file under ``folder`` a snapshot holds, as (path with forward slashes, file).

    Folders in ``left_out`` are skipped. A symbolic link is refused.
    """
    found: list[tuple[str, Path]] = []
    for path in sorted(folder.rglob("*")):
        found.extend(_kept(folder, path.relative_to(folder).parts, left_out))
    return found


def _kept(folder: Path, parts: tuple[str, ...], left_out: frozenset[str]) -> list[tuple[str, Path]]:
    """The file at ``parts`` under ``folder``, unless it is left out or not a file."""
    if any(part in left_out for part in parts):
        return []
    path = folder.joinpath(*parts)
    if path.is_symlink():
        raise SandboxError(f"{path} is a symbolic link; a snapshot holds plain files only")
    return [(PurePosixPath(*parts).as_posix(), path)] if path.is_file() else []
