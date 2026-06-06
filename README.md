# github-runners-for-repo

Containerized, zero-install GitHub Actions self-hosted runner manager scoped to a single repository. No software needs to be installed on the host beyond Docker and Python.

## Prerequisites

- Docker and Docker Compose
- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (the only Python toolchain used by this project)
- A GitHub Personal Access Token (PAT) with `repo` and `admin:org` scopes (classic PAT) or `Administration: Read and write` permission (fine-grained PAT)

## Quick Start

```bash
# Install dependencies
uv sync

# Configure environment
cp .env.example .env
# Edit .env — set GITHUB_ACCESS_TOKEN and GITHUB_REPOSITORY at minimum

# Start runners
uv run gh-runners start
```

## Configuration

Copy `.env.example` to `.env` and fill in the values:

| Variable | Required | Default | Description |
|---|---|---|---|
| `GITHUB_ACCESS_TOKEN` | Yes | — | PAT with `repo` and `admin:org` scopes |
| `GITHUB_REPOSITORY` | Yes | — | Target repo (`owner/repo`, case-sensitive) |
| `RUNNER_NAME_PREFIX` | No | `runner` | Prefix for runner names |
| `RUNNER_COUNT` | No | `1` | Number of runner instances |
| `RUNNER_LABELS` | No | `self-hosted,linux,x64` | Comma-separated labels |
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

1. The CLI validates configuration and verifies repository access via the GitHub API.
2. A short-lived registration token is obtained from the GitHub API.
3. Docker Compose launches one or more containers from an Ubuntu 22.04-based image that includes the GitHub Actions runner agent (v2.321.0).
4. Each container registers itself as an independent self-hosted runner scoped to the configured repository.
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
