# AGENTS.md — GitHub Runners for Repo

## Behavior Model: Sustainable YOLO + Progressive Disclosure

### Sustainable YOLO

Agents operate autonomously with bias toward action:

- **Act first, ask only when blocked.** If the task is clear and reversible, do it. Don't ask for permission to write code, run tests, or create files.
- **Test before committing.** Every change must pass tests before being committed. Run the test suite after each meaningful change. Fix failures immediately — do not commit broken code.
- **Solve errors in-loop.** When a test fails or a command errors, diagnose and fix it in the same step. Do not defer errors to the user or leave them for "later."
- **Keep momentum.** Complete the task end-to-end. Don't stop halfway to summarize or ask "should I continue?"
- **Stay scoped.** Do what was asked. Don't refactor unrelated code, add features that weren't requested, or over-engineer.

### Progressive Disclosure

Communicate at the right level of detail:

- **Lead with outcomes.** Say what you did and what the result is. Skip preamble.
- **Details on demand.** Don't explain every line of code unless asked. The diff speaks for itself.
- **Surface blockers immediately.** If something prevents progress, say so right away with the specific error.
- **Escalate only real decisions.** Only ask the user when there's a genuine ambiguity that affects the outcome (e.g., two valid architectural approaches).

## Development Workflow

### Bootstrap (one-time, on a fresh clone)

```bash
# Install archgate globally (provides `archgate check` for pre-commit + CI)
uv tool install archgate

# Install project + dev dependencies into a uv-managed venv
uv sync

# Install the pre-commit + pre-push hooks
uvx pre-commit install --hook-type pre-commit --hook-type pre-push

# Verify all 8 hooks pass on a clean tree
uvx pre-commit run --all-files
uvx pre-commit run --hook-stage pre-push --all-files
```

### Step-by-Step Protocol

1. **Understand** — Read the relevant code before changing it.
2. **Change** — Make the minimal change that achieves the goal.
3. **Test** — Run `uv run pytest` (or the relevant test command). All tests must pass.
4. **Fix** — If tests fail, fix immediately. Do not commit until green.
5. **Commit** — Only when tests pass. Use clear, concise commit messages.
6. **Pre-commit** — `uvx pre-commit run --all-files` mirrors the 8 CI checks locally.

### Commands

```bash
# Install dependencies
uv sync

# Run full CI locally (mirrors the 8 GH Actions checks)
uvx pre-commit run --all-files
uvx pre-commit run --hook-stage pre-push --all-files

# Or run tools directly
uv run pytest
uv run pytest --cov=github_runners_for_repo --cov-fail-under=95
uv run ruff check .
uv run ruff format .
uv run ty check github_runners_for_repo
uv run deptry .
uv run pip-audit
archgate check

# Run the CLI
uv run gh-runners --help
uv run gh-runners provision
uv run gh-runners install
uv run gh-runners configure --name <NAME>
uv run gh-runners start --service
uv run gh-runners stop
uv run gh-runners status
uv run gh-runners remove --name <NAME>
```

### Project Structure

```
├── AGENTS.md                    # This file — agent behavior config
├── CLAUDE.md                    # Points to AGENTS.md
├── pyproject.toml               # uv project configuration (PEP 621)
├── uv.lock                      # Dependency lockfile (single source of truth)
├── .env.example                 # Environment template (committed)
├── .env                         # Actual secrets (NOT committed)
├── github_runners_for_repo/     # Python package
│   ├── __init__.py
│   ├── cli.py                   # CLI entry point (provision/install/configure/start/stop/status/remove)
│   ├── config.py                # Configuration from env vars
│   ├── github_api.py            # GitHub API interactions
│   ├── runner_manager.py        # Native actions/runner binary lifecycle
│   └── scripts/
│       └── provision-host.sh    # Idempotent host toolchain provisioning (Node/npm, pipx)
├── tools/
│   ├── check_pr_link.py         # AC-21: pr-issue-link CI script
│   └── check_cov_threshold_drift.py  # AC-22: coverage drift guard
├── tests/                       # Test suite
│   ├── test_cli.py
│   ├── test_config.py
│   ├── test_github_api.py
│   ├── test_runner_manager.py
│   ├── test_check_pr_link.py
│   └── test_check_cov_threshold_drift.py
├── specs/                       # Documentation
│   ├── architecture.md
│   └── PIPELINES.md
├── .archgate/
│   ├── adrs/                    # ADR markdown + companion .rules.ts
│   └── rules.d.ts               # archgate rule type definitions
├── .github/
│   ├── workflows/               # 8 GH Actions workflows (CI parity)
│   ├── CODEOWNERS
│   ├── PULL_REQUEST_TEMPLATE.md
│   └── ISSUE_TEMPLATE/          # bug.md + feature.md
├── .gitleaks.toml               # AC-19: secrets-scan baseline
├── .pre-commit-config.yaml      # AC-14: 8 local hooks
├── docs/
│   └── branch-protection.md     # AC-1: policy + audit log
└── .omc/                        # OMC artifacts (plans, specs, wiki, drafts, research)
```

### Environment Variables

