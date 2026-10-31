#!/usr/bin/env bash
#
# provision-host.sh — make a self-hosted runner host able to run the same
# jobs a GitHub-hosted runner can. GitHub-hosted runners ship with a full
# toolchain pre-installed; a bare self-hosted host (e.g. a fresh Colima VM)
# does not. Without this, jobs that call `npm`, `node`, or `pipx` fail with
# "command not found" on the self-hosted leg, and the wrapper pattern
# silently falls back to the GitHub-hosted leg on every run.
#
# This script is idempotent: it checks before it installs, so re-running it
# is safe and fast. It is invoked by `gh-runners provision`.
#
# What it ensures:
#   - Node.js + npm (Linux: NodeSource; macOS: Homebrew)
#   - An npm global prefix the runner user can write to (Linux only — the
#     default /usr prefix is root-owned and breaks `npm install -g` with
#     EACCES for the non-root runner user)
#   - pip + pipx (for Python tooling installed globally on the host)
#   - uv (the Python package manager — installed by the official astral
#     installer to ~/.local/bin/uv and added to the user's shell rc)
#   - bun (the TypeScript runtime archgate is built with)
#   - archgate (ADR compliance CLI). On Linux/ARM64 the upstream has no
#     release binary, so the script builds it from source via bun and
#     installs the native binary to ~/.archgate/bin/archgate. On Linux/x64
#     and macOS the official install script downloads the platform binary
#     to the same location.
#   - All installed user-local bin dirs are prepended to the runner's
#     `.path` file so the actions-runner service inherits them.
#
# ## The .path file is written atomically, and as the runner's owner
#
# The runner reads its `.path` file when it starts a job. A file that is
# truncated in place and rewritten therefore hands the next job a truncated
# or empty PATH, and the job loses uv, bun and archgate. The file is
# written to a temporary file in the same directory and renamed into place
# instead: a rename(2) within a directory is atomic, so the runner sees
# either the old file or the new one, never a half-written one.
#
# The write runs AS THE OWNER of the runner home, so the runner is never
# left with a root-owned `.path` file it cannot rewrite itself.
#
# The runner homes are enumerated explicitly (see ensure_user_path_in_runner
# below), and the enumeration can reach homes owned by OTHER users — the
# Linux fleet's runners live in the guest user's home while the provision
# runs as root. The entries written into such a home are derived from that
# home's own owner, so they point at the tools the user the runner runs as
# actually has.
#
set -euo pipefail

NODE_MAJOR="${NODE_MAJOR:-22}"
NPM_PREFIX="${NPM_PREFIX:-/usr/local}"
ARCHGATE_VERSION="${ARCHGATE_VERSION:-v0.60.0}"

log() { printf '[provision] %s\n' "$*"; }

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  SUDO="sudo"
fi

uname_s="$(uname -s)"
uname_m="$(uname -m)"

# user-local bin dirs that must end up on the runner's PATH
USER_BIN_DIRS=(
  "$HOME/.local/bin"
  "$HOME/.bun/bin"
  "$HOME/.archgate/bin"
)

ensure_user_path_in_shell_rc() {
  # Persist PATH additions so interactive shells and the `bash -lc` jobs
  # run by `actions/runner` see the new tools. Idempotent.
  local rc_file
  for rc_file in "$HOME/.bashrc" "$HOME/.profile"; do
    [ -f "$rc_file" ] || continue
    if ! grep -qF "# runner-provision PATH" "$rc_file" 2>/dev/null; then
      # shellcheck disable=SC2016
      printf '\n# runner-provision PATH\nexport PATH="%s:$PATH"\n' \
        "$(IFS=:; echo "${USER_BIN_DIRS[*]}")" >> "$rc_file"
    fi
  done
}

