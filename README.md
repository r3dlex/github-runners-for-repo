# github-runners-for-repo

CLI for installing, configuring, and running official [`actions/runner`](https://github.com/actions/runner)
binaries as GitHub Actions self-hosted runners — scoped to a repository or
organization. Each runner lives in its own directory; the CLI is invoked
once per runner directory to manage that single instance.

## Strategy

The CLI is a thin wrapper around the official `actions/runner` package. It
does three things and gets out of the way:

1. **`install`** — downloads and extracts the official `actions-runner-<os>-<arch>-<ver>.tar.gz` from the GitHub release into a target directory.
2. **`configure`** — fetches a short-lived registration token from the GitHub API, then invokes the official `config.sh` with the resolved `--url`, `--labels`, and `--runnergroup`.
3. **`start` / `stop`** — runs `run.sh` in the foreground or installs a `launchd` (macOS) / `systemd` (Linux) service via `svc.sh`.

`status` and `remove` query and mutate the runner list at the configured scope.

No Docker, no custom runner agent, no `actions/runner-images` indirection.
Whatever the official binary supports, the CLI supports.

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (the only Python toolchain used by this project)
- A GitHub Personal Access Token (PAT) with `repo` scope for repo-scoped runners; also add the `admin:org` scope (classic PAT) or `Administration: Read and write` permission for the org (fine-grained PAT) when targeting an organization
- The host OS that the runner will execute on: macOS (x64 / arm64) or Linux (x64 / arm64). Windows is supported via `actions-runner-win-x64-<ver>.zip` but is not exercised in CI.

## Host provisioning

GitHub-hosted runners ship with a full toolchain pre-installed; a bare
self-hosted host (a fresh Colima VM, a clean macOS box) does not. When a
job calls `npm`, `node`, or `pipx` on an unprovisioned host it fails with
`command not found`, and the self-hosted-preferred wrapper silently falls
back to the GitHub-hosted leg on every run — so self-hosted never actually
does the work.

`gh-runners provision` runs a bundled, idempotent script that closes that
gap once per host:

- **Node.js + npm** — Linux via NodeSource (`NODE_MAJOR`, default `22`),
  macOS via Homebrew.
- **npm global prefix** (Linux only) — the default `/usr` prefix is
  root-owned, so `npm install -g` fails with `EACCES` for the non-root
  runner user. The script repoints the prefix at `/usr/local` (overridable
  with `NPM_PREFIX`) and chowns it to the runner user, which is already on
  `PATH`.
- **pip + pipx** — for Python tooling installed globally on the host.

```bash
gh-runners provision        # safe to re-run; checks before it installs
```

Run this once on each host before `configure` / `start`. It is the
permanent home of the "self-hosted host needs the toolchain too" lesson —
re-running after a host rebuild restores the toolchain.

## Host disk management

A self-hosted runner host fills up. The Linux runners live in a Colima VM
with two filesystems: a 19G root (`/dev/root`) and a 98G data disk
(`/dev/vdb1`, mounted at `/mnt/lima-colima`). The 19G root is the
bottleneck — it carries the OS, the toolchain, the runner binaries and
every package cache, and it is the one that fills first. When it filled,
both Linux runners went offline, jobs wedged in post-cleanup, and the whole
org stalled.

Two things keep that from happening again:

- **The runner's `_work` directory is bind-mounted onto the data disk.** The
  98G disk sat nearly empty while the 19G root filled, so the work area
  (checkouts, downloaded actions, the tool cache) is mounted on the data
  disk instead of the root. The two filesystems then fill independently and
  the root is no longer competing with the checkouts.
- **A cleanup routine runs every 15 minutes.** `runner-disk-cleanup.timer`
  fires `runner-disk-cleanup.service` on that heartbeat. The timer is fixed
  but the work is pressure-adaptive: below 60% disk usage nothing is swept,
  and above it the routine escalates through tiers (watch, elevated,
  critical) that halve the age thresholds and, at the top tier, prune
  everything that is older than the age floor and not in use. After each
  pass the disk is re-measured, and the routine escalates one tier and
  sweeps again if it is still over target — one pass is never assumed to
  be enough. The two filesystems are scored, tiered and escalated
  separately: a full root filesystem never makes the sweep more aggressive
  with the data disk that holds the checkouts.

### What the routine will not delete

Two independent rules keep a live job's checkout alive, and both apply at
every tier:

- **The age floor.** The runner writes into an active job's checkout
  continuously, so a live job's directory is never stale by mtime. The
  floor is never zero — the top tier prunes what is older than one hour
  (one day for the daily caches), not everything it can reach.
- **The liveness scan.** On Linux the routine reads the working directory
  and the open files of *every* live process straight out of `/proc` (it
  falls back to `lsof` elsewhere), and any path under a watched root counts
  as in use regardless of age. `actions/runner` never changes its own
  working directory, so the process that holds a checkout is the child it
  spawned — `node`, `git`, `bash`. A process whose working directory is
  `_work` itself holds every checkout below it and protects them all.

**A path that is in use is never deleted, at any tier.** The in-use set is
re-collected at the start of every pass, so a job that starts while an
earlier pass is running is protected by the next one.

A few directories are never swept at all, whatever their age:

- `_work/_update` — where the runner stages a self-update before copying
  the new binaries into `bin.<version>`. Removing it mid-update aborts the
  copy and can leave a runner that cannot start.
- `_work/_tool`, `_work/_actions`, `_work/_temp` — the runner's own
  bookkeeping, swept by their own age windows.
- `_diag/*.log` — the worker logs are the evidence for the "assigned, zero
  steps" signature, so they keep a minimum age at every tier.
- A freshly downloaded update archive in the runner home — deleting it
  aborts the update it belongs to.

Docker is pruned on the data disk, and only within limits: **dangling**
images and build cache only, never a tagged image, and **never** a stopped
container. The Docker daemon on the runner host also serves the user's own
long-lived containers, and their state is not the routine's to destroy.

### Repairing a bricked action

The runner marks a downloaded action with a watermark file *next to* the
action's version directory (`_work/_actions/<owner>/<repo>/v1.completed`
beside `.../v1/`). If the version directory is swept away and the
watermark is left behind, `actions/checkout` skips the download, the action
manifest is missing, and the step fails — forever, because nothing else
removes the watermark. The routine therefore deletes a watermark exactly
when its version directory is gone, which makes the runner download the
action again on the next job.

