"""Outbound GitHub ``.wiki.git`` validation, staging, pruning, and mirror push."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, NamedTuple
from urllib.parse import urlparse, urlunparse
from urllib.request import url2pathname

from logicaide.heritage.wiki_sync import DEFAULT_GITHUB_WIKI_STAGE_DIR, WikiValidationError, _has_symlink_under, sync_wiki

_ALLOWED_URL_SCHEMES = frozenset({"https", "http", "ssh", "file"})
_HELPER_PROTOCOL_RE = re.compile(r"^[A-Za-z0-9+.-]+::")
_SCP_REMOTE_RE = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._-]*@[A-Za-z0-9._][A-Za-z0-9._-]*:[A-Za-z0-9._/-]+$")
_GH_SCP_RE = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._-]*@github\.com:([A-Za-z0-9_][A-Za-z0-9._-]*)/([A-Za-z0-9_][A-Za-z0-9._-]*?)(?:\.wiki\.git|\.git)?/?$", re.IGNORECASE)
_GH_SLUG_PART_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]*$")
_SYMREF_HEAD_RE = re.compile(r"^ref:\s+refs/heads/(\S+)\tHEAD$")
_CREDENTIAL_URL_RE = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s'\"`<>]+@")
_OS_SYSTEM_LINK_PREFIXES = frozenset({"/tmp", "/var", "/var/tmp"})
_TRANSPORT_AUTH_ERRORS = (
    "could not resolve host", "failed to connect", "connection refused", "timed out",
    "authentication failed", "invalid username or password", "permission denied", "returned error: 401", "returned error: 5",
)
_UNINITIALIZED_MARKERS = ("repository not found", "does not appear to be a git repository", "wiki is disabled", "returned error: 404")
_UNINITIALIZED_REPO_NOT_FOUND_RE = re.compile(r"\brepository\s+[^\r\n]+\bnot found\b")
_COMMIT_MSG = "docs(wiki): mirror verified LogicAIDE Living Heritage Wiki"


class _RemoteProbe(NamedTuple):
    """Result of probing a ``.wiki.git`` remote via ``git ls-remote``."""

    reachable: bool
    branch: str
    has_commits: bool
    head_before: str | None
    action_required: str | None


def _redact_credentials(text: str) -> str:
    """Redact userinfo credentials from any URL embedded in text."""
    return _CREDENTIAL_URL_RE.sub(r"\1<REDACTED>@", text)


def _sanitize_remote_for_display(remote_url: str) -> str:
    """Strip userinfo credentials from http/https/ssh URLs for safe display and errors."""
    if "://" not in remote_url:
        return remote_url
    parsed = urlparse(remote_url)
    if parsed.scheme.lower() in {"https", "http", "ssh"} and "@" in parsed.netloc:
        hostport = parsed.netloc.rsplit("@", 1)[-1]
        if hostport:
            return urlunparse(parsed._replace(netloc=hostport))
    return _redact_credentials(remote_url)


def _git(cwd: Path | None, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """Run a non-interactive git command with terminal prompts disabled."""
    cmd = ["git", *(["-C", str(cwd)] if cwd is not None else []), *args]
    try:
        return subprocess.run(
            cmd, env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"},
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        safe_cmd = _redact_credentials(" ".join(cmd[:4]))
        raise WikiValidationError(f"Git command failed ({safe_cmd}): {_redact_credentials(str(exc))}") from exc


def _git_ok(cwd: Path | None, *args: str, err_prefix: str, timeout: int = 30) -> str:
    """Run git command and return stripped stdout, raising WikiValidationError on failure."""
    proc = _git(cwd, *args, timeout=timeout)
    if proc.returncode != 0:
        detail = _redact_credentials(proc.stderr.strip() or proc.stdout.strip() or f"exit code {proc.returncode}")
        raise WikiValidationError(f"{_redact_credentials(err_prefix)}: {detail}")
    return proc.stdout.strip()


def _reject_symlink_components(raw_path: Path, base: Path | None = None) -> None:
    """Reject any symlink component along an uncollapsed absolute or relative path."""
    if raw_path.is_absolute():
        cur, parts = Path(raw_path.anchor), raw_path.parts[1:]
    else:
        eff_base = base if base is not None else Path.cwd()
        if base is not None:
            _reject_symlink_components(eff_base)
        cur = eff_base if eff_base.is_absolute() else (Path.cwd() / eff_base)
        parts = raw_path.parts
    if str(cur) not in _OS_SYSTEM_LINK_PREFIXES and cur.is_symlink():
        raise WikiValidationError(f"Path must not traverse or target a symlink: {raw_path}")
    for part in parts:
        if part in ("", "."):
            continue
        cur = cur.parent if part == ".." else (cur / part)
        if str(cur) not in _OS_SYSTEM_LINK_PREFIXES and cur.is_symlink():
            raise WikiValidationError(f"Path must not traverse or target a symlink: {raw_path}")


def validate_wiki_remote(raw_remote: str, *, base_dir: Path | None = None) -> str:
    """Validate a git remote URL, SCP address, or local path against injection vectors."""
    if not isinstance(raw_remote, str) or any(ch in raw_remote for ch in "\x00\n\r\t"):
        raise WikiValidationError("Wiki remote must be a string without NUL bytes, newlines, or tabs")
    remote = raw_remote.strip()
    if not remote or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in remote):
        raise WikiValidationError("Wiki remote must be non-empty without embedded spaces or control chars")
    safe_disp = _sanitize_remote_for_display(remote)
    if remote.startswith("-") or _HELPER_PROTOCOL_RE.match(remote):
        raise WikiValidationError(f"Unsafe or unsupported wiki remote: {safe_disp!r}")
    if "://" in remote:
        parsed = urlparse(remote)
        scheme = parsed.scheme.lower()
        if scheme not in _ALLOWED_URL_SCHEMES:
            raise WikiValidationError(f"Unsupported wiki remote URL scheme {scheme!r}: {safe_disp}")
        if scheme == "file":
            local_path = Path(url2pathname(parsed.path))
            if parsed.netloc not in ("", "localhost") or not parsed.path or not local_path.is_absolute():
                raise WikiValidationError(f"Invalid file:// wiki remote URL: {safe_disp}")
            _reject_symlink_components(local_path)
            return remote
        try:
            user, host, _ = parsed.username, parsed.hostname, parsed.port
        except ValueError as exc:
            raise WikiValidationError(f"Invalid port in wiki remote URL: {safe_disp}") from exc
        if not parsed.netloc or not host or not parsed.path.strip("/"):
            raise WikiValidationError(f"Invalid {scheme}:// wiki remote URL: {safe_disp}")
        if (user or "").startswith("-") or host.startswith("-"):
            raise WikiValidationError(f"Unsafe {scheme}:// user or host in wiki remote URL: {safe_disp}")
        return remote
    if ":" in remote:
        if not _SCP_REMOTE_RE.fullmatch(remote):
            raise WikiValidationError(f"Invalid SCP-style wiki remote: {remote}")
        return remote
    if "@" in remote:
        raise WikiValidationError(f"Invalid wiki remote path: {remote}")
    raw_local = Path(remote)
    _reject_symlink_components(raw_local, base=base_dir)
    candidate = raw_local if raw_local.is_absolute() else ((base_dir if base_dir is not None else Path.cwd()) / raw_local)
    return str(candidate.resolve())


def _normalize_origin_to_wiki_remote(raw_url: str, *, base_dir: Path | None = None) -> str:
    """Convert an origin remote string to its validated ``.wiki.git`` counterpart."""
    if "\x00" in raw_url:
        raise WikiValidationError("Origin remote must not contain NUL bytes")
    stripped = raw_url.strip()
    cleaned = stripped if stripped == "/" else stripped.rstrip("/")
    if not cleaned or "\n" in cleaned or "\r" in cleaned:
        raise WikiValidationError("Origin remote URL is empty or contains newlines")
    if "://" not in cleaned and ":" not in cleaned and not _HELPER_PROTOCOL_RE.match(cleaned):
        cleaned = validate_wiki_remote(cleaned, base_dir=base_dir)
    suffix_url = cleaned if cleaned.endswith(".wiki.git") else (f"{cleaned[:-4]}.wiki.git" if cleaned.endswith(".git") else f"{cleaned}.wiki.git")
    return validate_wiki_remote(suffix_url, base_dir=base_dir)


def derive_github_wiki_remote(repo_root: str | Path = ".") -> str:
    """Derive and validate a ``.wiki.git`` remote from a repo's ``origin`` or an origin URL string."""
    if isinstance(repo_root, str):
        if not (stripped := repo_root.strip()):
            raise WikiValidationError("Repository root or origin URL must not be empty")
        if "://" in stripped or _SCP_REMOTE_RE.fullmatch(stripped) or _HELPER_PROTOCOL_RE.match(stripped):
            return _normalize_origin_to_wiki_remote(stripped)
    resolved_root = Path(repo_root).resolve()
    if not resolved_root.is_dir():
        raise WikiValidationError(f"Repository root directory does not exist: {repo_root}")
    origin = _git_ok(resolved_root, "remote", "get-url", "origin", err_prefix=f"Failed to read origin remote in {resolved_root}", timeout=15)
    return _normalize_origin_to_wiki_remote(origin, base_dir=resolved_root)


