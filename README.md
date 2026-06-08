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
tools/                     # check_pr_link.py + check_cov_threshold_drift.py
tests/                     # Pytest suite (mocked, no live API calls)
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