# True when the fragment is already in the runner's .path file.
#
# The file holds one PATH fragment per line and colons are allowed inside a
# line, so the lines are folded into one colon-separated stream before the
# match: a fragment that ends a line has no colon after it and a plain
# whole-file match cannot see it, so the fragment is prepended again on
# every provision run and the file grows by a line each time.
#
# The fragment is compared as a FIXED string. The old `case` pattern only
# matched literally because the expansion happened to sit inside double
# quotes — an expansion that lost its quotes would turn the fragment into
# a glob, and a directory whose name contains ".", "*" or "[" would then
# match far more than it should: a home directory called "runner[1]"
# would look like it was already in the file, and the entry would never be
# added.
#
# Same test as scripts/install-disk-cleanup.sh uses for the install
# directory. The two scripts keep their own copy on purpose: the installer
# and the routine are installed on the host separately, and neither may
# depend on a file the other does not bring along.
path_file_has() {
  local path_file="$1" fragment="$2"
  grep -Fxq -- "$fragment" "$path_file" 2>/dev/null && return 0
  # The stream is wrapped in colons so that a fragment at either end of
  # the file is still delimited: the first fragment of the file has no
  # colon in front of it.
  printf '%s' ":$(tr '\n' ':' <"$path_file" 2>/dev/null):" |
    grep -Fq -- ":${fragment}:" && return 0
  return 1
}

# The user that owns a directory. GNU stat and BSD stat spell this
# differently, and the provision runs on both, so both are tried. Never
# hardcoded: the runner service runs as the owner of its runner home.
dir_owner() {
  local dir="$1" owner numeric
  owner="$(stat -c '%U' "$dir" 2>/dev/null || true)"
  if [ -z "$owner" ]; then
    owner="$(stat -f '%Su' "$dir" 2>/dev/null || true)"
  fi
  # An id with no passwd entry comes back as UNKNOWN; fall back to the
  # numeric id, which chown and sudo both accept.
  if [ -z "$owner" ] || [ "$owner" = "UNKNOWN" ]; then
    numeric="$(stat -c '%u' "$dir" 2>/dev/null || true)"
    if [ -z "$numeric" ]; then
      numeric="$(stat -f '%u' "$dir" 2>/dev/null || true)"
    fi
    [ -n "$numeric" ] && owner="$numeric"
  fi
  printf '%s' "$owner"
}

# Run a command as another user. Root uses sudo directly; a non-root
# provision uses non-interactive sudo so a missing password fails the
# provision instead of hanging on a prompt.
as_user() {
  local user="$1"
  shift
  if [ "$(id -un)" = "$user" ]; then
    "$@"
  elif [ "$(id -u)" -eq 0 ]; then
    sudo -u "$user" -- "$@"
  else
    sudo -n -u "$user" -- "$@"
  fi
}

# The home parents that are scanned for runner homes, one per line.
#
# Only the parent of THIS platform is scanned by default: on macOS /home is
# an autofs mount, and a glob under it blocks until the automounter gives
# up, so a provision that scans both parents hangs on the macOS runners.
# The macOS runners live under /Users, the Linux fleet under /home.
#
# An EXPLICITLY EMPTY RUNNER_HOME_PARENTS means "scan nothing": the
# operator has said where the runner homes are not. An unset variable
# means "use the platform default".
home_parents() {
  local -a parents=()
  if [ -n "${RUNNER_HOME_PARENTS+x}" ]; then
    read -r -a parents <<<"$RUNNER_HOME_PARENTS" || true
  elif [ "$(uname -s)" = "Darwin" ]; then
    parents=(/Users)
  else
    parents=(/home)
  fi
  [ "${#parents[@]}" -gt 0 ] || return 0
  printf '%s\n' "${parents[@]}"
}

