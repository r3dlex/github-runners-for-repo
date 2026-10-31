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
│   ├── test_disk_cleanup.py     # Sandbox tests for the shell disk-cleanup routine
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

**The `.path` file is written atomically, and as the runner's owner.**
`provision-host.sh` used to write the file with a plain
`printf ... > "$path_file"`, which truncates the live file in place: the
runner reads that file when it starts a job, so a job that starts in that
window gets a truncated or empty PATH and loses uv, bun and archgate. The
file is now written to a temporary file in the same directory and renamed
into place (`rename(2)` within a directory is atomic), and the write runs
**as the owner of the runner home** so the runner is never left with a
root-owned `.path` file it cannot rewrite itself. The enumeration of the
runner homes can reach homes owned by **other users** — the Linux fleet's
runners live in the guest user's home while the provision runs as root —
and the entries written into such a home are derived from that home's own
owner, so they point at the tools the user the runner runs as actually has.
The membership test is an explicit **fixed-string** comparison
(`grep -F` over the colon-folded stream) with no pattern semantics at
all, so a directory whose name contains `.`, `*` or `[` is never mistaken
for one that is already there.

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
   older than the age floor and not in use. Each pass re-measures the disk
   and escalates one tier if it is still over `TARGET_PCT`. The root
   filesystem and the data disk are scored, tiered and escalated
   SEPARATELY: a full root filesystem must never make the sweep more
   aggressive with the data disk that holds the checkouts.

**The safety model has three independent rules, and each one guards a
different way a live job can be harmed.**

1. **The age floor.** The runner writes into an active job's checkout
   continuously, so a live job's directory is never stale by mtime. The
   floor is never zero — the top tier prunes what is older than one hour
   (one day for the daily caches), not everything it can reach. A zero
   window would make every path look stale (`find -newermt "-0 days"`
   matches nothing) and delete live jobs' checkouts. **Do not shorten the
   age thresholds below an hour.**
2. **The liveness scan.** On Linux the routine reads the working directory,
   the open files, the mapped files and the **executable** of *every* live
   process straight out of `/proc` (it falls back to `lsof` elsewhere) and
   treats every path under a watched root as in use. `actions/runner` never
   changes its own working directory — it sets the child's — so the process
   that holds a checkout is the child it spawned (`node`, `git`, `bash`).
   Filtering on the runner's command line protects nothing. A process whose
   working directory is a checkout's *direct parent* holds every checkout
   below it and protects them all; the check stops at one level, because the
   runner's own process holds the runner home and walking further up would
   disable the sweep entirely.

3. **The executable image.** `/proc/<pid>/exe` is a magic symlink: it names
   the binary the process is *running*, and the kernel keeps no descriptor
   for it — so it is in neither the working directory nor the descriptor
   list. The installed toolchain trees are therefore checked against that
   set separately, and a tree that a live process is executing is never
   removed. The check does not depend on the tree's mtime. On macOS there
   is no `/proc`, and the `lsof` fallback asks for the file **type** as
   well as the name: the text segment of the running binary is reported
   with the type `txt`, which is the same information `/proc/<pid>/exe`
   and `/proc/<pid>/maps` carry on Linux.

   The **second line of defence** is the access time of the interpreter:
   a tree whose interpreter was run in the last 24 hours is not reclaimed
   either, even when no process is running it any more. The mtime is the
   sweep's staleness signal and it says nothing about use — a toolchain
   that a job uses every day was installed months ago and is exactly what
   the sweep deletes. The window is short on purpose (`TOOLCHAIN_RECENT_HOURS`,
   default 24): a long one would protect every tree that is used at all,
   and the sweep would silently stop reclaiming what it exists to reclaim.
   The signal is only ever **additive** — it protects a tree, it never
   condemns one — and it is a coarse one: `relatime` bumps the access
   time at most once a day, and macOS does not update it for reads at all.

The in-use set is re-collected at the top of every pass **and immediately
before every destructive step inside the watched roots**, so a job that
starts while an earlier pass is running is protected by the next one. The
paths outside the watched roots — the apt package index and the crash
dumps — are guarded by their own activity check instead: the index is
dropped only while no package manager is running, the crash dumps only
while no coredump is being written. The scan therefore runs once per
candidate, and it has to stay cheap: it reads the whole host with one
`ls -l` per directory of descriptors instead of a `readlink` fork per
descriptor (~490 ms per scan on the fleet host, which is minutes of lock
time per pass). The two collectors have to return the same set of paths;
the sandbox test compares them on every Linux run.

**A few paths are never swept, whatever their age:** `_work/_update` (the
runner's self-update staging area — deleting it mid-update aborts the copy
and can leave a runner that cannot start), `_work/_tool`, `_work/_actions`
and `_work/_temp` (the runner's own bookkeeping, swept by their own age
windows), `_diag/*.log` (the worker logs are the evidence for the
"assigned, zero steps" signature, so they keep a minimum age at every
tier), and `~/.cache/ms-playwright` (the Playwright browsers — the E2E
leg needs the exact binaries that were downloaded, and re-downloading them
costs ~929 MB and a network round trip). A freshly downloaded update
archive in the runner home is kept too: deleting it aborts the update it
belongs to.

