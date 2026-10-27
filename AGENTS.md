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
│       ├── provision-host.sh    # Idempotent host toolchain provisioning (Node/npm, pipx)
│       ├── cleanup-host-disk.sh # Pressure-adaptive disk reclaim routine (timer-driven)
│       └── install-disk-cleanup.sh  # Installs the routine + its two systemd units
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

## archgate on Linux/ARM64

The upstream archgate project ships release binaries only for
`darwin/arm64`, `linux/x64`, and `win32/x64` — there is no
`linux/arm64` build. The self-hosted Linux runners (`r3dlex-runner-3`
and `r3dlex-runner-4` on the Colima VM) are `aarch64`, so the official
install script (`https://cli.archgate.dev/install-unix`) and the npm
shim both hard-reject that platform.

`scripts/provision-host.sh` works around this by building archgate from
source on linux/arm64:

1. Install [Bun](https://bun.sh) (the build toolchain archgate uses).
2. `git clone --depth 1 --branch $ARCHGATE_VERSION https://github.com/archgate/cli.git`
3. `bun install` (pulls archgate's npm deps into the cloned tree).
4. `bun build src/cli.ts --compile --bytecode --outfile ~/.archgate/bin/archgate`
   — Bun's `--compile` produces a self-contained native binary, so the
   result runs without a Bun runtime at job time.

The resulting binary is installed to `~/.archgate/bin/archgate` and
the runner's `.path` file is updated to include that directory (plus
`~/.local/bin` for `uv` and `~/.bun/bin` for the build toolchain).

The `.github/workflows/archgate.yml` workflow skips the
`archgate/setup-action@v1` step on linux/arm64 (it would fail) and runs
`archgate check` directly using the pre-installed binary, which is on
the runner's PATH via the `.path` file. On every other platform
(macOS, linux/x64) the setup-action still downloads the upstream
release binary, so behavior there is unchanged.

## Runner host disk management

The self-hosted Linux runners (`r3dlex-runner-3` and `r3dlex-runner-4`) live
in a Colima VM with a 19G root filesystem and a 98G data disk at
`/mnt/lima-colima`. The root is the bottleneck: it carries the OS, the
toolchain, the runner binaries and every package cache. When it filled, both
runners went offline and jobs wedged in post-cleanup.

Two mitigations are in place, and both are tracked here:

1. **The runner `_work` directory is bind-mounted onto the data disk**
   (`/dev/vdb1` → `<runner-home>/actions-runner-linux{,-2}/_work`). The 98G
   disk sat nearly empty while the 19G root filled.
2. **`github_runners_for_repo/scripts/cleanup-host-disk.sh` runs every 15
   minutes**, driven by `runner-disk-cleanup.timer` and installed by
   `github_runners_for_repo/scripts/install-disk-cleanup.sh`. The timer is a
   fixed heartbeat; the work is pressure-adaptive — below `WATCH_PCT` (60%)
   nothing is swept, and above it the routine escalates through tiers that
   halve the age thresholds and, at the top tier, prune everything that is
   not in use. Each pass re-measures the disk and escalates again if it is
   still over `TARGET_PCT`.

**The safety model is mtime-based, deliberately.** The VM has neither `lsof`
nor `fuser`, so the routine decides whether a directory belongs to a live job
by mtime alone — the runner writes into an active job's checkout
continuously, so a live job's directory is never stale by mtime. On Linux it
also reads the open files and working directories of live `Runner.Worker`
processes straight out of `/proc` and treats every path under them as in use.
**Do not shorten the age thresholds and do not add an `lsof` dependency** — a
live job's checkout is protected by that rule, and the wrong threshold
deletes work in progress.

The host copy lives at `/usr/local/bin/runner-disk-cleanup.sh` on the VM and
is the one proven under real disk pressure. `cleanup-host-disk.sh` in this
repository is the portable, repo-tracked copy: the same logic with the
host-specific paths parameterised (`RUNNER_HOMES`, `HOME_DIR`, `DATA_MOUNT`)
so it also runs on the macOS runners and in CI. **Keep the two in sync** —
when the host copy changes, port the change here, and the other way round.

The installer is idempotent and **must never stop the `actions.runner.*`
services**: a routine install takes no runner offline. It updates each
runner's `.path` file the same way `provision-host.sh` does.

**Everything the install creates under a runner home belongs to the runner
user.** The runner service runs as a non-root user, and a root-owned
`_work` tree makes every job fail with `Access to the path ... is denied`
while the runner still looks healthy — the job is assigned and zero steps
run, which is easily mistaken for a billing problem. The installer derives
that user from the runner home (never a hardcoded name), chowns the runner
home recursively, and verifies that the user can write into `_work`; it
fails closed if it cannot. The cleanup routine only ever deletes, so it
never leaves a root-owned artefact behind — **do not add a `mkdir` under a
runner home to the sweep.**

Every job in `.github/workflows/` sets `timeout-minutes` explicitly — 30 for
test/build/lint work, 5 for the health heartbeat and the verdict gate.
GitHub's default is 360 minutes, and with only two Linux runners one hung
job can occupy a runner for six hours.

GitHub-hosted runners **cannot start for private repositories** in this org:
the Actions spending limit is exhausted ("The job was not started because
recent account payments have failed or your spending limit needs to be
increased."). Public repositories are unaffected. For private repos the
self-hosted fleet is the only working path.

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
