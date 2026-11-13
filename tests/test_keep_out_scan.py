"""The keep-out scan must catch leaks without ever printing a private term."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "keep_out_scan", ROOT / "scripts/keep_out_scan.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


scan = _load()

# Leaks are assembled at run time so this file holds no text the scan would refuse.
USERS = "/" + "Users"
HOME = "/" + "home"
HOME_PATH = f"{USERS}/someone/project/file.py"


def _rules(line: str) -> list[str]:
    return [finding.rule for finding in scan.scan_text("f", line, ())]


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.mark.parametrize(
    "line",
    [
        f"see {HOME_PATH}",
        f"cwd = {USERS}/someone",
        f'"{USERS}/someone"',
        f"PATH={HOME}/someone:/usr/bin",
        "C:" + "\\Users\\" + "someone" + "\\repo",
    ],
)
def test_a_personal_home_directory_is_found(line: str) -> None:
    assert _rules(line) == ["personal home directory"]


@pytest.mark.parametrize(
    "line",
    [
        f"{USERS}/you/project",
        f"{HOME}/runner/work",
        f"https://example.org{HOME}/docs/page",
        "a relative path like docs/home/page.md",
    ],
)
def test_placeholders_and_web_addresses_are_not_home_directories(line: str) -> None:
    assert _rules(line) == []


@pytest.mark.parametrize(
    "token",
    [
        "gh" + "p_" + "a" * 36,
        "github" + "_pat_" + "a" * 60,
        "py" + "pi-" + "A" * 60,
        "sk" + "-ant-" + "api03-" + "a" * 30,
        "sk" + "-" + "a" * 32,
        "AK" + "IA" + "A" * 16,
        "xo" + "xb-" + "1" * 24,
        "-----BEGIN " + "PGP PRIVATE KEY BLOCK-----",
        "-----BEGIN " + "RSA PRIVATE KEY-----",
    ],
)
def test_a_credential_or_private_key_is_found(token: str) -> None:
    assert len(_rules(f'value = "{token}"')) == 1


def test_hyphenated_prose_is_not_a_credential() -> None:
    assert _rules("an sk-learn-compatible-estimator-interface") == []


def test_a_private_term_matches_whole_words_and_ignores_case() -> None:
    terms = scan.parse_terms("Acme\n# a comment\n\n")

    found = scan.scan_text("a.md", "built at ACME today\nacmeology\nacme_client", terms)

    assert [finding.line for finding in found] == [1, 3]


def test_a_case_sensitive_term_only_matches_its_case() -> None:
    terms = scan.parse_terms("cs:ZEBRA")

    assert scan.scan_text("a.md", "a zebra crossing", terms) == []
    assert len(scan.scan_text("a.md", "the ZEBRA project", terms)) == 1


def test_a_finding_never_prints_the_private_term() -> None:
    terms = scan.parse_terms("Acme")

    (finding,) = scan.scan_text("a.md", "built at Acme", terms)

    assert str(finding) == "a.md:1: private term 1"


def test_a_term_in_a_path_is_reported_without_the_path() -> None:
    terms = scan.parse_terms("acme")

    (hidden,) = scan.scan_path("docs/acme-notes.md", terms, reveal=False)
    (shown,) = scan.scan_path("docs/acme-notes.md", terms, reveal=True)

    assert "acme" not in str(hidden)
    assert hidden.rule == "private term 1 in the path"
    assert str(shown) == "docs/acme-notes.md:0: private term 1 in the path"


@pytest.mark.parametrize("closer", ["", " -->", " */"])
def test_an_allowed_line_is_skipped(closer: str) -> None:
    assert _rules(f"path = '{HOME_PATH}'  # keep-out: allow{closer}") == []


def test_terms_come_from_the_environment_before_the_file(tmp_path: Path) -> None:
    file = tmp_path / "terms.txt"
    file.write_text("FromFile\n", encoding="utf-8")

    from_env = scan.load_terms({"KEEP_OUT_TERMS": "One, Two", "KEEP_OUT_FILE": str(file)})
    from_file = scan.load_terms({"KEEP_OUT_FILE": str(file)})

    assert len(from_env) == 2
    assert len(from_file) == 1


def test_the_scan_covers_tracked_new_binary_and_oddly_encoded_files(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    (tmp_path / "clean.md").write_text("nothing to see\n", encoding="utf-8")
    (tmp_path / "tracked.md").write_text(f"{HOME_PATH}\n", encoding="utf-8")
    _git(tmp_path, "add", "tracked.md")
    (tmp_path / "new.md").write_text("made by acme\n", encoding="utf-8")
    (tmp_path / "latin1.txt").write_bytes(f"caf\xe9 {HOME_PATH}\n".encode("latin-1"))
    (tmp_path / "acme-logo.png").write_bytes(b"\x89PNG\x00\x00")
    (tmp_path / "ignored.md").write_text(f"{HOME_PATH}\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("ignored.md\n", encoding="utf-8")

    findings = scan.scan_files(
        tmp_path, scan.candidate_files(tmp_path), scan.parse_terms("acme"), reveal=True
    )

    assert sorted(str(finding) for finding in findings) == [
        "acme-logo.png:0: private term 1 in the path",
        "latin1.txt:1: personal home directory",
        "new.md:1: private term 1",
        "tracked.md:1: personal home directory",
    ]


def test_the_exit_code_says_whether_the_scan_ran(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    monkeypatch.delenv("KEEP_OUT_TERMS", raising=False)
    monkeypatch.setenv("KEEP_OUT_FILE", str(tmp_path / "missing.txt"))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))

    assert scan.main(["--root", str(repository)]) == 0
    assert "no private terms loaded" in capsys.readouterr().out
    assert scan.main(["--root", str(repository), "--require-terms"]) == 2

    (repository / "leak.md").write_text(f"{HOME_PATH}\n", encoding="utf-8")
    assert scan.main(["--root", str(repository)]) == 1

    outside = tmp_path / "not-a-repository"
    outside.mkdir()
    assert scan.main(["--root", str(outside)]) == 2