### Repairing a broken interpreter

`uv` inspects its managed CPython interpreters when it resolves one for a
job. A tree whose interpreter lost its execute bit (mode `0444` instead of
`0755`) makes every such inspection fail with `Permission denied (os error
13)`, which breaks CI on the fleet even though the interpreter itself is
intact and the host is otherwise healthy.

That is not a disk-pressure problem, so the repair is **not** gated on the
pressure tiers: it runs on every sweep, including the tier-0 tick, and
restores the execute bit on any managed interpreter that is missing it. The
mode it restores is `0755` — the mode `uv` itself installs — so the tree
stays consistent with its siblings instead of ending up at `0455`. The
repair is idempotent: a second run finds nothing to do.

```bash
runner-disk-cleanup.sh --status   # report only, changes nothing
runner-disk-cleanup.sh --dry-run  # report what would change, change nothing
```

### Not running twice at once

The routine takes an exclusive `flock` on `/run/runner-disk-cleanup.lock`.
The weekly `runner-cleanup.service` on the same host prunes the work tree
as well and has no in-use guard of its own, so **both units must take the
same lock**: systemd serialises a unit against itself, never against a
differently named one. The weekly unit lives outside this repository — the
lock file is the contract between the two.

### Installing it

```bash
github_runners_for_repo/scripts/install-disk-cleanup.sh    # Linux
```