def _extract_github_owner_repo(remote_url: str) -> str | None:
    """Return ``owner/repo`` for github.com remotes, or None for other remotes."""
    if scp_match := _GH_SCP_RE.fullmatch(remote_url):
        return f"{scp_match.group(1)}/{scp_match.group(2)}"
    if "://" not in remote_url or ((parsed := urlparse(remote_url)).hostname or "").lower() != "github.com":
        return None
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], re.sub(r"(?:\.wiki\.git|\.git)$", "", parts[1])
    return f"{owner}/{repo}" if _GH_SLUG_PART_RE.fullmatch(owner) and _GH_SLUG_PART_RE.fullmatch(repo) else None


def _build_action_required(remote_url: str, display_remote: str, lower_err: str) -> str:
    """Inspect repo wiki state via ``gh`` (fail-open) and return setup guidance."""
    owner_repo = _extract_github_owner_repo(remote_url)
    gh_info: dict[str, Any] | None = None
    if owner_repo:
        try:
            proc = subprocess.run(
                ["gh", "repo", "view", owner_repo, "--json", "hasWikiEnabled,isPrivate"],
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"},
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10, check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip() and isinstance(data := json.loads(proc.stdout), dict):
                gh_info = data
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, ValueError):
            gh_info = None

    wiki_disabled = ("wiki is disabled" in lower_err) or (gh_info is not None and gh_info.get("hasWikiEnabled") is False)
    wiki_tab = f"https://github.com/{owner_repo}/wiki" if owner_repo else "the repository's GitHub Wiki tab"
    action_tail = f'open {wiki_tab} and click "Create the first page" once to initialize .wiki.git, then re-run --push-github-wiki.'
    if wiki_disabled:
        msg = f"GitHub Wiki is disabled for {owner_repo or display_remote}. Enable Wikis under Settings -> Features -> Wikis, {action_tail}"
    elif owner_repo and gh_info is not None and gh_info.get("hasWikiEnabled") is True:
        msg = f"GitHub Wiki is enabled for {owner_repo} but .wiki.git is not initialized yet. {action_tail.capitalize()}"
    else:
        msg = f"Wiki remote {display_remote} is uninitialized or not yet created. Ensure Wikis are enabled under Settings -> Features -> Wikis, {action_tail}"
    return _redact_credentials(msg)


