"""The cases of Software Factory Evals, as a system under test may see them.

A case is two files with the same filename, and the filename is the case name, plus an
optional overlay folder::

    evals/cases/<split>/<name>.json          the request and the rung
    evals/cases/<split>/facts/<name>.json    the fact sheet
    evals/cases/<split>/repo/<name>/         the overlay, if any

This module reads only the first: the public half. The fact sheet is read by
:mod:`evals.facts`, on the grader side. The smoke case exists to debug the harness and is
never scored.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

CASES = Path(__file__).parent
"""The folder that holds one folder for each split."""

Split = Literal["smoke", "dev", "test"]
SPLITS: tuple[Split, ...] = ("smoke", "dev", "test")

Rung = Literal["empty", "r0", "r1"]
"""The base a case's repository snapshot starts from: nothing, or a reference app."""

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


class PublicCase(BaseModel):
    """What a system is given for a case: the request, and the rung its snapshot starts from."""

    model_config = ConfigDict(frozen=True)

    name: str
    request: str
    rung: Rung


def cases(split: str, *, root: Path = CASES) -> list[PublicCase]:
    """The cases of ``split``, in name order. A split with no folder yet has none."""
    folder = _split_folder(split, root)
    if not folder.is_dir():
        return []
    found: list[PublicCase] = []
    for path in sorted(folder.glob("*.json")):
        case = PublicCase.model_validate_json(path.read_text(encoding="utf-8"))
        if case.name != path.stem:
            raise ValueError(f"{path.name} holds the case {case.name!r}")
        found.append(case)
    return found


def split_of(case_name: str, *, root: Path = CASES) -> Split:
    """The split that holds ``case_name``. Case names are unique across splits."""
    _check_name(case_name)
    for split in SPLITS:
        if (root / split / f"{case_name}.json").is_file():
            return split
    raise KeyError(case_name)


def case_hash(split: str, case_name: str, *, root: Path = CASES) -> str:
    """The SHA-256 hash of a case, which the run card carries.

    It runs over the bytes of the case file, then the fact sheet, then each overlay
    file's path and bytes, in path order.
    """
    _check_name(case_name)
    folder = _split_folder(split, root)
    digest = hashlib.sha256()
    digest.update((folder / f"{case_name}.json").read_bytes())
    digest.update((folder / "facts" / f"{case_name}.json").read_bytes())
    overlay = folder / "repo" / case_name
    if overlay.is_dir():
        files = sorted(p for p in overlay.rglob("*") if p.is_file())
        for path in files:
            digest.update(path.relative_to(overlay).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _split_folder(split: str, root: Path) -> Path:
    if split not in SPLITS:
        raise ValueError(f"{split!r} is not a split; the splits are {', '.join(SPLITS)}")
    return root / split


def _check_name(name: str) -> None:
    if not _NAME.fullmatch(name):
        raise ValueError(f"{name!r} is not a plain name")