The installer is idempotent: it installs the script to
`/usr/local/bin/runner-disk-cleanup.sh`, writes the two systemd units, and
enables the timer. It never stops the `actions.runner.*` services — a
routine install must not take a runner offline. On macOS it installs
nothing (systemd timers are Linux-only), but the routine itself is
portable and runs there too.

The host copy at `/usr/local/bin/runner-disk-cleanup.sh` is the one that
has been proven under real disk pressure, so the installer refuses to
overwrite a host script whose content differs from the repository copy
unless you pass `--force`. With `--force` the previous host script is
backed up to `runner-disk-cleanup.sh.bak` first:

```bash
github_runners_for_repo/scripts/install-disk-cleanup.sh --force
```

### Runner ownership

The runner service runs as a non-root user, and it has to be able to write
into its own work directory. When a root-run install creates that
directory — when the work tree is bind-mounted onto the data disk, for
instance — the directories end up root-owned and every job dies within
about three seconds with `Access to the path ... is denied`. The runner
still looks healthy, because it accepted the job and then failed to write
its work directory: the job is assigned and no step ever runs, which is
easy to mistake for a billing problem.

The installer therefore derives the owning user from the runner home
(never a hardcoded name), hands the whole runner home back to that user
recursively, and then verifies that the user can actually write into the
runner home, `_work`, `_work/_tool`, `_work/_temp`, `_work/_actions` and
`_diag` — a root-owned `_actions` or `_diag` produces the same
"assigned, zero steps" failure as a root-owned `_work`. If it cannot, the
install fails instead of reporting success. The runner's credential files
are tightened to `0600` while the installer is there, so no other user on
the host can read or rewrite them. The cleanup routine itself only ever
deletes, so it cannot leave a root-owned artefact behind.

A **root-owned** runner home is the one case where the directory's owner
cannot be the answer: chowning it to root is a no-op and the write check
would trivially pass, so the install would report success while the
non-root runner service still could not write. The installer therefore
asks the runner's own systemd unit (`actions.runner.*.service`) which user
it runs as, and uses that. If no unit names a user, the install refuses
rather than guessing.

Each runner's `.path` file is updated atomically — written to a temporary
file and renamed — and as the runner user, so the runner never reads a
half-written or root-owned path file. The install directory is added
exactly once: the file is checked line by line, because that is the format
the installer writes it in.

### Job timeouts

GitHub's default job timeout is 360 minutes. With only two Linux runners,
one hung job can occupy a runner for six hours. Every job in
`.github/workflows/` therefore sets `timeout-minutes` explicitly: 30
minutes for test/build/lint work, 5 minutes for the health heartbeat and
the verdict gate.

### GitHub-hosted runners cannot start for private repos

The org's Actions spending limit is exhausted, so GitHub-hosted runners
refuse to start for **private** repositories:

> The job was not started because recent account payments have failed or
> your spending limit needs to be increased.

Public repositories (including this one) are unaffected. For private repos
the self-hosted fleet is the only working path — which is why the runners
have to stay online, and why the disk routine and the job timeouts matter.

## Quick Start

```bash
# Install dependencies
uv sync

# Configure environment
cp .env.example .env
# Edit .env — set GITHUB_ACCESS_TOKEN and exactly one of GITHUB_ORG or GITHUB_REPOSITORY

# Provision the host toolchain (Node/npm, pipx) — one-time per host
gh-runners provision

# Create a runner directory and install the official binary
mkdir -p ./runners/mac-1
cd ./runners/mac-1
gh-runners install        # downloads actions-runner-osx-arm64-2.334.0.tar.gz

# Configure it (fetches a JIT token, runs config.sh)
gh-runners configure --name r3dlex-runner-1

# Start it as a launchd service (persistent across reboots)
gh-runners start --service
```