def _probe_wiki_remote(remote_url: str, display_remote: str) -> _RemoteProbe:
    """Probe the remote wiki repository via a single ls-remote --symref invocation."""
    proc = _git(None, "ls-remote", "--symref", "--", remote_url, "HEAD", "refs/heads/*", timeout=30)
    if proc.returncode != 0:
        err_text = _redact_credentials((proc.stderr or proc.stdout or "").strip())
        lower_err = err_text.lower()
        is_403_auth = "returned error: 403" in lower_err and "wiki is disabled" not in lower_err
        if is_403_auth or any(marker in lower_err for marker in _TRANSPORT_AUTH_ERRORS):
            raise WikiValidationError(f"Failed to reach wiki remote {display_remote!r}: {err_text}")
        if not (any(m in lower_err for m in _UNINITIALIZED_MARKERS) or _UNINITIALIZED_REPO_NOT_FOUND_RE.search(lower_err)):
            raise WikiValidationError(f"Wiki remote probe failed for {display_remote!r}: {err_text or proc.returncode}")
        return _RemoteProbe(False, "master", False, None, _build_action_required(remote_url, display_remote, lower_err))

    head_branch: str | None = None
    remote_heads: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if sym_match := _SYMREF_HEAD_RE.match(line.strip()):
            head_branch = sym_match.group(1)
        elif not line.startswith("ref:") and len(parts := line.split("\t", 1)) == 2 and parts[1].startswith("refs/heads/"):
            remote_heads[parts[1][len("refs/heads/") :]] = parts[0].strip()

    if head_branch is None and not remote_heads:
        parsed = urlparse(remote_url) if "://" in remote_url else None
        local_dir = Path(url2pathname(parsed.path)) if (parsed and parsed.scheme.lower() == "file") else (Path(remote_url) if ("://" not in remote_url and ":" not in remote_url) else None)
        if local_dir is not None and local_dir.is_dir():
            sym = _git(local_dir, "symbolic-ref", "--short", "HEAD", timeout=10)
            if sym.returncode == 0 and sym.stdout.strip():
                head_branch = sym.stdout.strip()
    branch = head_branch or ("master" if "master" in remote_heads else ("main" if "main" in remote_heads else (next(iter(remote_heads)) if remote_heads else "master")))
    return _RemoteProbe(True, branch, branch in remote_heads, remote_heads.get(branch), None)