**The shared home is swept in two classes, and they are not the same.**
The *download caches* (pip, uv, npm, bun, pre-commit, the cargo registry,
the rustup downloads, the protobuf download cache, and everything else
cold under `~/.cache`) are pure downloads: a miss costs one fetch, so
they get the short window (`BASE_CACHE_DAYS`, default 3 days). The
*installed toolchain trees* (`~/.local/share/uv/python/cpython-*`, the
mise installs, `~/.rustup/toolchains/*`) are installed state: a miss costs
a re-download of the whole toolchain, so they get their own, much longer
window (`BASE_TOOLCHAIN_DAYS`, default 30 days) and are only reclaimed
from the **elevated tier up**. The tier logic scales that window like every
other one — 15 days at the elevated tier, the one-unit floor at the critical
tier, where the disk has to reclaim something. The user's `site-packages` is
deliberately never swept — the `~/.local/bin` shims are installed into it.

**Every owner home is swept, not just the first one.** `RUNNER_HOMES` takes
a list, and the owner home of every entry — the directory above each
runner home, which is the home of the user the runner runs as — is swept.
A host with two runner homes owned by two different users has two owner
homes; sweeping only the first one left the second user's toolchains
growing without bound while the routine reported green. The runner homes
themselves are made absolute before use: a relative one would make every
path derived from it relative as well, and the sweep would then delete
paths relative to the unit's working directory (`/`).

**Docker is pruned on the data disk, and only within limits:** dangling
images and dangling build cache only, never a tagged image, and never a
stopped container. The Docker daemon on the runner host also serves the
user's own long-lived containers, and their state is not the routine's to
destroy. `docker builder prune -a` (the whole unused build cache, ~12 GB
on the fleet host) is **not** used — the build cache is pruned dangling
only, at every tier.

**A broken interpreter is repaired on every sweep, at every tier.**
`uv` inspects its managed CPython interpreters when it resolves one for a
job; a tree whose interpreter lost its execute bit (mode `0444` instead of
`0755`) makes that inspection fail with `Permission denied (os error 13)`
and breaks CI on the fleet even though the interpreter itself is intact.
That is not a disk-pressure problem, so the repair is **not** gated on the
pressure tiers — it runs on the tier-0 tick too, restores `0755` (the mode
`uv` itself installs, so the tree stays consistent with its siblings), and
is idempotent. **Do not move it behind the tier gate.**

**The routine takes an exclusive `flock` on
`/run/runner-disk-cleanup.lock`.** The weekly `runner-cleanup.service` on
the VM prunes `_work` as well and has no in-use guard of its own, so
**both units must take the same lock** — systemd serialises a unit
against itself, never against a differently named one. The weekly unit
lives outside this repository; the lock file is the contract between the
two. The two units take the lock in different ways, **on purpose**: the
routine takes the lock **itself**, so the 15-minute
`runner-disk-cleanup.timer` unit deliberately carries **no outer
`/usr/bin/flock`** (a unit that locks a lock it already holds deadlocks
the timer against itself). The weekly `runner-cleanup.service` runs a
script that takes no lock of its own, so **it must keep its
`flock -w 300` wrapper**. Do not "fix" one unit to look like the other.

**The action watermark has to go with its version directory.** The runner
marks a downloaded action with a sibling file
(`_work/_actions/<owner>/<repo>/v1.completed` beside `.../v1/`). A sweep
that only removes directories leaves the watermark behind, and
`actions/checkout` then skips the download with the manifest missing — a
failure that never heals, because nothing else removes the watermark. The
routine therefore deletes a watermark exactly when its version directory is
gone.

The host copy lives at `/usr/local/bin/runner-disk-cleanup.sh` on the VM and
is the one proven under real disk pressure. `cleanup-host-disk.sh` in this
repository is the portable, repo-tracked copy: the same logic with the
host-specific paths parameterised (`RUNNER_HOMES`, `HOME_DIR`, `DATA_MOUNT`,
`TMP_DIR`, `LOCK_FILE`) so it also runs on the macOS runners and in CI.
**Keep the two in sync** — when the host copy changes, port the change
here, and the other way round. The repo copy is what the installer writes
to the host, so it must never be *less* safe than the copy already on the
host: a regression here is a regression on the live runners.

The installer is idempotent and **must never stop the `actions.runner.*`
services**: a routine install takes no runner offline. It refuses to
overwrite a host copy of the routine that differs from the repository copy
unless `--force` is passed, and backs the old copy up to
`runner-disk-cleanup.sh.bak` when it does. It updates each runner's
`.path` file the same way `provision-host.sh` does — atomically (write to
a temporary file, then rename) and as the runner user, so the runner never
reads a half-written or root-owned path file. The install directory is
added exactly once: the membership test is an explicit **fixed-string**
comparison (`grep -F` over the colon-folded stream), so it carries no
pattern semantics at all — a directory called `runner[1]` is never
mistaken for the different directory `runner1` — and it is checked per
PATH *fragment* over a colon-wrapped stream so that a fragment at either
end of the file is still delimited.

