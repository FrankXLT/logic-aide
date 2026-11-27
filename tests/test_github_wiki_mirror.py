"""Tests for LogicAIDE GitHub .wiki.git mirror sync and --push-github-wiki CLI."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess

from click.testing import CliRunner
import pytest

from logicaide.cli import main as cli_main
from logicaide.heritage import github_wiki
from logicaide.heritage.github_wiki import (
    _prune_stale_pages,
    derive_github_wiki_remote,
    push_github_wiki_mirror,
    validate_wiki_remote,
)
from logicaide.heritage.wiki_sync import WikiValidationError, run_sync_wiki, sync_wiki


def _frontmatter(title: str = "Sample", slug: str = "sample", category: str = "turbo-and-airflow") -> str:
    return (
        f'---\ntitle: "{title}"\nslug: "{slug}"\ncategory: "{category}"\n'
        'tier: "Tier 2"\nprovenance: "Cleanroom verification"\n'
        'ecu_scope: ["SMEC_88_89"]\nengine_scope: ["2.2L_K"]\n'
        'status: "verified"\nlast_verified: "2026-11-26"\ntags: ["wave-a"]\n---\n\n'
    )


def _build_sample_wiki(root: Path) -> Path:
    wiki = root / "wiki"
    (wiki / "topic").mkdir(parents=True)
    (wiki / "_Sidebar.md").write_text("- [Home](Home.md)\n- [Guide](topic/guide.md)\n", encoding="utf-8")
    (wiki / "_Footer.md").write_text("Cleanroom Heritage Footer\n", encoding="utf-8")
    (wiki / "Home.md").write_text(
        _frontmatter("Home", "home", "portal") + "# Home\n\nSee [Guide](topic/guide.md#pinout).\n",
        encoding="utf-8",
    )
    (wiki / "topic" / "guide.md").write_text(
        _frontmatter("Guide", "guide")
        + "## Pinout\n\nBack to [Home](../Home.md).\n\n"
        + '```mermaid\nflowchart LR\n    A["Pin 22"] -->|"0.5V"| B["SMEC"]\n```\n',
        encoding="utf-8",
    )
    return wiki


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def _init_bare_wiki_remote(path: Path) -> Path:
    subprocess.run(["git", "init", "--bare", "-b", "master", str(path)], capture_output=True, text=True, check=True)
    return path


def test_derive_and_validate_wiki_remote_accepts_expected_urls(tmp_path: Path) -> None:
    assert derive_github_wiki_remote("https://github.com/org/repo.git") == "https://github.com/org/repo.wiki.git"
    assert derive_github_wiki_remote("https://github.com/org/repo/") == "https://github.com/org/repo.wiki.git"
    assert derive_github_wiki_remote("https://github.com/org/repo") == "https://github.com/org/repo.wiki.git"
    assert derive_github_wiki_remote("git@github.com:org/repo.git") == "git@github.com:org/repo.wiki.git"
    assert derive_github_wiki_remote("https://github.com/org/repo.wiki.git") == "https://github.com/org/repo.wiki.git"
    assert derive_github_wiki_remote("git@github.com:org/repo.wiki.git") == "git@github.com:org/repo.wiki.git"

    for valid in (
        "https://github.com/org/repo.wiki.git",
        "git@github.com:org/repo.wiki.git",
        str(tmp_path / "local_bare.wiki.git"),
    ):
        assert validate_wiki_remote(valid) == valid


@pytest.mark.parametrize(
    "bad_remote",
    [
        "",
        "   ",
        "-oUploadPack=evil",
        "--upload-pack=evil",
        "ext::sh -c id",
        "https://github.com/org/repo.wiki.git\n-oEvil",
        "https://github.com/org/repo.wiki.git\x00evil",
    ],
)
def test_validate_wiki_remote_rejects_injection_and_invalid_inputs(bad_remote: str) -> None:
    with pytest.raises(WikiValidationError):
        validate_wiki_remote(bad_remote)


def test_push_github_wiki_bare_repo_push_incremental_and_prunes_stale_pages(tmp_path: Path) -> None:
    wiki = _build_sample_wiki(tmp_path)
    wiki_bare = _init_bare_wiki_remote(tmp_path / "wiki_bare.git")
    mirror = tmp_path / "mirror"

    res1 = run_sync_wiki(
        wiki, target_dir=mirror, push_github_wiki=True, wiki_remote=str(wiki_bare), repo_root=tmp_path
    )
    assert res1["pushed"] is True
    assert res1["github_wiki_status"] == "pushed"
    assert res1["synced_files"] == 4 and res1["pruned_files"] == 0
    assert res1["commit_created"] is True
    expected_pages = {"Home.md", "guide.md", "_Sidebar.md", "_Footer.md"}
    assert set(_git(wiki_bare, "ls-tree", "-r", "--name-only", "master").splitlines()) == expected_pages
    assert not _git(wiki_bare, "show", "master:Home.md").startswith("---")
    assert not _git(wiki_bare, "show", "master:guide.md").startswith("---")

    res2 = run_sync_wiki(
        wiki, target_dir=mirror, push_github_wiki=True, wiki_remote=str(wiki_bare), repo_root=tmp_path
    )
    assert res2["pushed"] is True
    assert res2["github_wiki_status"] == "up_to_date"
    assert res2["commit_created"] is False

    (wiki / "topic" / "guide.md").unlink()
    (wiki / "_Sidebar.md").write_text("- [Home](Home.md)\n", encoding="utf-8")
    (wiki / "Home.md").write_text(
        _frontmatter(title="Home", slug="home", category="portal") + "# Home\n\nUpdated portal.\n",
        encoding="utf-8",
    )

    res3 = run_sync_wiki(
        wiki, target_dir=mirror, push_github_wiki=True, wiki_remote=str(wiki_bare), repo_root=tmp_path
    )
    assert res3["pushed"] is True
    assert res3["github_wiki_status"] == "pushed"
    assert res3["pruned_files"] == 1
    tree3 = set(_git(wiki_bare, "ls-tree", "-r", "--name-only", "master").splitlines())
    assert tree3 == {"Home.md", "_Sidebar.md", "_Footer.md"}
    assert "guide.md" not in tree3


def test_push_github_wiki_aligns_with_preexisting_remote_commit(tmp_path: Path) -> None:
    wiki = _build_sample_wiki(tmp_path)
    wiki_bare = _init_bare_wiki_remote(tmp_path / "wiki_bare.git")

    seed_clone = tmp_path / "seed_clone"
    _git(tmp_path, "clone", str(wiki_bare), str(seed_clone))
    _git(seed_clone, "checkout", "-B", "master")
    _git(seed_clone, "config", "user.name", "LogicAIDE Test")
    _git(seed_clone, "config", "user.email", "logicaide-test@localhost")
    (seed_clone / "Home.md").write_text("Initial GitHub Wiki placeholder page.\n", encoding="utf-8")
    _git(seed_clone, "add", "Home.md")
    _git(seed_clone, "commit", "-m", "Create the first page")
    _git(seed_clone, "push", "origin", "master")

    mirror = tmp_path / "mirror"
    uninit_res = run_sync_wiki(
        wiki, target_dir=mirror, push_github_wiki=True, wiki_remote=str(tmp_path / "missing.wiki.git"), repo_root=tmp_path
    )
    assert uninit_res["github_wiki_status"] == "remote_uninitialized"

    aligned = run_sync_wiki(
        wiki, target_dir=mirror, push_github_wiki=True, wiki_remote=str(wiki_bare), repo_root=tmp_path
    )
    assert aligned["pushed"] is True
    assert aligned["github_wiki_status"] == "pushed"
    assert set(_git(wiki_bare, "ls-tree", "-r", "--name-only", "master").splitlines()) == {
        "Home.md", "guide.md", "_Sidebar.md", "_Footer.md",
    }
    assert "# Home" in _git(wiki_bare, "show", "master:Home.md")


def test_push_github_wiki_uninitialized_remote_returns_clean_status(tmp_path: Path) -> None:
    wiki = _build_sample_wiki(tmp_path)
    mirror = tmp_path / "mirror"
    missing_remote = tmp_path / "missing.wiki.git"

    res = run_sync_wiki(
        wiki, target_dir=mirror, push_github_wiki=True, wiki_remote=str(missing_remote), repo_root=tmp_path
    )
    assert res["pushed"] is False
    assert res["github_wiki_status"] == "remote_uninitialized"
    assert res["synced_files"] == 4
    staged_commit = str(res["staged_commit"])
    assert len(staged_commit) >= 7
    assert _git(mirror, "rev-parse", "--verify", staged_commit) == staged_commit
    assert "Create the first page" in str(res["action_required"])


def test_cli_sync_wiki_push_github_wiki_dry_run_validate_only_and_bare_push(tmp_path: Path) -> None:
    wiki = _build_sample_wiki(tmp_path)
    wiki_bare = _init_bare_wiki_remote(tmp_path / "wiki_bare.git")
    mirror = tmp_path / "cli_mirror"
    runner = CliRunner()

    dry_res = runner.invoke(
        cli_main,
        [
            "heritage", "sync-wiki", "--wiki-dir", str(wiki), "--target-dir", str(mirror),
            "--wiki-remote", str(wiki_bare), "--dry-run", "--push-github-wiki", "--json",
        ],
    )
    assert dry_res.exit_code == 0, dry_res.output
    dry_payload = json.loads(dry_res.output)
    assert dry_payload["dry_run"] is True and dry_payload["pushed"] is False and dry_payload["synced_files"] == 0
    assert not mirror.exists()
    assert _git(wiki_bare, "for-each-ref") == ""

    val_res = runner.invoke(
        cli_main, ["heritage", "sync-wiki", "--wiki-dir", str(wiki), "--validate-only", "--push-github-wiki"]
    )
    assert val_res.exit_code != 0

    push_res = runner.invoke(
        cli_main,
        [
            "heritage", "sync-wiki", "--wiki-dir", str(wiki), "--target-dir", str(mirror),
            "--wiki-remote", str(wiki_bare), "--push-github-wiki", "--json",
        ],
    )
    assert push_res.exit_code == 0, push_res.output
    push_payload = json.loads(push_res.output)
    assert push_payload["github_wiki_status"] == "pushed"
    assert push_payload["pushed"] is True
    assert push_payload["synced_files"] == 4


def test_origin_derivation_and_relative_local_remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _build_sample_wiki(tmp_path)
    gh_repo = tmp_path / "gh_repo"
    gh_repo.mkdir()
    _git(gh_repo, "init")
    _git(gh_repo, "remote", "add", "origin", "https://github.com/org/sample-repo.git")
    assert derive_github_wiki_remote(gh_repo) == "https://github.com/org/sample-repo.wiki.git"

    code_bare = _init_bare_wiki_remote(tmp_path / "sample.git")
    wiki_bare = _init_bare_wiki_remote(tmp_path / "sample.wiki.git")
    origin_repo = tmp_path / "origin_repo"
    origin_repo.mkdir()
    _git(origin_repo, "init")
    _git(origin_repo, "remote", "add", "origin", str(code_bare))
    res_origin = push_github_wiki_mirror(wiki, target_dir=tmp_path / "origin_mirror", wiki_remote=None, repo_root=origin_repo)
    assert res_origin["pushed"] is True
    assert res_origin["wiki_remote"] == str(wiki_bare)
    assert "Home.md" in _git(wiki_bare, "ls-tree", "-r", "--name-only", "master").splitlines()

    rel_bare = _init_bare_wiki_remote(tmp_path / "rel_wiki.git")
    caller_cwd = tmp_path / "caller_cwd"
    caller_cwd.mkdir()
    monkeypatch.chdir(caller_cwd)
    res_rel = push_github_wiki_mirror(wiki, target_dir=tmp_path / "deep" / "rel_mirror", wiki_remote="../rel_wiki.git", repo_root=tmp_path)
    assert res_rel["pushed"] is True
    assert "Home.md" in _git(rel_bare, "ls-tree", "-r", "--name-only", "master").splitlines()


def test_push_github_wiki_redacts_credentials_in_outputs_and_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _build_sample_wiki(tmp_path)
    secret_remote = "https://tok-user:tok-secret@127.0.0.1:1/org/repo.wiki.git"
    orig_git = github_wiki._git

    def _mock_uninit(cwd: Path | None, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        if args[:1] == ("ls-remote",):
            return subprocess.CompletedProcess(
                ["git", *args], 128, "", f"remote: Repository not found.\nfatal: repository '{secret_remote}/' not found"
            )
        return orig_git(cwd, *args, timeout=timeout)

    monkeypatch.setattr(github_wiki, "_git", _mock_uninit)
    res = push_github_wiki_mirror(wiki, target_dir=tmp_path / "m_uninit", wiki_remote=secret_remote, repo_root=tmp_path)
    assert res["github_wiki_status"] == "remote_uninitialized"
    assert "tok-user" not in str(res["wiki_remote"]) and "tok-secret" not in str(res["wiki_remote"])
    assert "tok-user" not in str(res.get("action_required") or "") and "tok-secret" not in str(res.get("action_required") or "")

    cli_res = CliRunner().invoke(
        cli_main,
        ["heritage", "sync-wiki", "--wiki-dir", str(wiki), "--target-dir", str(tmp_path / "m_cli"), "--wiki-remote", secret_remote, "--push-github-wiki", "--json"],
    )
    assert cli_res.exit_code == 0, cli_res.output
    assert "tok-user" not in cli_res.output and "tok-secret" not in cli_res.output
    cli_payload = json.loads(cli_res.output)
    assert "tok-user" not in str(cli_payload["wiki_remote"]) and "tok-secret" not in str(cli_payload["wiki_remote"])

    def _mock_conn_err(cwd: Path | None, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        if args[:1] == ("ls-remote",):
            return subprocess.CompletedProcess(["git", *args], 128, "", f"fatal: unable to access '{secret_remote}/': Failed to connect")
        return orig_git(cwd, *args, timeout=timeout)

    monkeypatch.setattr(github_wiki, "_git", _mock_conn_err)
    with pytest.raises(WikiValidationError) as exc_info:
        push_github_wiki_mirror(wiki, target_dir=tmp_path / "m_err", wiki_remote=secret_remote, repo_root=tmp_path)
    assert "tok-user" not in str(exc_info.value) and "tok-secret" not in str(exc_info.value)


def test_validate_wiki_remote_rejects_additional_injection_and_file_symlink(tmp_path: Path) -> None:
    real_bare = tmp_path / "real.wiki.git"
    real_bare.mkdir()
    sym_bare = tmp_path / "sym.wiki.git"
    sym_bare.symlink_to(real_bare)

    for bad in ("git@-oProxyCommand=x:a/b", "ssh://-oProxyCommand=x/a/b", "fd::sh", "git://host/a/b", sym_bare.as_uri()):
        with pytest.raises(WikiValidationError):
            validate_wiki_remote(bad)


def test_probe_error_classification_raises_on_auth_network_and_allows_wiki_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _build_sample_wiki(tmp_path)
    orig_git = github_wiki._git

    for err_msg in (
        "fatal: unable to access 'https://github.com/org/repo.wiki.git/': The requested URL returned error: 401",
        "fatal: unable to access 'https://github.com/org/repo.wiki.git/': The requested URL returned error: 403",
        "fatal: Authentication failed for 'https://github.com/org/repo.wiki.git/'",
        "git@github.com: Permission denied (publickey).",
        "fatal: Could not resolve host: github.com",
        "git-remote-https: not found",
    ):
        def _mock_fail(cwd: Path | None, *args: str, timeout: int = 30, _msg: str = err_msg) -> subprocess.CompletedProcess[str]:
            if args[:1] == ("ls-remote",):
                return subprocess.CompletedProcess(["git", *args], 128, "", _msg)
            return orig_git(cwd, *args, timeout=timeout)

        monkeypatch.setattr(github_wiki, "_git", _mock_fail)
        with pytest.raises(WikiValidationError):
            push_github_wiki_mirror(wiki, target_dir=tmp_path / "m_fail", wiki_remote="https://github.com/org/repo.wiki.git", repo_root=tmp_path)

    def _mock_disabled(cwd: Path | None, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        if args[:1] == ("ls-remote",):
            return subprocess.CompletedProcess(["git", *args], 128, "", "remote: wiki is disabled\nfatal: The requested URL returned error: 403")
        return orig_git(cwd, *args, timeout=timeout)

    monkeypatch.setattr(github_wiki, "_git", _mock_disabled)
    res = push_github_wiki_mirror(wiki, target_dir=tmp_path / "m_disabled", wiki_remote="https://github.com/org/repo.wiki.git", repo_root=tmp_path)
    assert res["github_wiki_status"] == "remote_uninitialized" and res["pushed"] is False


def test_push_github_wiki_main_branch_and_prepopulated_untracked_home(tmp_path: Path) -> None:
    wiki = _build_sample_wiki(tmp_path)
    main_bare = tmp_path / "main_bare.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(main_bare)], capture_output=True, text=True, check=True)
    res_main = run_sync_wiki(wiki, target_dir=tmp_path / "m_main", push_github_wiki=True, wiki_remote=str(main_bare), repo_root=tmp_path)
    assert res_main["pushed"] is True and res_main["wiki_branch"] == "main"
    assert "Home.md" in _git(main_bare, "ls-tree", "-r", "--name-only", "main").splitlines()

    seed_bare = _init_bare_wiki_remote(tmp_path / "seed_bare.git")
    seed_clone = tmp_path / "seed_clone"
    _git(tmp_path, "clone", str(seed_bare), str(seed_clone))
    _git(seed_clone, "checkout", "-B", "master")
    _git(seed_clone, "config", "user.name", "LogicAIDE Test")
    _git(seed_clone, "config", "user.email", "logicaide-test@localhost")
    (seed_clone / "Home.md").write_text("Seed placeholder.\n", encoding="utf-8")
    _git(seed_clone, "add", "Home.md")
    _git(seed_clone, "commit", "-m", "Seed Home")
    _git(seed_clone, "push", "origin", "master")

    stage_pre = tmp_path / "stage_pre"
    sync_wiki(wiki, target_dir=stage_pre, dry_run=False)
    assert (stage_pre / "Home.md").is_file() and not (stage_pre / ".git").exists()

    aligned = run_sync_wiki(wiki, target_dir=stage_pre, push_github_wiki=True, wiki_remote=str(seed_bare), repo_root=tmp_path)
    assert aligned["pushed"] is True and aligned["github_wiki_status"] == "pushed"
    assert "# Home" in _git(seed_bare, "show", "master:Home.md")


def test_stage_guards_reject_outside_repo_unsafe_targets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _build_sample_wiki(tmp_path)
    wiki_bare = _init_bare_wiki_remote(tmp_path / "wiki_bare.git")
    outside = tmp_path / "outside"
    outside.mkdir()
    other_repo = tmp_path / "other_repo"
    other_repo.mkdir()
    _git(other_repo, "init", "-b", "main")

    sym_git_stage = tmp_path / "sym_git_stage"
    sym_git_stage.mkdir()
    (sym_git_stage / ".git").symlink_to(tmp_path / "missing_git")

    file_git_stage = tmp_path / "file_git_stage"
    file_git_stage.mkdir()
    (file_git_stage / ".git").write_text("gitdir: nowhere\n", encoding="utf-8")

    real_stage = tmp_path / "real_stage"
    real_stage.mkdir()
    sym_stage = tmp_path / "sym_stage"
    sym_stage.symlink_to(real_stage)

    sym_sub = tmp_path / "sub" / "real_dir"
    sym_sub.mkdir(parents=True)
    sym_dir = tmp_path / "sym_dir"
    sym_dir.symlink_to(sym_sub)

    monkeypatch.chdir(outside)
    for bad_target in (
        other_repo / "unignored_stage",
        other_repo,
        other_repo / ".git" / "wiki_stage",
        sym_git_stage,
        file_git_stage,
        sym_stage,
        sym_dir / ".." / "escape",
    ):
        with pytest.raises(WikiValidationError):
            push_github_wiki_mirror(wiki, target_dir=bad_target, wiki_remote=str(wiki_bare))


def test_prune_stale_pages_nested_dirs_keeps_git_and_refuses_symlinks(tmp_path: Path) -> None:
    wiki = _build_sample_wiki(tmp_path)
    wiki_bare = _init_bare_wiki_remote(tmp_path / "wiki_bare.git")
    mirror = tmp_path / "mirror"
    push_github_wiki_mirror(wiki, target_dir=mirror, wiki_remote=str(wiki_bare), repo_root=tmp_path)

    (mirror / "stale" / "nested").mkdir(parents=True)
    (mirror / "stale" / "nested" / "old.md").write_text("stale\n", encoding="utf-8")
    (mirror / "empty_dir" / "inner").mkdir(parents=True)

    res = push_github_wiki_mirror(wiki, target_dir=mirror, wiki_remote=str(wiki_bare), repo_root=tmp_path)
    assert res["pruned_files"] == 1
    assert not (mirror / "stale").exists() and not (mirror / "empty_dir").exists()
    assert (mirror / ".git" / "HEAD").is_file()

    outside_md = tmp_path / "outside.md"
    outside_md.write_text("preserve\n", encoding="utf-8")
    (mirror / "bad_link.md").symlink_to(outside_md)
    with pytest.raises(WikiValidationError):
        _prune_stale_pages(mirror, wiki.resolve())
    assert outside_md.read_text(encoding="utf-8") == "preserve\n"


def test_cli_sync_wiki_guards_and_human_output(tmp_path: Path) -> None:
    wiki = _build_sample_wiki(tmp_path)
    wiki_bare = _init_bare_wiki_remote(tmp_path / "wiki_bare.git")
    mirror = tmp_path / "cli_mirror"
    runner = CliRunner()

    assert runner.invoke(cli_main, ["heritage", "sync-wiki", "--wiki-dir", str(wiki), "--wiki-remote", str(wiki_bare)]).exit_code != 0
    assert runner.invoke(cli_main, ["heritage", "sync-wiki", "--wiki-dir", str(wiki), "--validate-only", "--wiki-remote", str(wiki_bare)]).exit_code != 0

    for flag in ("--no-strip-frontmatter", "--no-flatten"):
        bad = runner.invoke(cli_main, ["heritage", "sync-wiki", "--wiki-dir", str(wiki), "--target-dir", str(mirror), "--wiki-remote", str(wiki_bare), "--push-github-wiki", flag])
        assert bad.exit_code != 0 and "Error:" in bad.output

    human = runner.invoke(cli_main, ["heritage", "sync-wiki", "--wiki-dir", str(wiki), "--target-dir", str(mirror), "--wiki-remote", str(wiki_bare), "--push-github-wiki"])
    assert human.exit_code == 0, human.output
    mermaid_match = re.search(r"checked (\d+) Mermaid diagrams", human.output)
    assert mermaid_match is not None and int(mermaid_match.group(1)) > 0
    assert "GitHub Wiki mirror: status=pushed" in human.output
