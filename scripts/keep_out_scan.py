"""Refuse to publish text that must stay out of a public repository.

Two kinds of text are caught:

* generic leaks that are wrong in any public repository: a personal home directory,
  a credential, a private key;
* private terms that only the maintainer knows, such as names from unrelated work.

Private terms never live in this repository. They are read from the ``KEEP_OUT_TERMS``
environment variable (one term per line, or comma separated) or from the file named by
``KEEP_OUT_FILE``, which defaults to ``~/.config/pydantic-ai-factory/keep-out.txt``.

A term is matched as a whole word, ignoring case. Prefix it with ``cs:`` to match case.
Whole-word matching means ``acme`` finds ``acme_client`` and ``acme.com`` but not
``AcmeClient``, so list joined forms as their own terms.

A finding prints the file, the line and the kind of rule. It never prints a private term.
When a term is in a file's path, the path is withheld too, so the output is safe in a
public log. Pass ``--reveal`` on your own machine to see those paths.

A line is skipped when it ends with ``keep-out: allow``, optionally followed by a comment
closer such as ``-->``.

Exit codes: 0 clean, 1 findings, 2 the scan could not run as asked.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TERMS_FILE = Path("~/.config/pydantic-ai-factory/keep-out.txt")
MAX_BYTES = 2_000_000

_ALLOWED = re.compile(r"keep-out: allow\s*(?:-->|\*/)?\s*$")
_SEGMENT = r"[A-Za-z0-9._-]"
_PLACEHOLDER = rf"(?:runner|user|username|you|me|name|example|Shared)(?!{_SEGMENT})"
_END = r"""(?=[/\\"'\s:)]|$)"""

GENERIC_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "personal home directory",
        re.compile(rf"(?<![\w.:/-])(?:/Users|/home)/(?!{_PLACEHOLDER}){_SEGMENT}+{_END}"),
    ),
    (
        "personal home directory",
        re.compile(rf"(?<![A-Za-z])[A-Za-z]:\\Users\\(?!{_PLACEHOLDER}){_SEGMENT}+{_END}"),
    ),
    ("credential", re.compile(r"\bsk-(?:(?:ant|proj)-[A-Za-z0-9_-]{20,}|[A-Za-z0-9]{20,})")),
    ("credential", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("credential", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{50,}")),
    ("credential", re.compile(r"\bpypi-[A-Za-z0-9_-]{50,}")),
    ("credential", re.compile(r"\b(?:xox[abprs]|xapp)-[A-Za-z0-9-]{20,}")),
    ("credential", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("credential", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("private key", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY(?: BLOCK)?-----")),
)


@dataclass(frozen=True)
class Finding:
    """One line, or one path, that must not be published."""

    path: str
    line: int
    rule: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}"


def parse_terms(text: str) -> tuple[re.Pattern[str], ...]:
    """Turn a private term list into whole-word patterns.

    Blank lines and lines starting with ``#`` are ignored.
    """
    patterns: list[re.Pattern[str]] = []
    for raw in re.split(r"[\n,]", text):
        term = raw.strip()
        if not term or term.startswith("#"):
            continue
        flags = re.IGNORECASE
        if term.startswith("cs:"):
            term, flags = term[3:].strip(), 0
        if term:
            patterns.append(re.compile(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", flags))
    return tuple(patterns)


def load_terms(environ: dict[str, str] | None = None) -> tuple[re.Pattern[str], ...]:
    """Load private terms from the environment, then from the terms file."""
    env = dict(os.environ) if environ is None else environ
    inline = env.get("KEEP_OUT_TERMS", "")
    if inline.strip():
        return parse_terms(inline)
    path = Path(env.get("KEEP_OUT_FILE") or DEFAULT_TERMS_FILE).expanduser()
    if path.is_file():
        return parse_terms(path.read_text(encoding="utf-8"))
    return ()


def scan_text(path: str, text: str, terms: Sequence[re.Pattern[str]]) -> list[Finding]:
    """Return every finding in one file's text."""
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if _ALLOWED.search(line):
            continue
        for rule, pattern in GENERIC_RULES:
            if pattern.search(line):
                findings.append(Finding(path, number, rule))
        for index, pattern in enumerate(terms, start=1):
            if pattern.search(line):
                findings.append(Finding(path, number, f"private term {index}"))
    return findings


def scan_path(path: str, terms: Sequence[re.Pattern[str]], *, reveal: bool) -> list[Finding]:
    """Return a finding for each private term in a file's path.

    The path itself would give the term away, so it is replaced by a short digest
    unless ``reveal`` is set.
    """
    shown = path
    if not reveal:
        digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:8]
        shown = f"<path withheld {digest}>"
    return [
        Finding(shown, 0, f"private term {index} in the path")
        for index, pattern in enumerate(terms, start=1)
        if pattern.search(path)
    ]


def candidate_files(root: Path) -> list[Path]:
    """List tracked files and new files that git does not ignore."""
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8")
    return [root / name for name in listed.split("\0") if name]


def scan_files(
    root: Path,
    files: Iterable[Path],
    terms: Sequence[re.Pattern[str]],
    *,
    reveal: bool = False,
) -> list[Finding]:
    """Scan the path of every file, and the text of every file that is text."""
    findings: list[Finding] = []
    for file in files:
        relative = file.relative_to(root).as_posix()
        findings.extend(scan_path(relative, terms, reveal=reveal))
        if not file.is_file() or file.stat().st_size > MAX_BYTES:
            continue
        data = file.read_bytes()
        if b"\0" in data:
            continue
        findings.extend(scan_text(relative, data.decode("utf-8", errors="replace"), terms))
    return findings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refuse to publish text that must stay out of a public repository."
    )
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root")
    parser.add_argument(
        "--require-terms",
        action="store_true",
        help="fail when no private terms were loaded",
    )
    parser.add_argument(
        "--reveal",
        action="store_true",
        help="print paths that contain a private term; never use in a public log",
    )
    args = parser.parse_args(argv)
    root: Path = args.root.resolve()

    terms = load_terms()
    if not terms:
        message = "keep-out: no private terms loaded; generic rules only"
        if args.require_terms:
            print(f"{message}. Set KEEP_OUT_TERMS or KEEP_OUT_FILE.", file=sys.stderr)
            return 2
        print(message)

    try:
        files = candidate_files(root)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print(f"keep-out: {root} is not a git repository, or git is missing", file=sys.stderr)
        return 2

    findings = scan_files(root, files, terms, reveal=args.reveal)
    for finding in findings:
        print(finding, file=sys.stderr)
    if findings:
        print(f"keep-out: {len(findings)} finding(s); nothing may be published", file=sys.stderr)
        return 1
    print(f"keep-out: clean ({len(terms)} private term(s) checked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
