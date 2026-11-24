"""The cases of Software Factory Evals.

A case is two files with the same filename, and the filename is the case name, plus an
optional overlay folder::

    evals/design/cases/<split>/<name>.json          the request and the rung
    evals/design/cases/<split>/facts/<name>.json    the fact sheet
    evals/design/cases/<split>/repo/<name>/         the overlay, if any

:func:`cases` reads only the first: the public half, which a system is given. The fact
sheet is read by :func:`load_sheet`, on the grader side only. The smoke case exists to
debug the harness and is never scored.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Literal

from pydantic_ai_factory.evals import Case, FactSheet, load_fact_sheet

CASES = Path(__file__).parent
"""The folder that holds one folder for each split."""

Split = Literal["smoke", "dev", "test"]
SPLITS: tuple[Split, ...] = ("smoke", "dev", "test")

Rung = Literal["empty", "r0", "r1"]
"""The base a case's repository snapshot starts from: nothing, or a reference app."""

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


class PublicCase(Case):
    """What a system is given for a case: the request, and the rung its snapshot starts from."""

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


def load_sheet(split: str, case_name: str, *, root: Path = CASES) -> FactSheet:
    """Read ``<root>/<split>/facts/<case_name>.json``. Case names are unique across splits."""
    for name in (split, case_name):
        _check_name(name)
    return load_fact_sheet(root / split / "facts" / f"{case_name}.json", case_name)


def sheet_for(case_name: str, *, root: Path = CASES) -> FactSheet:
    """The fact sheet of ``case_name``, from whichever split holds it."""
    return load_sheet(split_of(case_name, root=root), case_name, root=root)


def all_sheets(*, root: Path = CASES) -> list[FactSheet]:
    """Every fact sheet of every split, which a snapshot is scanned against."""
    return [
        load_sheet(split, case.name, root=root)
        for split in SPLITS
        for case in cases(split, root=root)
    ]


def _split_folder(split: str, root: Path) -> Path:
    if split not in SPLITS:
        raise ValueError(f"{split!r} is not a split; the splits are {', '.join(SPLITS)}")
    return root / split


def _check_name(name: str) -> None:
    if not _NAME.fullmatch(name):
        raise ValueError(f"{name!r} is not a plain name")
