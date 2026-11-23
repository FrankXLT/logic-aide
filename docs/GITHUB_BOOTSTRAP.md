# Git & Private GitHub Bootstrap (`docs/GITHUB_BOOTSTRAP.md`)

Use this guide to verify local git hygiene, enforce LF line endings for 68HC11 cross-assemblers, create a private GitHub repository via `gh`, configure branch protection, and set up parallel agent worktrees.

---

## 1. Verify Local Git, `.gitignore`, and `.gitattributes` LF Rules

LogicAIDE pins `eol=lf` across `.asm`, `.inc`, `.sym`, `.lst`, `.py`, `.md`, `.json`, `.toml`, and web assets because 68HC11 cross-assemblers (`AS11`, `Blackfoot`, `A09`) and Python parity tests break on CRLF drift.

```bash
# 1. Initialize git if not already initialized (default branch: main)
git init -b main

# 2. Enforce LF normalization locally
git config core.autocrlf false
git config core.eol lf

# 3. Verify .gitattributes and .gitignore are respected
git check-attr eol -- .claude/README.md docs/WORKFLOWS.md
# Expected: eol: lf

# 4. Verify OEM ROMs, Parquet lakes, raw heritage, and secrets are ignored
git status --ignored -s
```

**Pre-Commit Leak Check**: Before your first commit, confirm that zero `.bin`, `.s19`, `.parquet`, `.env`, `secrets.env`, or `data/heritage_raw/` payloads appear in `git status`:

```bash
git ls-files --others --exclude-standard | grep -E '\.(bin|s19|rom|hex|parquet|env|pem|key)$' || echo "Clean"
```

---

## 2. Initial Clean Commit

```bash
git add .gitignore .gitattributes .quiet-ui.md AGENTS.md CLAUDE.md .claude/ .vscode/ docs/
git commit -m "chore: seed LogicAIDE .claude harness, workflow specs, and repo guardrails"
```

---

## 3. Create Private GitHub Repository via `gh`

```bash
# Authenticate gh if needed
gh auth status

# Create a private repo under your GitHub account/org and push main
gh repo create logic-aide \
  --private \
  --source=. \
  --remote=origin \
  --description "LogicAIDE: Offline SMEC/SBEC 68HC11 ROM simulator, High-Speed SCI DuckDB lake, and Chrysler heritage workbench" \
  --push
```

---

## 4. Branch Protection on `main`

Enable branch protection so candidate ROM edits, UI changes, and telemetry schema updates always flow through PRs and verified checks:

```bash
OWNER_REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner)"

gh api \
  --method PUT \
  "repos/${OWNER_REPO}/branches/main/protection" \
  -H "Accept: application/vnd.github+json" \
  --input - <<'EOF'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["pytest", "ui-visual-contract"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "required_approving_review_count": 1
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
EOF
```

*(Note: On GitHub Free personal private repositories, branch protection API rules may require GitHub Pro or a Ruleset via `gh api repos/${OWNER_REPO}/rulesets`.)*

---

## 5. Parallel Agent Worktree Setup

When running multiple coding agents (e.g., one on `rom/` + `emulator/`, another on `ui/` + `docs/showcase/`), isolate edits with `git worktree` instead of sharing one working tree:

```bash
# Create isolated feature worktrees alongside the repo
git worktree add ../logic-aide-rom -b feat/rom-twin
git worktree add ../logic-aide-ui -b feat/workbench-ui

# List active worktrees
git worktree list

# Clean up after PR merge
git worktree remove ../logic-aide-rom
git worktree remove ../logic-aide-ui
```

Remember: **`.claude/settings.json` Hardware Deadbolt** rules apply per worktree—keep `rom/inputs/` and `rom/stock_baselines/` immutable across every branch.
