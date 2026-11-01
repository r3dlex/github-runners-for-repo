# Architecture — GitHub Runners for Repo

## Overview

This project is a thin CLI wrapper around the official
[`actions/runner`](https://github.com/actions/runner) binary. It does not
ship a custom runner agent, a Docker image, or any platform-specific
build pipeline. The CLI downloads the upstream release, configures it
against an org or repo, and starts it as a service.

Runners are scoped to either a single repository or an entire
organization, chosen via the `GITHUB_ORG` / `GITHUB_REPOSITORY` env var
pair (mutually exclusive).

## Components

### 1. The official `actions/runner` binary

Not part of this repo — the CLI downloads the release tarball at
`install` time. The package contains:

- `config.sh` — registers the runner against an org/repo using a JIT
  token; idempotent with `--replace`.
- `run.sh` — starts the runner agent in the foreground.
- `svc.sh` — installs and starts a `launchd` (macOS) or `systemd`
  (Linux) service for the runner.
- `_diag/`, `_work/`, `bin/`, `externals/` — internal directories the
  runner manages.

The CLI never reads or writes to those internal paths directly.

### 2. Python CLI (`github_runners_for_repo/`)

Four modules:

- `config.py` — loads `RunnerConfig` from `.env` / environment
  variables. Exposes `runner_scope` (`org` if `GITHUB_ORG` is set,
  else `repo`), `runner_url`, and `api_path` properties that the API
  client reads.
- `github_api.py` — `_check_access`, `get_registration_token`,
  `list_runners`, `remove_runner`. All four dispatch on
  `config.api_path`, so the same code path serves org- and repo-scope.
- `runner_manager.py` — `install` (download + extract),
  `configure` (token + `config.sh`), `start` (`run.sh` or `svc.sh`),
  `stop` (`svc.sh` teardown), `status` (API list), `remove` (API
  delete).
- `cli.py` — argparse subcommand dispatch with `--target-dir`,
  `--name`, `--labels`, `--runner-group`, `--work-dir`, `--service`,
  `--version`, and `--env-file` flags.

No container runtime, no Compose, no `Dockerfile`. The CLI runs the
official scripts directly on the host.

### 3. Configuration (`.env`)

`GITHUB_ACCESS_TOKEN` plus exactly one of `GITHUB_ORG` or
`GITHUB_REPOSITORY`. Optional `RUNNER_LABELS` and `RUNNER_GROUP`
provide defaults for `configure`. See `.env.example` for the full
list.

## Data Flow

### Install + configure (one-time per runner directory)

```
User runs `gh-runners install --target-dir ./runners/mac-1`
    │
    ├─► Python CLI downloads actions-runner-osx-arm64-2.334.0.tar.gz
    │       from https://github.com/actions/runner/releases/download/v2.334.0
    ├─► Python CLI extracts into ./runners/mac-1/
    └─► Done. config.sh, run.sh, svc.sh are now in the target dir.

User runs `gh-runners configure --name r3dlex-runner-1 --target-dir ./runners/mac-1`
    │
    ├─► Python CLI loads .env, validates GITHUB_ACCESS_TOKEN + GITHUB_ORG
    ├─► Python CLI calls GitHub API: POST /orgs/{org}/actions/runners/registration-token
    ├─► Python CLI invokes ./runners/mac-1/config.sh with:
    │       --url https://github.com/{org}
    │       --token <jit-token>
    │       --name r3dlex-runner-1
    │       --labels <RUNNER_LABELS>
    │       --runnergroup <RUNNER_GROUP>
    │       --unattended --replace
    └─► config.sh writes .runner / .credentials / .path inside the target dir.
```

### Start (foreground or service)

```
User runs `gh-runners start --service --target-dir ./runners/mac-1`
    │
    ├─► Python CLI invokes ./runners/mac-1/svc.sh install
    ├─► Python CLI invokes ./runners/mac-1/svc.sh start
    └─► launchd / systemd starts the runner and keeps it running across reboots.

User runs `gh-runners start --target-dir ./runners/mac-1`  (no --service)
    │
    └─► Python CLI os.execvs ./runners/mac-1/run.sh (foreground).
        The runner runs until the user hits Ctrl-C.
```

### Stop / status / remove

```
User runs `gh-runners stop --target-dir ./runners/mac-1`
    │
    ├─► svc.sh stop
    └─► svc.sh uninstall

User runs `gh-runners status`
    │
    └─► GET /orgs/{org}/actions/runners → list with status, busy, labels

User runs `gh-runners remove --name r3dlex-runner-1`
    │
    ├─► List runners, find id for the named runner
    └─► DELETE /orgs/{org}/actions/runners/{id}
```

### Host provisioning (once per host)

```
User runs `gh-runners provision`  (optionally `--non-interactive`)
    │
    ├─► Python CLI shells out to scripts/provision-host.sh
    │
    └─► provision-host.sh
        ├─ resolve_tool_dirs  → the tool user, npm prefix and PATH
        ├─ Linux:  NodeSource (Node/npm) → pipx → GitHub CLI
        │          macOS:  Homebrew  (Node/npm) → pipx → Homebrew gh
        ├─ uv, bun, archgate, ar
        └─ verify_gh_runnable  → run `gh` as the runner user under the
                                 default job PATH
```

`provision` is a **host-level** step, not a per-runner one: it installs
the toolchain that a GitHub-hosted runner image ships with (Node/npm,
pipx, `gh`, uv, bun) onto a bare host. It is idempotent — a second run
checks what is there and only installs what is missing — so it is also
the way to restore a host after a rebuild.

The privileged steps escalate with `sudo`: interactively by default, so
a watched invocation can answer the password prompt, and with `sudo -n`
under `--non-interactive`, where a missing password **fails** the
provisioning instead of hanging on a prompt.

The GitHub CLI is installed from a **pinned** release on Linux
(`GH_VERSION`, default `v2.103.3`) to `GH_INSTALL_PATH` (default
`/usr/local/bin/gh`). The digest recorded in the script
(`gh_expected_sha256`) is the only trust anchor; `GH_VERSION=latest` is
refused, because a release named at install time has no recorded digest
and the upstream checksum file arrives from the same origin as the
tarball. On macOS the pinned release is Linux-only, so `gh` comes from
Homebrew and the Homebrew prefix is put on the runner's `.path` file.

The install path is checked against the default job `PATH` **before**
anything is downloaded, and the directory that holds the `gh` is checked
**before** the binary is staged into it. The directory is verified, not
taken over: it has to belong to root and must not be group or other
writable, and the provisioning never chowns it and never chmods it. The
binary itself is repaired on every path — mode `0755`, owner
`root:root`, never the user that runs the provisioning — because
`/usr/local/bin/gh` is shared by every job on the host.

The final check runs the `gh` **as the runner user** (the owner of the
runner home, named by the `RUNNER_HOMES` knob) under the default job
`PATH`: root bypasses the mode bits, so a root-only check would pass for
a binary the runner user cannot execute. With no runner user on the
host the property is proved for *every* user instead, and a runner user
that cannot be switched to fails the provisioning.

## Scaling

There is no built-in `--count` flag. Scale by creating one directory per
runner and running `gh-runners install && gh-runners configure &&
gh-runners start --service` in each. The official `actions/runner` is
designed for one process per directory, and this CLI mirrors that
constraint deliberately.

## Security Considerations

- The `GITHUB_ACCESS_TOKEN` (PAT) needs `repo` scope for repo-level
  runners; org-level runners additionally need the `admin:org` scope
  (classic PAT) or `Administration: Read and write` permission on the
  org (fine-grained PAT).
- Tokens are passed via environment variables only — never baked into
  scripts or arguments visible in process listings (`--token` is the
  last argument before the runner's own config, and the CLI logs
  truncate it).
- Registration tokens are short-lived (1 hour) and single-use.
- The `.env` file is gitignored — only `.env.example` is committed.
- Service-mode runners persist as `launchd` jobs (macOS) or
  `systemd` units (Linux) under the current user — the install
  command does not require root on macOS.

## References

- [GitHub Actions self-hosted runners](https://docs.github.com/en/actions/hosting-your-own-runners)
- [actions/runner](https://github.com/actions/runner)
- [actions/runner release notes](https://github.com/actions/runner/releases)
