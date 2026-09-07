"""The repository must not itself contain anything a secret scanner rejects.

This is dogfooding, and it is also practical. GitHub push protection matches
the *pattern*, not the validity: a fake credential written as a literal blocks
a push exactly as a real one would, and the fix at that point is a history
rewrite rather than a commit. Catching it here costs one test run.

The patterns below are assembled from fragments for the same reason the
fixtures in ``tests/unit/test_redaction.py`` are. A guard that trips on itself
is not a guard.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Name -> pattern. Deliberately narrower than the redaction engine's own
#: detectors: this asks "would a scanner reject the push", not "is this
#: sensitive".
CREDENTIAL_PATTERNS: dict[str, re.Pattern[str]] = {
    "Slack token": re.compile("xox" + r"[bapsr]-[A-Za-z0-9]{8,}"),
    "GitHub token": re.compile("gh" + r"[pousr]_[A-Za-z0-9]{20,}"),
    "OpenAI-style key": re.compile("s" + r"k-[A-Za-z0-9]{20,}"),
    "AWS access key id": re.compile("AKI" + r"A[0-9A-Z]{16}"),
    "Google API key": re.compile("AIza" + r"[0-9A-Za-z_-]{30,}"),
    "JSON web token": re.compile(
        "ey" + r"J[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    ),
    "PEM private key": re.compile("-----BEGIN [A-Z ]*PRIVATE" + " KEY-----"),
}

#: Files that legitimately contain a credential-shaped string, each with the
#: reason. Deliberately empty: if this list ever grows, the entry needs a
#: justification a reviewer would accept.
ALLOWED: dict[str, str] = {}

BINARY_SUFFIXES = frozenset({".pdf", ".png", ".jpg", ".webp", ".ico", ".woff", ".woff2"})


#: Directories that exist because a tool put them there, not because anyone
#: wrote them. Only consulted when git cannot be asked what is tracked.
_NOT_SOURCE = frozenset({
    ".git", ".venv", "venv", "__pycache__", "node_modules",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "build", "dist",
})


def _tracked_files() -> list[Path]:
    """Every file git tracks. Falls back to a walk outside a git checkout."""
    try:
        output = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True, timeout=30,
        ).stdout
        names = [n for n in output.split("\0") if n]
    except (subprocess.SubprocessError, FileNotFoundError):  # pragma: no cover
        # Outside a checkout there is no index to ask, so the tree is walked
        # instead. Build and cache directories are skipped: they hold copies
        # of test names and fixtures, and reporting those as findings would
        # cry wolf about files nobody could ever commit.
        names = [
            str(p.relative_to(REPO_ROOT))
            for p in REPO_ROOT.rglob("*")
            if p.is_file() and not (set(p.parts) & _NOT_SOURCE)
        ]
    return [REPO_ROOT / n for n in sorted(names)]


@pytest.mark.security()
def test_no_tracked_file_contains_a_credential_shaped_literal() -> None:
    findings: list[str] = []

    for path in _tracked_files():
        if path.suffix in BINARY_SUFFIXES or not path.is_file():
            continue
        relative = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
        if relative in ALLOWED:
            continue
        try:
            text = path.read_text("utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        for name, pattern in CREDENTIAL_PATTERNS.items():
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                findings.append(f"{relative}:{line}: {name}")

    assert findings == [], (
        "credential-shaped literals found; a push containing these will be "
        "rejected by secret scanning. Assemble the value at runtime from "
        "fragments, as tests/unit/test_redaction.py does:\n  "
        + "\n  ".join(findings)
    )


@pytest.mark.security()
def test_no_environment_file_is_tracked() -> None:
    """`.env.example` is fine. `.env` is not, and neither are keys or profiles."""
    forbidden = {".env", ".envrc", "credentials.json", "id_rsa", "id_ed25519"}
    forbidden_suffixes = {".pem", ".key", ".p12", ".pfx"}

    offenders = [
        str(p.relative_to(REPO_ROOT))
        for p in _tracked_files()
        if p.name in forbidden or p.suffix in forbidden_suffixes
    ]
    assert offenders == [], f"secret-bearing files are tracked: {offenders}"


@pytest.mark.security()
def test_shell_scripts_use_unix_line_endings() -> None:
    """A script that acquires CRLF fails on Linux blaming the interpreter.

    `.gitattributes` pins this, but only for people whose git honours it, so it
    is also checked here.
    """
    offenders = [
        str(p.relative_to(REPO_ROOT))
        for p in _tracked_files()
        if p.suffix == ".sh" and p.is_file() and b"\r\n" in p.read_bytes()
    ]
    assert offenders == [], f"scripts contain CRLF line endings: {offenders}"


@pytest.mark.security()
def test_shell_scripts_are_executable() -> None:
    """A non-executable script breaks `make verify` in a fresh clone."""
    import os
    import stat

    if os.name == "nt":  # pragma: no cover - Windows has no executable bit
        pytest.skip("no executable bit on Windows")

    offenders = [
        str(p.relative_to(REPO_ROOT))
        for p in _tracked_files()
        if p.suffix == ".sh" and p.is_file()
        and not p.stat().st_mode & stat.S_IXUSR
    ]
    assert offenders == [], f"scripts are not executable: {offenders}"


def test_every_test_named_in_security_md_exists() -> None:
    """The invariant table is a list of claims. A claim citing a test that does
    not exist is worse than no claim: it reads as evidence and is not.

    Renaming a test is easy and normal. This makes the table follow.
    """
    import re

    security = (REPO_ROOT / "SECURITY.md").read_text("utf-8")
    references = sorted(set(re.findall(r"`(tests/[^`]+)`", security)))
    assert references, "SECURITY.md cites no tests at all"

    missing: list[str] = []
    for reference in references:
        path_part, _, test_name = reference.partition("::")
        path = REPO_ROOT / path_part
        if not path.exists():
            missing.append(f"{reference} (no such path)")
        elif test_name and f"def {test_name}(" not in path.read_text("utf-8"):
            missing.append(f"{reference} (no such test in file)")

    assert not missing, "SECURITY.md points at tests that do not exist: " + ", ".join(missing)
