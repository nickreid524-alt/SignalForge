"""Publication hygiene: nothing private may be tracked in this repository.

SignalForge is intended to be readable by anyone. These tests scan every tracked file for the things
that must never be there, and they fail on anything new rather than relying on someone remembering
to look.

Credential-shaped strings *do* appear in four places: the tests that prove the redaction layer
removes them. Those are listed explicitly below, so a real credential appearing anywhere else is a
failure rather than noise.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: Files allowed to contain credential-shaped strings, because proving redaction needs a sample.
REDACTION_FIXTURES = {
    "tests/test_replay_and_trace.py",
    "tests/test_anthropic_adapter.py",
    "tests/test_openai_adapter.py",
    "tests/test_provider_factory_and_cli.py",
    "tests/test_api_system.py",
    "tests/test_api_investigations.py",
    "tests/test_publication_hygiene.py",
}

CREDENTIALS = {
    "anthropic key": re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    "openai key": re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{32,}"),
    "github token": re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    "aws access key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "slack token": re.compile(r"xox[bapsr]-[A-Za-z0-9-]{20,}"),
    "google api key": re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    "private key block": re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY"),
    "bearer token": re.compile(r"Bearer\s+[A-Za-z0-9._~+/-]{30,}"),
    "connection string": re.compile(r"(?:postgres|postgresql|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s:@]+:[^\s@]+@"),
}

#: Nothing in a public repository should carry a developer's filesystem or identity.
PRIVATE = {
    "windows user path": re.compile(r"[A-Za-z]:\\+Users\\+[A-Za-z0-9._-]+"),
    "unix home path": re.compile(r"/(?:home|Users)/[a-z][a-z0-9._-]{2,}"),
    "email address": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.(?:com|net|org|io|dev|co\.uk)\b"),
}

#: Paths that may legitimately mention a path pattern, because they assert one is *absent*.
PATH_ASSERTION_FILES = {
    "tests/test_api_system.py",
    "tests/test_api_investigations.py",
    "tests/test_publication_hygiene.py",
    "src/signalforge/api/trace_view.py",
}

BINARY_SUFFIXES = {".png", ".ico", ".woff", ".woff2", ".jpg", ".jpeg", ".gif", ".zip", ".sqlite"}


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def readable_tracked_files() -> list[tuple[str, str]]:
    files = []
    for relative in tracked_files():
        path = ROOT / relative
        if path.suffix.lower() in BINARY_SUFFIXES or not path.is_file():
            continue
        try:
            files.append((relative, path.read_text(encoding="utf-8")))
        except UnicodeDecodeError:
            pytest.fail(f"tracked file is not text and not a known binary type: {relative}")
    return files


def test_the_repository_tracks_something_and_no_build_output():
    files = tracked_files()
    assert len(files) > 200, "git ls-files returned an implausibly small set"

    forbidden = ("node_modules/", "/dist/", ".venv/", "playwright-report/", "test-results/",
                 ".vite/", "__pycache__/", "/runs/")
    for relative in files:
        normalised = f"/{relative}"
        for fragment in forbidden:
            assert fragment not in normalised, f"build or runtime output is tracked: {relative}"
        assert not relative.endswith((".sqlite", ".sqlite3", ".db", ".pyc", ".tsbuildinfo")), relative
        # A .env must never be tracked; `.env.example` holds variable names and placeholders only.
        name = Path(relative).name
        assert name != ".env", relative
        assert not (name.startswith(".env.") and name != ".env.example"), relative
    assert ".env.example" in files


def test_only_the_five_portfolio_screenshots_are_tracked():
    images = [f for f in tracked_files() if f.lower().endswith((".png", ".jpg", ".jpeg", ".gif"))]
    assert sorted(images) == [
        "docs/screenshots/evaluations.png",
        "docs/screenshots/grounded-report.png",
        "docs/screenshots/incident-queue.png",
        "docs/screenshots/investigation-workspace.png",
        "docs/screenshots/mcp-catalogue.png",
    ], images


def test_no_credentials_outside_the_redaction_fixtures():
    offenders = []
    for relative, text in readable_tracked_files():
        if relative in REDACTION_FIXTURES:
            continue
        for name, pattern in CREDENTIALS.items():
            for match in pattern.finditer(text):
                line = text[: match.start()].count("\n") + 1
                offenders.append(f"{relative}:{line} [{name}] {match.group(0)[:40]}")
    assert not offenders, "credential-shaped strings found:\n" + "\n".join(offenders)


def test_redaction_fixtures_contain_only_obviously_fake_credentials():
    """The allowed samples must be unmistakably synthetic, not a real key someone pasted."""
    fake_markers = ("test", "not-real", "not-a-real", "abcdef", "ABCDEF", "0000", "example", "placeholder")
    for relative in sorted(REDACTION_FIXTURES):
        path = ROOT / relative
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in CREDENTIALS.values():
            for match in pattern.finditer(text):
                sample = match.group(0)
                assert any(marker in sample for marker in fake_markers), \
                    f"{relative} contains a credential-shaped string that does not look synthetic: {sample[:40]}"


def test_no_personal_paths_or_addresses():
    offenders = []
    for relative, text in readable_tracked_files():
        for name, pattern in PRIVATE.items():
            if relative in PATH_ASSERTION_FILES and "path" in name:
                continue
            for match in pattern.finditer(text):
                line = text[: match.start()].count("\n") + 1
                offenders.append(f"{relative}:{line} [{name}] {match.group(0)[:60]}")
    assert not offenders, "private information found:\n" + "\n".join(offenders)


def test_no_hidden_provider_reasoning_is_shipped():
    """No fixture or artifact may carry a vendor's private reasoning."""
    offenders = []
    leak = re.compile(r'"(?:thinking|redacted_thinking)"\s*:\s*"[^"]{3,}|"encrypted_content"\s*:\s*"[A-Za-z0-9+/=]{40,}')
    for relative, text in readable_tracked_files():
        for match in leak.finditer(text):
            line = text[: match.start()].count("\n") + 1
            offenders.append(f"{relative}:{line} {match.group(0)[:60]}")
    assert not offenders, "provider reasoning content is tracked:\n" + "\n".join(offenders)


def test_the_frontend_never_renders_untrusted_content_as_html():
    offenders = []
    for relative, text in readable_tracked_files():
        if not relative.startswith("web/src"):
            continue
        if "dangerouslySetInnerHTML=" in text:
            offenders.append(relative)
    assert not offenders, f"evidence must render as text, never markup: {offenders}"


def test_cors_is_never_a_wildcard():
    wildcard = re.compile(r'allow_origins\s*=\s*\[?\s*["\']\*|Access-Control-Allow-Origin["\']?\s*[:=]\s*["\']\*')
    for relative, text in readable_tracked_files():
        assert not wildcard.search(text), f"wildcard CORS origin in {relative}"


def test_git_history_never_contained_a_file_that_was_later_removed():
    """A clean HEAD says nothing about history; check that nothing was ever added and deleted."""
    added = subprocess.run(["git", "log", "--all", "--pretty=format:", "--name-only", "--diff-filter=A"],
                           cwd=ROOT, capture_output=True, text=True, check=True).stdout
    ever = {line.strip() for line in added.splitlines() if line.strip()}
    now = set(tracked_files())
    removed = sorted(ever - now)
    assert not removed, (
        "paths were added and later removed; review them for secrets before publishing:\n"
        + "\n".join(removed)
    )
