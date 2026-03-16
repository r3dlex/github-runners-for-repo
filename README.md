# github-runners-for-repo

Containerized, zero-install GitHub Actions self-hosted runner manager scoped to a single repository. No software needs to be installed on the host beyond Docker and Python.

## Prerequisites

- Docker and Docker Compose
- Python 3.11+
- [Poetry](https://python-poetry.org/)
- A GitHub Personal Access Token (PAT) with `repo` and `admin:org` scopes (classic PAT) or `Administration: Read and write` permission (fine-grained PAT)

## Quick Start

```bash
# Install dependencies
poetry install

# Configure environment
cp .env.example .env
# Edit .env — set GITHUB_ACCESS_TOKEN and GITHUB_REPOSITORY at minimum

# Start runners
poetry run gh-runners start
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

Three GitHub Actions workflows run on every push to `main` and on pull requests — zero external dependencies required. Pipeline logic is extracted into `tools/pipeline_runner/`, a standalone Python package.

| Workflow | What it does |
|---|---|
| **Lint** | Checks formatting and style with Ruff |
| **Test** | Runs pytest with coverage across Python 3.11, 3.12, 3.13 |
| **Build** | Builds the package, installs the wheel, and verifies the entry point |

Run pipelines locally:

```bash
pip install ./tools/pipeline_runner
pipeline-runner lint          # Check formatting and lint
pipeline-runner test          # Run tests with coverage
pipeline-runner build         # Build and verify package
pipeline-runner all           # All stages in sequence
```

See [`specs/PIPELINES.md`](specs/PIPELINES.md) for design rationale.

## Project Structure

```
github_runners_for_repo/   # Python package (CLI, config, GitHub API client, Docker manager)
runner/                    # Dockerfile and container entrypoint (start.sh)
tools/pipeline_runner/     # CI pipeline runner library (zero-dependency)
tests/                     # Pytest suite (mocked, no live API calls)
specs/                     # Architecture and pipeline documentation
.github/workflows/         # CI pipelines (lint, test, build)
```

## Development

```bash
poetry install                          # Install all dependencies
pip install ./tools/pipeline_runner     # Install pipeline runner
pipeline-runner all                     # Run full CI locally
```

Or run tools directly:

```bash
poetry run pytest             # Run tests
poetry run pytest --cov       # Run tests with coverage
poetry run ruff check .       # Lint
poetry run ruff format .      # Format
```

## License

MIT