def _assert_valid_git_entry(stage_dir: Path) -> None:
    """Reject symlinked or non-directory .git pointers (e.g. worktree gitdir files)."""
    git_entry = stage_dir / ".git"
    if git_entry.is_symlink() or (git_entry.exists() and not git_entry.is_dir()):
        raise WikiValidationError(f"Stage .git entry must be a directory, not symlink or file: {git_entry}")


def _find_git_toplevel(start_dir: Path, resolved_stage: Path) -> Path | None:
    """Walk upward from start_dir to nearest existing dir and return its git toplevel."""
    cand = start_dir
    while not (cand.exists() and cand.is_dir()):
        if cand == cand.parent:
            return None
        cand = cand.parent
    proc = _git(cand, "rev-parse", "--show-toplevel", timeout=15)
    if proc.returncode == 0 and proc.stdout.strip():
        return Path(proc.stdout.strip()).resolve()
    bare_proc = _git(cand, "rev-parse", "--is-inside-git-dir", timeout=15)
    if bare_proc.returncode == 0 and bare_proc.stdout.strip() == "true" and resolved_stage.is_relative_to(cand):
        raise WikiValidationError(f"Refusing to stage wiki mirror inside git repository directory: {resolved_stage}")
    return None


def _check_stage_gitignored(resolved_stage: Path, toplevel: Path) -> None:
    """Reject staging at repo root, inside .git, or at an un-ignored path inside toplevel."""
    if not resolved_stage.is_relative_to(toplevel):
        return
    if resolved_stage == toplevel or ".git" in resolved_stage.relative_to(toplevel).parts:
        raise WikiValidationError(f"Refusing to stage wiki mirror at repo root or inside .git: {resolved_stage}")
    stage_str = str(resolved_stage)
    if not any(_git(toplevel, "check-ignore", "-q", "--", p, timeout=15).returncode == 0 for p in (stage_str, f"{stage_str}/")):
        raise WikiValidationError(f"Stage directory inside git repo must be gitignored: {resolved_stage}")