**The installer's defaults point at the LIVE fleet, and it fails closed
when a run would mix them.** The routine goes to `/usr/local/bin`, the
unit files go to `/etc/systemd/system` and the runner homes are the two
Colima runners. A verification that overrides `INSTALL_PATH` but not
`RUNNER_HOMES` used to write the verification's install directory into
the **live** runners' `.path` file — that happened on 2026-10-28
(`/tmp/verify/sandbox` became the first line of both runners' `.path`),
and every job on them started with a dead PATH entry. A verification that
overrode `INSTALL_PATH` and named `RUNNER_HOMES` but left the unit
directory alone was just as bad: it rewrote the **live**
`runner-disk-cleanup.service` to execute the new path, and the live
15-minute timer then failed on every tick while the root filesystem
filled up. The rules now are:

- **`SANDBOX=1`** makes the run a verification: the runner homes, the
  install path and the unit directory are confined to `SANDBOX_ROOT`
  (default `$TMPDIR/install-disk-cleanup-sandbox`), anything that
  resolves outside it is refused with a non-zero exit before a file is
  touched, and the timer is neither enabled nor started.
- **Without `SANDBOX`**, a run is either entirely **LIVE** (the live
  default for all three knobs) or entirely **ISOLATED** (its units go
  to a directory systemd does not read). A **mix is refused before
  anything is written**, and the refusal does not care whether
  `RUNNER_HOMES` was named:
  - the install *directory* changed but the runner homes did not — the
    run would write that directory into the LIVE runners' `.path` file;
  - the install *path* changed while `UNIT_DIR` is still a live systemd
    unit directory — the run would rewrite the LIVE
    `runner-disk-cleanup.service` to execute the new path;
  - `UNIT_DIR` is not a systemd directory while the install path is
    still the live one — enabling the timer would enable the LIVE
    `runner-disk-cleanup.timer`;
  - the runner homes changed but `INSTALL_PATH` and `UNIT_DIR` did not —
    the run would overwrite the LIVE routine, rewrite the LIVE unit and
    start the LIVE timer.
- **`SYSTEMD_UNIT_DIRS` cannot retarget the live check.** The directories
  systemd reads are hard-coded. A non-sandbox run that sets the variable
  is refused: it used to be the whole answer to "is this a live unit
  directory", and setting it to a sandbox path made `/etc/systemd/system`
  look non-live.
- **The old "name `RUNNER_HOMES` explicitly and you are fine" advice was
  wrong and must not come back**: naming the runner homes alone leaves
  the unit directory and the install path live.
- The install directory is only added to a `.path` file when it exists on
  disk, and the ownership fix never chowns anything outside the sandbox
  root.
- The unit files are written the same way the `.path` file is: to a
  temporary file in the unit directory, then renamed over the old one,
  so a reader never sees a half-written unit.

**The installer never prunes a `.path` file.** It only ever prepends its
install directory. The live runners carry a colon-joined line
(`/usr/games:/usr/local/games:/snap/bin`) whose fragments do not exist on
the Colima VM; a cleanup that dropped "missing" fragments — or a whole
line because one of its fragments was missing — would silently strip PATH
entries the runners are supposed to have. If that ever has to change,
prune per **FRAGMENT** and never per line.

**Everything the install creates under a runner home belongs to the runner
user.** The runner service runs as a non-root user, and a root-owned
`_work` tree makes every job fail with `Access to the path ... is denied`
while the runner still looks healthy — the job is assigned and zero steps
run, which is easily mistaken for a billing problem. The installer derives
that user from the runner home (never a hardcoded name), chowns the runner
home recursively, and verifies that the user can write into the runner
home, `_work`, `_work/_tool`, `_work/_temp`, `_work/_actions` and `_diag`;
it fails closed if it cannot. A root-owned `_actions` or `_diag` produces
the same "assigned, zero steps" failure as a root-owned `_work`.

A **root-owned runner home** is the one case where the directory's owner
cannot be the answer: chowning it to root is a no-op and the write check
would trivially pass, so the install would report success while the
non-root runner service still cannot write. The installer therefore asks
the runner's own systemd unit (`actions.runner.*.service`) which user it
runs as, and uses that; if no unit names a user, it refuses rather than
guessing.

The installer's `chmod -R u+rwX` never removes group/other write, so the
install tightens `.credentials` and `.credentials_rsaparams` to `0600`
explicitly — the host has other non-root users. The cleanup routine only
ever deletes, so it never leaves a root-owned artefact behind — **do not
add a `mkdir` under a runner home to the sweep.**

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
