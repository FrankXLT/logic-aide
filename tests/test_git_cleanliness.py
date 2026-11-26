"""Git cleanliness, .gitignore enforcement, PII scrubbing, and Hardware Deadbolt tests."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

MUST_IGNORE_PATHS = (
    "private_references/_probe.bin",
    "Private References/_probe.md",
    "data/heritage_raw/figures/turbo_maps/probe.gif",
    "data/heritage_raw/discovery_queue/probe.json",
    "rom/inputs/stock.BIN",
    "rom/stock_baselines/stock.bin",
    "candidate.bin",
    "candidate.BIN",
    "dump.s19",
    "dump.S19",
)

MUST_NOT_IGNORE_PATHS = (
    "data/heritage_raw/.gitkeep",
    "rom/inputs/README.md",
    "rom/stock_baselines/README.md",
)

FORBIDDEN_INDEX_PREFIXES = ("private_references/", "Private References/", "data/heritage_raw/")
FORBIDDEN_INDEX_SUFFIXES = (".bin", ".s19", ".parquet", ".sqlite", ".sqlite3")

CLEANROOM_ONLY_DIRS = ("profiles", "knowledge", "wiki", "scripts")
CODE_DIRS = ("src/logicaide",)
SCAN_EXTENSIONS = frozenset({".py", ".md", ".yaml", ".yml", ".json", ".toml", ".txt"})

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
HOME_USER_PATH_RE = re.compile(r"/home/[A-Za-z0-9_.-]+")
WIN_USER_PATH_RE = re.compile(r"[A-Za-z]:\\Users\\[A-Za-z0-9_.-]+", re.IGNORECASE)
PERSONAL_NAME_RE = re.compile(r"\b(?:bucar|steve|mcclurg)\b", re.IGNORECASE)
SERIAL_OPEN_RE = re.compile(r"\bserial\.Serial\b")
DEADBOLT_TOKEN_RE = re.compile(r"(?:Flash\.frm|/dev/ttyUSB)", re.IGNORECASE)


def _run_git(*args: str) -> subprocess.CompletedProcess[str]:
    """Execute a read-only git command from the repository root."""
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _iter_scan_files(*rel_dirs: str) -> list[Path]:
    """Collect text files across target repository directories deterministically."""
    files: list[Path] = []
    for rel_dir in rel_dirs:
        base = REPO_ROOT / rel_dir
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if path.suffix.lower() in SCAN_EXTENSIONS:
                files.append(path)
    return files


@pytest.mark.parametrize("rel_path", MUST_IGNORE_PATHS)
def test_gitignore_blocks_sensitive_paths(rel_path: str) -> None:
    """Verify .gitignore rejects private corpora, raw vault files, and ECU binaries."""
    proc = _run_git("check-ignore", "-v", "--no-index", rel_path)
    assert proc.returncode == 0, f"Expected {rel_path} to be ignored, stderr={proc.stderr!r}"
    rule = proc.stdout.strip().split("\t", 1)[0].split(":", 2)[2]
    assert not rule.startswith("!"), f"Expected ignore rule for {rel_path}, got negation {rule!r}"


@pytest.mark.parametrize("rel_path", MUST_NOT_IGNORE_PATHS)
def test_gitignore_preserves_required_placeholders(rel_path: str) -> None:
    """Verify .gitignore keeps tracked .gitkeep and README.md files unignored."""
    proc = _run_git("check-ignore", "-v", "--no-index", rel_path)
    if proc.returncode == 0:
        rule = proc.stdout.strip().split("\t", 1)[0].split(":", 2)[2]
        assert rule.startswith("!"), f"Expected {rel_path} not to be ignored, matched {rule!r}"
    else:
        assert proc.returncode == 1


def test_git_index_has_no_forbidden_tracked_artifacts() -> None:
    """Verify git ls-files contains zero private corpora, raw vault outputs, or binaries."""
    proc = _run_git("ls-files")
    assert proc.returncode == 0, proc.stderr
    tracked = [line for line in proc.stdout.splitlines() if line]
    violations: list[str] = []

    for path in tracked:
        if path == "data/heritage_raw/.gitkeep":
            continue
        if path.startswith(FORBIDDEN_INDEX_PREFIXES):
            violations.append(f"forbidden prefix: {path}")
        elif path.lower().endswith(FORBIDDEN_INDEX_SUFFIXES):
            violations.append(f"forbidden extension: {path}")

    assert not violations, "Forbidden files tracked in git index:\n" + "\n".join(violations)


def test_cleanroom_directories_free_of_pii() -> None:
    """Verify profiles, knowledge, wiki, and scripts contain no local paths, emails, or PII."""
    violations: list[str] = []
    for fpath in _iter_scan_files(*CLEANROOM_ONLY_DIRS):
        rel = fpath.relative_to(REPO_ROOT).as_posix()
        text = fpath.read_text(encoding="utf-8", errors="ignore")
        for label, pattern in (
            ("local POSIX user path", HOME_USER_PATH_RE),
            ("local Windows user path", WIN_USER_PATH_RE),
            ("email address", EMAIL_RE),
            ("personal name token", PERSONAL_NAME_RE),
        ):
            if pattern.search(text):
                violations.append(f"{rel}: matched {label}")

    assert not violations, "PII detected in cleanroom artifacts:\n" + "\n".join(violations)


def test_code_and_cleanroom_enforce_hardware_deadbolt() -> None:
    """Verify source, scripts, and cleanroom docs never expose serial writes or flash tools."""
    violations: list[str] = []
    blocklist_sources = frozenset({
        "src/logicaide/heritage/wiki_sync.py",
        "src/logicaide/heritage/discovery.py",
    })
    for fpath in _iter_scan_files(*CODE_DIRS, *CLEANROOM_ONLY_DIRS):
        rel = fpath.relative_to(REPO_ROOT).as_posix()
        text = fpath.read_text(encoding="utf-8", errors="ignore")
        if HOME_USER_PATH_RE.search(text):
            violations.append(f"{rel}: matched concrete /home/<user> path")
        if SERIAL_OPEN_RE.search(text):
            violations.append(f"{rel}: matched serial.Serial")
        if rel not in blocklist_sources and DEADBOLT_TOKEN_RE.search(text):
            violations.append(f"{rel}: matched Hardware Deadbolt token")

    assert not violations, "Hardware Deadbolt or path leak detected:\n" + "\n".join(violations)