def _is_known_wiki_mirror(resolved_stage: Path, expected_remote: str | None) -> bool:
    """Return True only when an existing .git stage dir looks like an initialized wiki mirror."""
    origin_proc = _git(resolved_stage, "remote", "get-url", "origin", timeout=10)
    if origin_proc.returncode == 0:
        origin = origin_proc.stdout.strip()
        if origin.endswith(".wiki.git") or (expected_remote is not None and _sanitize_remote_for_display(origin) == _sanitize_remote_for_display(expected_remote)):
            return True
    log_proc = _git(resolved_stage, "log", "-1", "--format=%s", timeout=10)
    return log_proc.returncode == 0 and log_proc.stdout.strip() == _COMMIT_MSG


def _resolve_and_guard_stage(target_dir: str | Path | None, effective_root: Path, resolved_wiki: Path, expected_remote: str | None = None) -> Path:
    """Resolve the mirror staging directory and enforce symlink, worktree, overlap, and .gitignore guards."""
    stage_path = Path(target_dir) if target_dir is not None else (effective_root / DEFAULT_GITHUB_WIKI_STAGE_DIR)
    _reject_symlink_components(stage_path)
    _assert_valid_git_entry(stage_path)
    resolved_stage = stage_path.resolve()
    if ".git" in stage_path.parts or ".git" in resolved_stage.parts or not resolved_stage.name or resolved_stage == resolved_stage.parent:
        raise WikiValidationError(f"Invalid stage directory target: {resolved_stage}")
    _assert_valid_git_entry(resolved_stage)
    if resolved_stage.exists() and not resolved_stage.is_dir():
        raise WikiValidationError(f"Invalid stage directory target: {resolved_stage}")
    if resolved_stage == resolved_wiki or resolved_stage.is_relative_to(resolved_wiki) or resolved_wiki.is_relative_to(resolved_stage):
        raise WikiValidationError(f"Refusing to stage wiki mirror inside source wiki directory: {resolved_stage}")
    has_stage_git = (resolved_stage / ".git").is_dir()
    probe_bases = (resolved_stage.parent if has_stage_git else resolved_stage, effective_root)
    for toplevel in {t for b in probe_bases if (t := _find_git_toplevel(b, resolved_stage)) is not None}:
        _check_stage_gitignored(resolved_stage, toplevel)
    if has_stage_git and not _is_known_wiki_mirror(resolved_stage, expected_remote):
        raise WikiValidationError(f"Refusing to stage wiki mirror in existing unrelated git repository: {resolved_stage}")
    return resolved_stage