Run the same sequence in another directory for each additional runner.
The CLI does not manage multiple runners in one invocation — that's a
deliberate choice, because the official `actions/runner` is designed
for one process per directory.

## Org-level runners

Pick **org-level** when you want runners to serve every repository under an
organization (for example, all repos under `r3dlex/*`). Pick **repo-level**
when the runner should only see a single repository.

| | Repo-level | Org-level |
|---|---|---|
| `GITHUB_REPOSITORY` | `owner/repo` | (leave empty) |
| `GITHUB_ORG` | (leave empty) | `owner` |
| Token scope required | `repo` (classic) or repo-scoped fine-grained | `repo` + `admin:org` (classic) OR `Administration: Read and write` for the org (fine-grained) |
| Visible to | the one repo | every repo in the org (subject to runner-group restrictions) |
| API path | `/repos/{owner}/{repo}/actions/runners/...` | `/orgs/{org}/actions/runners/...` |

> **Label collision warning.** When you register org-level runners, every
> `runs-on: self-hosted` workflow in any repo under the org that matches
> your labels can start landing on these new runners — a silent behavior
> change for repos that did not ask for them. The default label set
> (`self-hosted,linux,x64,r3dlex-org`) includes a project-specific
> opt-in label (`r3dlex-org`) so existing `runs-on: self-hosted` workflows
> do **not** silently migrate. Update the opt-in label to match your
> org, and migrate workflows with `runs-on: <your-label>` when you are
> ready.

> **Runner groups.** To restrict runner access to specific repos, create
> a custom runner group in the org settings and set
> `RUNNER_GROUP=<name>` in `.env`. Runners in the `Default` group are
> available to every repo in the org.

### Verification

```bash
gh-runners status
gh api orgs/<your-org>/actions/runners | jq '.runners[] | {name, status, labels: [.labels[].name]}'
```

You should see each registered runner with `status: "online"` and the
labels from your `.env`.

## Configuration

Copy `.env.example` to `.env` and fill in the values. `GITHUB_ORG` and
`GITHUB_REPOSITORY` are mutually exclusive — set exactly one.

| Variable | Required | Default | Description |
|---|---|---|---|
| `GITHUB_ACCESS_TOKEN` | Yes | — | PAT with `repo` (and `admin:org` for org-level) scopes |
| `GITHUB_ORG` | One of | — | Target org slug (case-sensitive) — mutually exclusive with `GITHUB_REPOSITORY` |
| `GITHUB_REPOSITORY` | One of | — | Target repo (`owner/repo`, case-sensitive) — mutually exclusive with `GITHUB_ORG` |
| `RUNNER_NAME_PREFIX` | No | `runner` | Prefix for runner names |
| `RUNNER_COUNT` | No | `4` | Number of runner instances |
| `RUNNER_LABELS` | No | `self-hosted,linux,x64,r3dlex-org` | Comma-separated labels for `configure --labels` |
| `RUNNER_GROUP` | No | `Default` | Runner group for `configure --runner-group` |

## CLI Commands

| Command | Description |
|---|---|
| `gh-runners provision` | Install the host toolchain jobs expect (Node/npm, pipx) — idempotent |
| `gh-runners install [--target-dir DIR] [--version VER]` | Download and extract the official `actions/runner` binary |
| `gh-runners configure --name NAME [--target-dir DIR] [--labels L] [--runner-group G] [--work-dir W] [--no-replace]` | Fetch a JIT token, run `config.sh` with the resolved options |
| `gh-runners start [--target-dir DIR] [--service]` | Start the runner (foreground by default; `--service` installs `launchd`/`systemd`) |
| `gh-runners stop [--target-dir DIR]` | Stop the runner service |
| `gh-runners status` | List runners registered at the configured scope |
| `gh-runners remove --name NAME` | Deregister a runner by name from the configured scope |

All commands accept `--env-file` to specify a custom `.env` path.

## How It Works

