# github-runners-for-repo

Containerized, zero-install GitHub Actions self-hosted runner manager scoped to a repository or organization. No software needs to be installed on the host beyond Docker and Python.

## Prerequisites

- Docker and Docker Compose (v2 plugin)
- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (the only Python toolchain used by this project)
- A GitHub Personal Access Token (PAT) with `repo` scope for repo-scoped runners; also add the `admin:org` scope (classic PAT) or `Administration: Read and write` permission for the org (fine-grained PAT) when targeting an organization

## Quick Start

```bash
# Install dependencies
uv sync

# Configure environment
cp .env.example .env
# Edit .env — set GITHUB_ACCESS_TOKEN and exactly one of GITHUB_ORG or GITHUB_REPOSITORY

# Start runners
uv run gh-runners start
```

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

After `uv run gh-runners start` with `GITHUB_ORG=...`, confirm the
runners are registered at the org level:

```bash
uv run gh-runners status
gh api orgs/<your-org>/actions/runners | jq '.runners[] | {name, status, labels: [.labels[].name]}'
```

You should see `RUNNER_COUNT` runners with `status: "online"` and the
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
| `RUNNER_LABELS` | No | `self-hosted,linux,x64,r3dlex-org` | Comma-separated labels |
| `RUNNER_GROUP` | No | `Default` | Runner group assignment |
| `RUNNER_IMAGE` | No | `github-runner:latest` | Docker image name |

## CLI Commands

| Command | Description |
|---|---|
| `gh-runners start` | Build image (if needed), obtain registration token, start containers |
| `gh-runners stop` | Stop containers and deregister runners from GitHub |
| `gh-runners status` | Show container and runner status |
| `gh-runners build` | Build the runner Docker image |

All commands accept `--env-file` to specify a custom `.env` path.

## How It Works

1. The CLI validates configuration and verifies GitHub API access (org or repo, based on which env var is set).
2. A short-lived registration token is obtained from the GitHub API at the appropriate endpoint.
3. Docker Compose launches one or more containers from an Ubuntu 22.04-based image that includes the GitHub Actions runner agent (v2.321.0).
4. Each container registers itself as an independent self-hosted runner scoped to the configured organization or repository.
5. On shutdown, containers deregister cleanly via the GitHub API to prevent stale entries.

## CI Pipelines

Seven GitHub Actions workflows run on every push to `main` and on pull requests — zero external dependencies required. All workflows use `uv` and run the same commands locally and in CI (no Poetry, no custom pipeline runner).

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
github_runners_for_repo/   # Python package (CLI, config, GitHub API client, Docker manager)
runner/                    # Dockerfile and container entrypoint (start.sh)
tools/                     # check_pr_link.py + check_cov_threshold_drift.py
tests/                     # Pytest suite (mocked, no live API calls)
specs/                     # Architecture and pipeline documentation
.archgate/                 # ADRs + archgate rule type definitions
.github/                   # CODEOWNERS + PR/issue templates + 7 GH Actions workflows
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
