# Architecture — GitHub Runners for Repo

## Overview

This project provides zero-install, containerized GitHub Actions self-hosted runners scoped to a specific repository. No software needs to be installed on the host machine beyond Docker and Python.

## Components

### 1. Runner Container (`runner/`)

A Docker image based on Ubuntu 22.04 that includes:

- The GitHub Actions runner agent (downloaded at build time)
- System dependencies for common CI workloads (Python, Git, build-essential)
- A non-root `docker` user that runs the agent

**Entrypoint (`start.sh`):**

1. Reads `GITHUB_REPOSITORY` and `GITHUB_ACCESS_TOKEN` from environment
2. Calls the GitHub API to obtain a short-lived registration token
3. Configures the runner agent for the target repository
4. Starts the runner agent in the foreground
5. Traps `SIGINT`/`SIGTERM` to deregister the runner on shutdown

### 2. Python CLI (`github_runners_for_repo/`)

A thin orchestration layer that:

- Loads configuration from `.env` / environment variables
- Validates configuration before starting containers
- Verifies GitHub API access (registration token) before launching
- Delegates container lifecycle to `docker compose`
- Provides `status` command that queries both Docker and the GitHub API

### 3. Docker Compose (`docker-compose.yml`)

Defines the runner service with:

- Build context pointing to `runner/`
- Environment variables passed from `.env`
- Horizontal scaling via `--scale runner=N`

## Data Flow

```
User runs `gh-runners start`
    │
    ├─► Python CLI loads .env config
    ├─► Python CLI validates config
    ├─► Python CLI obtains registration token (GitHub API)
    ├─► Python CLI invokes `docker compose up --scale runner=N`
    │       │
    │       └─► Each container:
    │           ├─► start.sh obtains its own registration token
    │           ├─► Configures runner agent
    │           └─► Runs runner agent (waits for jobs)
    │
User runs `gh-runners stop`
    │
    ├─► Python CLI invokes `docker compose down`
    │       │
    │       └─► Each container:
    │           ├─► Receives SIGTERM
    │           ├─► Trap fires: deregisters runner via API
    │           └─► Container exits
```

## Security Considerations

- The `GITHUB_ACCESS_TOKEN` (PAT) needs `repo` scope for repo-level runners
- Tokens are passed via environment variables, never baked into images
- Registration tokens are short-lived (1 hour) and single-use
- Runners deregister on shutdown to avoid stale entries
- The `.env` file is gitignored — only `.env.example` is committed

## Scaling

Scale horizontally by setting `RUNNER_COUNT` or using:

```bash
docker compose up --scale runner=N -d
```

Each container registers as an independent runner with a unique name based on its hostname.

## References

- [GitHub Actions self-hosted runners](https://docs.github.com/en/actions/hosting-your-own-runners)
- [actions/runner-images](https://github.com/actions/runner-images)
- [Containerize a GitHub Actions self-hosted runner](https://baccini-al.medium.com/how-to-containerize-a-github-actions-self-hosted-runner-5994cc08b9fb)
- [GitHub Actions with Docker](https://testdriven.io/blog/github-actions-docker/)