1. The CLI validates configuration and verifies GitHub API access (org or repo, based on which env var is set).
2. `install` downloads `actions-runner-<os>-<arch>-<ver>.tar.gz` from the official GitHub release and extracts it into the target directory.
3. `configure` fetches a short-lived registration token from the appropriate `/orgs/{org}/actions/runners/registration-token` or `/repos/{owner}/{repo}/actions/runners/registration-token` endpoint, then invokes the binary's own `config.sh` with `--url`, `--token`, `--name`, `--labels`, `--runnergroup`, `--unattended`, `--replace`.
4. `start --service` runs the binary's `svc.sh install && svc.sh start` (launchd on macOS, systemd on Linux). Without `--service`, the CLI `exec`s `run.sh` in the foreground.
5. `stop` runs `svc.sh stop && svc.sh uninstall` for service-mode runners.
6. `status` lists runners at the configured scope via the GitHub API.
7. `remove` looks up a runner by name and calls `DELETE` on the GitHub API.

## CI Pipelines

Eight GitHub Actions workflows run on every push to `main` and on pull
requests — zero external dependencies required. All workflows use `uv` and
run the same commands locally and in CI (no Poetry, no custom pipeline
runner).

| Workflow | Required check | What it does |
|---|---|---|
| **Lint** | `lint` | `uv run ruff check .` + `uv run ruff format --check .` |
| **Test** | `test` | `uv run pytest` across Python 3.11, 3.12, 3.13 |
| **Coverage** | `coverage` | `uv run pytest --cov=... --cov-fail-under=95` + drift guard |
| **Build** | `build` | `uv build` + entry-point verification |
| **archgate** | `archgate` | `archgate check` against `.archgate/adrs/*.rules.ts` |
| **pr-issue-link** | `pr-issue-link` | PR body must contain `Closes #N` / `Fixes #N` |
| **ty** | `ty` | `uv run ty check github_runners_for_repo` |
| **deptry-pip-audit** | `deptry-pip-audit` | `uv run deptry .` + `uv run pip-audit` |

All 8 required checks (the 7 above + `secrets-scan`) run on every PR. See
[`docs/branch-protection.md`](docs/branch-protection.md) for the policy
and the audit log.

### Run pipelines locally

```bash
# Install archgate + project deps (one-time)
uv tool install archgate
uv sync
uvx pre-commit install --hook-type pre-commit --hook-type pre-push

# Mirror all 8 CI checks locally
uvx pre-commit run --all-files                    # 6 fast hooks
uvx pre-commit run --hook-stage pre-push --all-files   # + coverage + deptry-pip-audit

# Or run tools directly
uv run ruff check .              # Lint
uv run ruff format .             # Format
uv run pytest                    # Tests
uv run pytest --cov=github_runners_for_repo --cov-fail-under=95
uv run ty check github_runners_for_repo
uv run deptry .
uv run pip-audit
uv build                         # Build the wheel
archgate check                   # ADR compliance
```

See [`specs/PIPELINES.md`](specs/PIPELINES.md) for design rationale.

## Project Structure

```
github_runners_for_repo/   # Python package (CLI, config, GitHub API client, runner manager)
  scripts/                 # provision-host.sh (toolchain) + disk cleanup routine and its installer
tools/                     # check_pr_link.py + check_cov_threshold_drift.py
tests/                     # Pytest suite (mocked, no live API calls, sandbox tests for the shell scripts)
specs/                     # Architecture and pipeline documentation
.archgate/                 # ADRs + archgate rule type definitions
.github/                   # CODEOWNERS + PR/issue templates + 8 GH Actions workflows
```

## Development

```bash
uv sync                            # Install all dependencies
uvx pre-commit run --all-files     # Run all 8 CI checks locally
```

Or run tools directly:

```bash
uv run pytest             # Run tests
uv run pytest --cov       # Run tests with coverage
uv run ruff check .       # Lint
uv run ruff format .      # Format
```

## License

MIT