@contextmanager
def _stage_lock(resolved_stage: Path) -> Iterator[None]:
    """Acquire an exclusive advisory lockfile beside resolved_stage without following symlinks."""
    resolved_stage.parent.mkdir(parents=True, exist_ok=True)
    lock_path = resolved_stage.parent / f".{resolved_stage.name}.lock"
    if lock_path.is_symlink():
        raise WikiValidationError(f"Stage lock path must not be a symlink: {lock_path}")
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError as exc:
        raise WikiValidationError(f"Failed to open stage lock {lock_path}: {exc}") from exc
    with os.fdopen(fd, "r+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _iter_stale_stage_md(resolved_stage: Path, resolved_wiki: Path) -> Iterator[tuple[Path, str]]:
    """Yield safe stale markdown paths under resolved_stage outside .git/."""
    expected_names = {p.name for p in resolved_wiki.rglob("*.md") if p.is_file()}
    for stale in sorted(resolved_stage.rglob("*.md")):
        rel = stale.relative_to(resolved_stage)
        if ".git" in rel.parts or (len(rel.parts) == 1 and stale.name in expected_names):
            continue
        if stale.is_symlink() or _has_symlink_under(resolved_stage, stale):
            raise WikiValidationError(f"Refusing to prune symlink under stage directory: {stale}")
        try:
            if not stale.resolve().is_relative_to(resolved_stage):
                raise WikiValidationError(f"Refusing to prune out-of-tree path: {stale}")
        except OSError as exc:
            raise WikiValidationError(f"Refusing to prune out-of-tree path: {stale}") from exc
        yield stale, rel.as_posix()


def _count_untracked_stale_md(resolved_stage: Path, resolved_wiki: Path, *, include_fetch_head: bool) -> int:
    """Count untracked stale markdown pages before git clean -fd without double-counting remote files."""
    tracked = {p for p in _git(resolved_stage, "ls-files", "-z").stdout.split("\0") if p}
    if include_fetch_head:
        tracked |= {p for p in _git(resolved_stage, "ls-tree", "-r", "--name-only", "-z", "FETCH_HEAD").stdout.split("\0") if p}
    return sum(1 for _, rel_posix in _iter_stale_stage_md(resolved_stage, resolved_wiki) if rel_posix not in tracked)


def _prepare_stage_repo(resolved_stage: Path, remote_url: str, probe: _RemoteProbe, resolved_wiki: Path | None = None) -> int:
    """Initialize/align stage git repo, clean untracked files, and return pre-cleaned stale md count."""
    resolved_stage.mkdir(parents=True, exist_ok=True)
    _assert_valid_git_entry(resolved_stage)
    if not (resolved_stage / ".git").exists():
        _git_ok(resolved_stage, "init", "-b", probe.branch, err_prefix=f"Failed to init stage repo in {resolved_stage}")
    has_origin = _git(resolved_stage, "remote", "get-url", "origin").returncode == 0
    remote_cmd = ("remote", "set-url", "origin", "--", remote_url) if has_origin else ("remote", "add", "origin", "--", remote_url)
    _git_ok(resolved_stage, *remote_cmd, err_prefix=f"Failed to set origin remote in {resolved_stage}")
    align_remote = probe.reachable and probe.has_commits
    if align_remote:
        _git_ok(resolved_stage, "fetch", "--depth=1", "origin", "--", f"refs/heads/{probe.branch}", err_prefix=f"Failed to fetch branch {probe.branch!r}")
    pre_cleaned = _count_untracked_stale_md(resolved_stage, resolved_wiki, include_fetch_head=align_remote) if resolved_wiki else 0
    _git_ok(resolved_stage, "clean", "-fd", err_prefix=f"Failed to clean untracked files in {resolved_stage}")
    checkout_args = ("checkout", "-f", "-B", probe.branch, "FETCH_HEAD") if align_remote else ("checkout", "-B", probe.branch)
    _git_ok(resolved_stage, *checkout_args, err_prefix=f"Failed to align branch {probe.branch!r}")
    return pre_cleaned


def _prune_stale_pages(resolved_stage: Path, resolved_wiki: Path) -> int:
    """Remove stale markdown files outside .git/ and prune empty subdirectories."""
    pruned_files = 0
    for stale, _ in _iter_stale_stage_md(resolved_stage, resolved_wiki):
        if stale.is_file():
            stale.unlink()
            pruned_files += 1
    for directory in sorted((d for d in resolved_stage.rglob("*") if d.is_dir() and not d.is_symlink() and ".git" not in d.relative_to(resolved_stage).parts), key=lambda p: len(p.parts), reverse=True):
        if not any(directory.iterdir()):
            directory.rmdir()
    return pruned_files


def _commit_stage_if_dirty(resolved_stage: Path) -> tuple[bool, str]:
    """Stage all mirror changes, commit when non-empty, and return (commit_created, head_sha)."""
    _git_ok(resolved_stage, "add", "-A", err_prefix=f"git add failed in {resolved_stage}")
    status_out = _git_ok(resolved_stage, "status", "--porcelain", err_prefix=f"git status failed in {resolved_stage}")
    commit_created = False
    if status_out:
        cfg_args: list[str] = []
        for key, fallback in (("user.name", "LogicAIDE Wiki Bot"), ("user.email", "noreply@logicaide.local")):
            proc = _git(resolved_stage, "config", "--get", key)
            if proc.returncode != 0 or not proc.stdout.strip():
                cfg_args.extend(["-c", f"{key}={fallback}"])
        _git_ok(resolved_stage, *cfg_args, "commit", "-m", _COMMIT_MSG, err_prefix=f"git commit failed in {resolved_stage}")
        commit_created = True
    return commit_created, _git_ok(resolved_stage, "rev-parse", "--verify", "HEAD", err_prefix=f"Failed to resolve HEAD in {resolved_stage}")


def push_github_wiki_mirror(
    wiki_dir: str | Path, target_dir: str | Path | None = None, *, dry_run: bool = False,
    strip_frontmatter: bool = True, flatten_for_github_wiki: bool = True,
    use_external_linter: bool = False, wiki_remote: str | None = None, repo_root: str | Path | None = None,
) -> dict[str, Any]:
    """Validate, flatten, stage, prune, commit, and mirror wiki pages to GitHub ``.wiki.git``."""
    if not strip_frontmatter or not flatten_for_github_wiki:
        raise WikiValidationError("GitHub .wiki.git mirroring requires strip_frontmatter=True and flatten_for_github_wiki=True")

    resolved_wiki = Path(wiki_dir).resolve()
    effective_root = Path(repo_root).resolve() if repo_root else Path.cwd().resolve()
    remote_url = validate_wiki_remote(wiki_remote) if wiki_remote is not None else derive_github_wiki_remote(effective_root)
    display_remote = _sanitize_remote_for_display(remote_url)
    resolved_stage = _resolve_and_guard_stage(target_dir, effective_root, resolved_wiki, expected_remote=remote_url)
    summary = sync_wiki(wiki_dir, target_dir=resolved_stage, dry_run=True, strip_frontmatter=True, flatten_for_github_wiki=True, use_external_linter=use_external_linter)
    if dry_run:
        return {
            **summary, "validate_only": False, "synced_files": 0, "pruned_files": 0, "dry_run": True,
            "target_dir": str(resolved_stage), "push_github_wiki": True, "wiki_remote": display_remote, "wiki_branch": "master",
            "github_wiki_status": "dry_run", "pushed": False, "pushed_commit": None, "staged_commit": None, "commit_created": False, "action_required": None,
        }

    with _stage_lock(resolved_stage):
        probe = _probe_wiki_remote(remote_url, display_remote)
        pre_cleaned = _prepare_stage_repo(resolved_stage, remote_url, probe, resolved_wiki=resolved_wiki)
        sync_res = sync_wiki(wiki_dir, target_dir=resolved_stage, dry_run=False, strip_frontmatter=True, flatten_for_github_wiki=True, use_external_linter=False)
        pruned_files = pre_cleaned + _prune_stale_pages(resolved_stage, resolved_wiki)
        commit_created, head_sha = _commit_stage_if_dirty(resolved_stage)
        result = {
            **summary, "validate_only": False, "synced_files": sync_res["synced_files"], "pruned_files": pruned_files,
            "dry_run": False, "target_dir": str(resolved_stage), "push_github_wiki": True, "wiki_remote": display_remote,
            "wiki_branch": probe.branch, "staged_commit": head_sha, "commit_created": commit_created,
        }
        if probe.reachable:
            _git_ok(resolved_stage, "push", "origin", f"HEAD:refs/heads/{probe.branch}", err_prefix=f"Failed to push GitHub Wiki mirror to {display_remote}")
            status = "up_to_date" if (not commit_created and probe.head_before == head_sha) else "pushed"
            return {**result, "github_wiki_status": status, "pushed": True, "pushed_commit": head_sha, "action_required": None}
        return {**result, "github_wiki_status": "remote_uninitialized", "pushed": False, "pushed_commit": None, "action_required": probe.action_required}