ensure_user_path_in_runner() {
  # The actions-runner service uses the .path file in the runner dir to
  # set PATH. Prepend our user-local bin dirs so the service can find
  # uv, bun, and archgate.
  #
  # The runner home is NOT necessarily under $HOME. A root-run provision has
  # HOME=/root, while the fleet's Linux runners live in the guest user's
  # home (/home/<user>/actions-runner*), so the old "$HOME"/actions-runner*
  # glob matched nothing and the runner's .path file was NEVER updated — the
  # same class of defect as the gh PATH guard, one level up.
  #
  # The runner homes are enumerated explicitly instead:
  #
  #   - $RUNNER_HOMES, the space separated list the disk cleanup routine
  #     uses, when the operator sets it. The list is split on WHITESPACE
  #     (spaces, tabs and newlines), exactly like the routine splits it, so
  #     a runner home whose path contains a space cannot be named here;
  #   - the runner dirs directly under $HOME (the macOS layout, where the
  #     runner and its owner share a home);
  #   - the runner dirs one level below a home parent (the Linux fleet
  #     layout: /home/<user>/actions-runner*). The parents are overridable
  #     with $RUNNER_HOME_PARENTS for hosts that keep their homes elsewhere.
  #
  # Only the home parent of THIS platform is scanned: on macOS /home is an
  # autofs mount, and a glob under it blocks until the automounter gives up,
  # so the provision would hang there. On macOS the runners live under
  # /Users, on Linux under /home.
  #
  # The bin dirs are derived from the OWNER's home — the directory above the
  # runner dir — and not from $HOME, so the entries point at the tools the
  # user the runner runs as actually has. When the provision runs as that
  # user the two are the same directory.
  local runner_dir owner_home current d path_file owner tmp_file
  local -a runner_dirs=() bin_dirs=() missing=()

  if [ -n "${RUNNER_HOMES:-}" ]; then
    read -r -a runner_dirs <<<"$RUNNER_HOMES" || true
  fi
  for d in "$HOME"/actions-runner*; do
    runner_dirs+=("$d")
  done
  while IFS= read -r d; do
    [ -n "$d" ] || continue
    for runner_dir in "$d"/*/actions-runner*; do
      runner_dirs+=("$runner_dir")
    done
  done < <(home_parents)

  for runner_dir in "${runner_dirs[@]}"; do
    [ -d "$runner_dir" ] || continue
    path_file="$runner_dir/.path"
    [ -f "$path_file" ] || continue
    # The owner home is resolved PHYSICALLY. On the Linux fleet
    # /home/<user>.linux is a symlink to /home/<user>.guest, so the scan
    # above finds the SAME runner twice — once through each name. With
    # the symlinked name the bin dirs are spelled differently, the
    # membership test does not find what the first pass wrote, and all
    # three fragments are prepended AGAIN.
    owner_home="$(dirname "$runner_dir")"
    owner_home="$(cd "$owner_home" 2>/dev/null && pwd -P)" || owner_home="$(dirname "$runner_dir")"
    bin_dirs=(
      "$owner_home/.local/bin"
      "$owner_home/.bun/bin"
      "$owner_home/.archgate/bin"
    )
    current="$(cat "$path_file" 2>/dev/null || true)"
    missing=()
    for d in "${bin_dirs[@]}"; do
      path_file_has "$path_file" "$d" || missing+=("$d")
    done
    if [ "${#missing[@]}" -gt 0 ]; then
      local prepend
      prepend="$(IFS=:; echo "${missing[*]}")"

      # The file is written to a temporary file in the SAME directory and
      # renamed into place. A file that is truncated in place and
      # rewritten hands the next job a truncated or empty PATH: the
      # runner reads .path when it starts a job, and a job that starts
      # in that window loses uv, bun and archgate. A rename(2) within a
      # directory is atomic, so the runner sees either the old file or
      # the new one.
      #
      # The write runs AS THE OWNER of the runner home, so the runner is
      # never left with a root-owned .path file it cannot rewrite itself.
      owner="$(dir_owner "$runner_dir")"
      if [ -z "$owner" ]; then
        log "WARN: cannot determine the owner of $runner_dir; leaving $path_file untouched"
        continue
      fi
      tmp_file="$path_file.provision.$$"
      # The single quotes are deliberate: $1..$3 are expanded by that
      # shell at runtime, not here.
      #
      # An empty .path file gets the entries and nothing else: a trailing
      # blank line is a PATH fragment that does not exist.
      # shellcheck disable=SC2016
      if ! as_user "$owner" sh -c '
          if [ -n "$2" ]; then
            printf "%s\n%s\n" "$1" "$2" > "$3"
          else
            printf "%s\n" "$1" > "$3"
          fi
        ' sh "$prepend" "$current" "$tmp_file"; then
        log "ERROR: could not write $tmp_file as $owner; leaving $path_file untouched"
        rm -f "$tmp_file" 2>/dev/null || true
        continue
      fi
      if ! as_user "$owner" mv -f -- "$tmp_file" "$path_file"; then
        log "ERROR: could not replace $path_file; leaving it untouched"
        rm -f "$tmp_file" 2>/dev/null || true
        continue
      fi
      log "updated $path_file (added $(IFS=,; echo "${missing[*]}"))"
    fi
  done
}

ensure_node_linux() {
  if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
    log "node $(node --version) / npm $(npm --version) already present"
  else
    log "installing Node.js ${NODE_MAJOR}.x via NodeSource"
    curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | $SUDO -E bash -
    $SUDO apt-get install -y nodejs
  fi

  # The default npm prefix (/usr) is root-owned, so `npm install -g` fails
  # with EACCES for the runner user. Point the prefix at a directory the
  # runner user owns and is already on PATH.
  local user group
  user="$(id -un)"
  group="$(id -gn)"
  $SUDO mkdir -p "${NPM_PREFIX}/lib/node_modules" "${NPM_PREFIX}/bin"
  $SUDO chown -R "${user}:${group}" "${NPM_PREFIX}/lib/node_modules" "${NPM_PREFIX}/bin"
  npm config set prefix "${NPM_PREFIX}"
  log "npm global prefix set to ${NPM_PREFIX} (owned by ${user})"
}

ensure_node_macos() {
  if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
    log "node $(node --version) / npm $(npm --version) already present"
    return
  fi
  if ! command -v brew >/dev/null 2>&1; then
    log "ERROR: Homebrew not found; install it from https://brew.sh then re-run"
    exit 1
  fi
  log "installing Node.js via Homebrew"
  brew install node
}

ensure_pipx_linux() {
  if command -v pipx >/dev/null 2>&1; then
    log "pipx already present"
    return
  fi
  log "installing python3-pip + pipx"
  $SUDO apt-get install -y python3-pip pipx
  pipx ensurepath >/dev/null 2>&1 || true
}

ensure_pipx_macos() {
  if command -v pipx >/dev/null 2>&1; then
    log "pipx already present"
    return
  fi
  if command -v brew >/dev/null 2>&1; then
    log "installing pipx via Homebrew"
    brew install pipx
    pipx ensurepath >/dev/null 2>&1 || true
  else
    log "WARN: Homebrew not found; skipping pipx"
  fi
}

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    log "uv $(uv --version | head -1) already present"
    return
  fi
  log "installing uv via astral.sh (to ~/.local/bin/uv)"
  curl -fsSL https://astral.sh/uv/install.sh | sh
}

ensure_bun() {
  if command -v bun >/dev/null 2>&1; then
    log "bun $(bun --version) already present"
    return
  fi
  log "installing bun (needed to build archgate from source on linux/arm64)"
  curl -fsSL https://bun.sh/install | bash
}

# Build the native archgate binary on linux/arm64 (no upstream release
# asset for that platform). On linux/x64 and macOS the official install
# script downloads a prebuilt platform binary to the same location.
ensure_archgate() {
  if command -v archgate >/dev/null 2>&1 || [ -x "$HOME/.archgate/bin/archgate" ]; then
    log "archgate $(archgate --version 2>/dev/null || ~/.archgate/bin/archgate --version) already present"
    return
  fi

  if [ "$uname_s" = "Linux" ] && [ "$uname_m" = "aarch64" ]; then
    log "building archgate $ARCHGATE_VERSION from source for linux/arm64"
    if ! command -v bun >/dev/null 2>&1; then
      log "ERROR: bun is required to build archgate from source; rerun with bun available"
      exit 1
    fi
    if ! command -v git >/dev/null 2>&1; then
      log "ERROR: git is required to clone the archgate source"
      exit 1
    fi
    local tmpdir
    tmpdir="$(mktemp -d)"
    # shellcheck disable=SC2064
    trap "rm -rf '$tmpdir'" EXIT
    git clone --depth 1 --branch "$ARCHGATE_VERSION" \
      https://github.com/archgate/cli.git "$tmpdir/archgate" 2>&1 | tail -2
    ( cd "$tmpdir/archgate" && bun install >/dev/null )
    mkdir -p "$HOME/.archgate/bin"
    bun build "$tmpdir/archgate/src/cli.ts" --compile --bytecode \
      --outfile "$HOME/.archgate/bin/archgate"
    log "archgate $(~/.archgate/bin/archgate --version) installed to ~/.archgate/bin/archgate"
    trap - EXIT
    rm -rf "$tmpdir"
  else
    log "installing archgate $ARCHGATE_VERSION via official install script (to ~/.archgate/bin)"
    curl -fsSL https://cli.archgate.dev/install-unix | sh
  fi
}

case "$uname_s" in
  Linux)
    $SUDO apt-get update -y
    ensure_node_linux
    ensure_pipx_linux
    ;;
  Darwin)
    ensure_node_macos
    ensure_pipx_macos
    ;;
  *)
    log "ERROR: unsupported OS '$uname_s' (expected Linux or Darwin)"
    exit 1
    ;;
esac

ensure_uv
ensure_bun
ensure_archgate

ensure_user_path_in_shell_rc
ensure_user_path_in_runner

log "host provisioning complete (uv, bun, archgate on PATH)"