See `.env.example` for the full list. Key variables:

- `GITHUB_ACCESS_TOKEN` — PAT with `repo` and (for org-level) `admin:org` scopes
- `GITHUB_ORG` — Target org slug for org-level runners (mutually exclusive with `GITHUB_REPOSITORY`)
- `GITHUB_REPOSITORY` — Target repo in `owner/repo` format (mutually exclusive with `GITHUB_ORG`)
- `RUNNER_LABELS` — Default `--labels` for `configure` (comma-separated)
- `RUNNER_GROUP` — Default `--runner-group` for `configure` (org-level only)

### Architecture

The CLI is a thin wrapper around the official `actions/runner` binary. Each
runner lives in its own directory; the CLI is invoked once per directory to
manage that single instance. To run N runners, create N directories and run
the CLI in each. There is no Docker, no custom runner agent, and no
`actions/runner-images` indirection.

A self-hosted host needs the same toolchain a GitHub-hosted runner ships
with (Node/npm, pipx); a bare host (fresh Colima VM, clean macOS box) does
not have it, and jobs that call `npm`/`node` fail with `command not found`,
silently falling back to the GitHub-hosted leg. `gh-runners provision`
runs the bundled idempotent `scripts/provision-host.sh` to close that gap
once per host. This is the permanent home of that lesson — do not delete
it; if a host rebuild loses the toolchain, re-running `provision` restores
it.

The flow for one runner:

0. `gh-runners provision` — one-time per host; installs the toolchain
   (Node/npm via NodeSource on Linux or Homebrew on macOS, npm global
   prefix repointed to a user-writable dir on Linux, pip + pipx).
1. `gh-runners install` — downloads and extracts the official
   `actions-runner-<os>-<arch>-<ver>.tar.gz` from the GitHub release into
   a target directory.
2. `gh-runners configure` — fetches a JIT registration token from the
   GitHub API, then invokes the binary's own `config.sh` with the
   resolved `--url`, `--labels`, and `--runnergroup`.
3. `gh-runners start` — runs `run.sh` in the foreground, or
   `svc.sh install && svc.sh start` with `--service` (launchd on macOS,
   systemd on Linux).
4. `gh-runners stop` — runs `svc.sh stop && svc.sh uninstall` for
   service-mode runners.
5. `gh-runners status` / `gh-runners remove` — query and mutate the
   runner list at the configured scope via the GitHub API.

See `specs/architecture.md` for more details.

## TypeScript runtime for archgate

archgate v0.43.0 ships a bundled TypeScript runtime (bun + node), so the
`archgate` GH Actions job does not need a separate `actions/setup-node@v4`
step. The commit message that first introduced `.github/workflows/archgate.yml`
contains the marker `[archgate-runtime: bundled]` and the workflow file
itself carries the same comment (`# archgate-runtime: bundled`). If
archgate's runtime story changes (e.g., a future major version drops the
bundled runtime), update both this section and the workflow comment, and
add `actions/setup-node@v4` to the workflow.

## Governance

- All changes land via PR — no direct push to `main`.
- Every PR must reference an open issue with `Closes #N` / `Fixes #N`.
- 1 approving review is required; admins are not exempt.
- All 8 CI checks must pass; no merges with red.
- Run `uvx pre-commit run --all-files` before pushing.
- uv is the only Python toolchain; do not introduce Poetry or `pipeline-runner`.
- Architectural decisions live in `.archgate/adrs/` and are enforced by archgate.

### Adding a new check or rule

Governance changes go through the ADR process — never a one-line edit to a
workflow or a hidden flag. The pattern is:

1. **Author an ADR.** Create `.archgate/adrs/ARCH-NNN-<slug>.md` (decision
   record) and a sibling `.archgate/adrs/ARCH-NNN-<slug>.rules.ts` (the
   archgate-enforced rule). Follow the format used by the existing 5 ADRs
   (see `ARCH-001-pr-only-and-issue-link.md` for the template: context,
   decision, consequences). Use the next available `ARCH-NNN` number.
2. **Add the workflow.** If the change introduces a new required check,
   create `.github/workflows/<name>.yml` whose job name matches the ADR's
   rule context exactly, then add that job name to the branch protection
   required-status-checks list via
   `gh api -X PATCH repos/:owner/:repo/branches/main/protection/required_status_checks`.
3. **Wire pre-commit.** Mirror the check in `.pre-commit-config.yaml` so it
   runs locally on `pre-commit` (or `pre-push` for heavy hooks like
   `pytest --cov` and `pip-audit`). Keep the warm pre-commit budget ≤30s.
4. **Update docs.** Reflect the new check in `AGENTS.md` and
   `docs/branch-protection.md`, and reference the new ADR.

The 5 ADRs currently in force are: `ARCH-001-pr-only-and-issue-link`,
`ARCH-002-main-branch-protection` (single-maintainer caveat included),
`ARCH-003-uv-only-toolchain`, `ARCH-004-coverage-floor-95`,
`ARCH-005-stacked-pr-phasing`. See [`docs/branch-protection.md`](docs/branch-protection.md)
for the live policy, the rollback snippet, and the audit log.
