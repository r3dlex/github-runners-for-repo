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
# ## How it is run
#
# It is normally run AS ROOT: the toolchain goes to /usr/local, the npm
# prefix has to be chowned, and the runner's `.path` file has to be
# written. Run as the runner user instead, every privileged step is
# escalated with sudo — interactively by default, so a watched
# invocation can answer the password prompt. `--non-interactive` turns
# the escalation into `sudo -n`, where a missing password fails the
# provisioning instead of hanging on a prompt.
#
# The runner user is NOT taken from $HOME: provisioning runs as root and
# root's home holds no runner directory. It is the owner of the runner
# home, and the runner home is named by the RUNNER_HOMES knob with
# ABSOLUTE paths (the same knob install-disk-cleanup.sh uses). A runner
# home owned by root is SKIPPED: root is not a runner user and a check
# that ran as root would pass for a binary the runner user cannot run.
# When no runner home other than a root-owned one exists the provisioning
# says so loudly and proves the install the other way round — the binary
# has to be executable by EVERY user on the host, because every job on
# the host runs it. The check never passes without proving something.
#
# What it ensures:
#   - Node.js + npm (Linux: NodeSource; macOS: Homebrew)
#   - An npm global prefix the runner user can write to (Linux only — the
#     default /usr prefix is root-owned and breaks `npm install -g` with
#     EACCES for the non-root runner user). The prefix lives in the runner
#     user's own home: its bin directory has to be writable by the runner
#     user, and a shared /usr/local/bin would let a job replace the
#     host-wide GitHub CLI that lives next to it.
#   - pip + pipx (for Python tooling installed globally on the host)
#   - The GitHub CLI (gh). The GitHub-hosted images ship it pre-installed;
#     a bare self-hosted host does not, so a workflow that calls `gh api`
#     dies with "gh: command not found" (exit code 127) while the runner
#     itself looks healthy. On Linux the official release tarball is
#     installed to /usr/local/bin/gh — the architecture the host runs on
#     (arm64 or amd64) — which is root-owned and on the default GitHub
#     Actions job PATH. The release is pinned and the download is checked
#     against a recorded sha256 before it is unpacked: the binary ends up
#     root-owned and world executable, so every job on the host runs it.
#     The owner is ALWAYS root:root — never the user that happens to run
#     the provisioning — because a host-wide binary the runner user owns
#     is a binary every job on the host can replace.
#     The directory that holds the gh is CHECKED, not repaired: it has to
#     belong to root and may not be group or other writable, or only root
#     can replace the gh. A directory that fails the check is refused —
#     the provisioning does not take a host-wide directory over.
#     On macOS gh comes from Homebrew: the pinned release install is
#     Linux-only, and the Homebrew prefix is not on the Linux job PATH.
#     The prefix is added to the runner's .path file so the macOS jobs
#     find the Homebrew gh.
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
# The npm prefix: where `npm install -g` puts its packages.
#
# It is NOT /usr/local. The bin dir of the prefix has to be writable by
# the runner user — that is what makes `npm install -g` work as that
# user — and /usr/local/bin also holds the host-wide GitHub CLI.
# unlink(2) and rename(2) are governed by the write permission on the
# CONTAINING DIRECTORY, not by the owner or the mode of the file inside
# it: a runner-writable /usr/local/bin lets the runner user delete
# /usr/local/bin/gh and replace it with anything at all, and the next
# job that calls `gh api` then runs the attacker's code with the job's
# own GITHUB_TOKEN.
#
# The prefix therefore defaults to a directory of the runner user's own
# home, and its bin dir is put on the runner PATH. Set NPM_PREFIX to
# move it; a prefix whose bin dir holds the GitHub CLI is refused.
NPM_PREFIX="${NPM_PREFIX:-}"
ARCHGATE_VERSION="${ARCHGATE_VERSION:-v0.60.0}"
# Where the GitHub CLI is installed and which release is installed. The
# release is PINNED: the sha256 of the asset is recorded in this file and
# the download is verified against it, so the install is reproducible and
# a tampered tarball never reaches /usr/local/bin. A release that has no
# recorded digest stops the install BEFORE the download.
#
# GH_VERSION=latest is refused. A release that is named at install time
# has no recorded digest, and the upstream checksum file is not an
# alternative: it arrives from the same origin as the tarball, so it
# cannot vouch for it. "Latest" would buy a moving target at the price
# of the guarantee — the two do not go together.
GH_INSTALL_PATH="${GH_INSTALL_PATH:-/usr/local/bin/gh}"
GH_VERSION="${GH_VERSION:-v2.103.3}"

# The runner homes on this host, as ABSOLUTE paths, separated by spaces.
# Same knob as install-disk-cleanup.sh uses, so an operator points both
# at the same place. Unset means "discover them" (see home_parents): a
# hardcoded fleet path would miss every other host, and a $HOME lookup
# misses them when provisioning runs as root.
RUNNER_HOMES="${RUNNER_HOMES:-}"
read -r -a RUNNER_HOME_LIST <<<"$RUNNER_HOMES"

log() { printf '[provision] %s\n' "$*"; }

# A message that must not be swallowed by a command substitution. The
# caller captures stdout, so a message on stdout would end up in the
# captured value instead of reaching the operator.
log_stderr() { printf '[provision] %s\n' "$*" >&2; }

# The command used to escalate to root for the writes that need it. It is
# empty when the script already runs as root. It can be pinned from the
# environment — the tests set it to "env" so an install can run into a
# temporary directory without a password prompt.
#
# The name is namespaced on purpose: SUDO is a common environment
# variable, and an unrelated SUDO in the environment would silently
# reroute every privileged call in this script.
PROVISION_SUDO="${PROVISION_SUDO:-sudo}"
if [ "$(id -u)" -eq 0 ] && [ "$PROVISION_SUDO" = "sudo" ]; then
  PROVISION_SUDO=""
fi

# Whether the escalation may prompt for a password. The documented flow is
# a watched invocation, so the default is interactive and a missing
# password prompt can be answered. --non-interactive turns it into
# `sudo -n`, where a missing password fails the provisioning instead of
# hanging on a prompt.
NON_INTERACTIVE=0

# Whether the escalation command is a real sudo. The flags that belong to
# sudo are only passed to it: a pinned escalation command — the tests pin
# $PROVISION_SUDO to "env" — is not sudo and does not know them.
is_sudo() {
  [ "${1##*/}" = "sudo" ]
}

usage() {
  log "usage: $0 [--non-interactive]"
  log "  --non-interactive  the privileged steps must not prompt for a password;"
  log "                     a missing password fails the provisioning instead"
  log "                     of hanging on a prompt"
}

parse_args() {
  local arg
  for arg in "$@"; do
    case "$arg" in
      --non-interactive) NON_INTERACTIVE=1 ;;
      -h | --help)
        usage
        exit 0
        ;;
      *)
        log "ERROR: unknown argument '$arg' (see --help)"
        usage
        exit 1
        ;;
    esac
  done
}

# Run a command as root when the script is not root already.
#
# The escalation is chosen HERE instead of being interpolated into the
# command line: an empty $PROVISION_SUDO used to leave the sudo flags
# behind, so `curl … | $PROVISION_SUDO -E bash -` became `-E bash -` and
# the shell reported "-E: command not found" every time the script ran as
# root — which is how `gh-runners provision` is normally run.
run_privileged() {
  if [ -n "$PROVISION_SUDO" ]; then
    if is_sudo "$PROVISION_SUDO"; then
      if [ "$NON_INTERACTIVE" = "1" ]; then
        "$PROVISION_SUDO" -n "$@"
      else
        "$PROVISION_SUDO" "$@"
      fi
    else
      "$PROVISION_SUDO" "$@"
    fi
  else
    "$@"
  fi
}

# Pipe a remote install script into bash.
#
# `sudo -E` keeps the environment (the NodeSource script needs it), and
# the flag only exists when there is a sudo to hand it to. sudo only
# accepts -E when the invoking user holds the SETENV tag; a host where
# it does not refuses the escalation with "sorry, you are not allowed to
# preserve the environment", and the NodeSource install then has to be
# run by a user that does.
run_remote_script() {
  if [ -n "$PROVISION_SUDO" ]; then
    if is_sudo "$PROVISION_SUDO"; then
      if [ "$NON_INTERACTIVE" = "1" ]; then
        "$PROVISION_SUDO" -n -E bash -
      else
        "$PROVISION_SUDO" -E bash -
      fi
    else
      "$PROVISION_SUDO" bash -
    fi
  else
    bash -
  fi
}

# Put back the EXIT trap that was in place before one of this script's
# own was installed.
#
# `trap - EXIT` clears EVERY EXIT trap, so a caller that registered a
# cleanup of its own would lose it. The trap has to be restored BEFORE
# the script exits, not from inside the EXIT handler: a trap that is
# registered while the EXIT trap runs is never run itself.
restore_exit_trap() {
  trap - EXIT
  if [ -n "${1:-}" ]; then
    eval "$1"
  fi
}

uname_s="$(uname -s)"
uname_m="$(uname -m)"

# The PATH a GitHub Actions job gets by default. gh has to resolve from
# it, not just from the installer's PATH: the job runs as the runner
# user.
DEFAULT_JOB_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# The user-local bin dirs that must end up on the runner's PATH, and the
# user they belong to.
#
# They are the dirs of the TOOL USER's home — the runner user — and NOT
# of the user that happens to run the provisioning: a tool in a home the
# job cannot read is a tool the job cannot run. When the provisioning
# runs as root, the dirs of root's home (/root/.local/bin and friends)
# are on the runner's PATH and reach nobody.
#
# The Homebrew prefix is in the list as well: on macOS the GitHub CLI
# comes from Homebrew, and that prefix is not on the default job PATH.
USER_BIN_DIRS=()
TOOL_USER=""
TOOL_HOME=""
TOOL_PATH=""

# The Homebrew prefix on this host: /opt/homebrew on Apple silicon,
# /usr/local on Intel. Empty when Homebrew is not installed.
#
# Homebrew is a macOS thing. On Linux the function would answer
# /usr/local and put the host's own binary directory on the runner
# PATH, where it does not belong.
homebrew_prefix() {
  [ "$uname_s" = "Darwin" ] || return 1
  if [ -d /opt/homebrew/bin ]; then
    printf '/opt/homebrew'
    return 0
  fi
  if [ -d /usr/local/bin ]; then
    printf '/usr/local'
    return 0
  fi
  return 1
}

# Resolve the tool user, its home and the bin dirs that hold the tools.
#
# The runner user is preferred: the tools belong to the account the
# jobs run as. When no runner home can be found the invoking user is
# used — that is the "provision as the runner user" flow, where the two
# are the same account and nothing moves.
resolve_tool_dirs() {
  local user home prefix
  TOOL_USER="$(id -un)"
  TOOL_HOME="$HOME"
  if user="$(runner_user)" && [ -n "$user" ]; then
    if home="$(user_home "$user")" && [ -n "$home" ]; then
      TOOL_USER="$user"
      TOOL_HOME="$home"
    fi
  fi
  # The npm prefix belongs to the tool user as well: `npm install -g`
  # runs as that user, and the prefix is where it writes. It defaults
  # to a directory of that user's own home so it can never be the
  # directory that holds the host-wide GitHub CLI — see NPM_PREFIX.
  if [ -z "$NPM_PREFIX" ]; then
    NPM_PREFIX="$TOOL_HOME/.npm"
  fi
  USER_BIN_DIRS=(
    "$TOOL_HOME/.local/bin"
    "$TOOL_HOME/.bun/bin"
    "$TOOL_HOME/.archgate/bin"
    "$NPM_PREFIX/bin"
  )
  if prefix="$(homebrew_prefix)"; then
    USER_BIN_DIRS+=("$prefix/bin")
  fi
  TOOL_PATH="$(IFS=:; echo "${USER_BIN_DIRS[*]}")"
}

ensure_user_path_in_shell_rc() {
  # Persist PATH additions so interactive shells and the `bash -lc` jobs
  # run by `actions/runner` see the new tools. Idempotent.
  #
  # The rc file belongs to the TOOL USER, so the tools are on the PATH
  # of the account that owns them.
  resolve_tool_dirs
  local rc_file
  for rc_file in "$TOOL_HOME/.bashrc" "$TOOL_HOME/.profile"; do
    [ -f "$rc_file" ] || continue
    if ! grep -qF "# runner-provision PATH" "$rc_file" 2>/dev/null; then
      as_tool_user bash -c 'printf "\n# runner-provision PATH\nexport PATH=\"%s:$PATH\"\n" "$1" >> "$2"' \
        _ "$TOOL_PATH" "$rc_file"
    fi
  done
}

# True when the fragment is already in the runner's .path file.
#
# The file holds one PATH fragment per line and colons are allowed inside a
# line, so the lines are folded into one colon-separated stream before the
# match. The fragment is compared as a FIXED string: a case pattern would
# treat ".", "*" or "[" in a home directory name as a glob.
#
# Same test as scripts/install-disk-cleanup.sh uses for the install
# directory. The two scripts keep their own copy on purpose: the installer
# and the routine are installed on the host separately, and neither may
# depend on a file the other does not bring along.
path_file_has() {
  local path_file="$1" fragment="$2"
  grep -Fxq -- "$fragment" "$path_file" 2>/dev/null && return 0
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
  if [ -z "$owner" ] || [ "$owner" = "UNKNOWN" ]; then
    numeric="$(stat -c '%u' "$dir" 2>/dev/null || true)"
    if [ -z "$numeric" ]; then
      numeric="$(stat -f '%u' "$dir" 2>/dev/null || true)"
    fi
    [ -n "$numeric" ] && owner="$numeric"
  fi
  printf '%s' "$owner"
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
  # uv, bun, archgate and the npm prefix.
  #
  # The runner home is NOT necessarily under $HOME. A root-run provision has
  # HOME=/root, while the fleet's Linux runners live in the guest user's
  # home (/home/<user>/actions-runner*), so the old "$HOME"/actions-runner*
  # glob matched nothing and the runner's .path file was NEVER updated.
  #
  # The runner homes are enumerated explicitly instead:
  #
  #   - $RUNNER_HOMES, the space separated list the disk cleanup routine
  #     uses, when the operator sets it. The list is split on WHITESPACE,
  #     so a runner home whose path contains a space cannot be named here;
  #   - the runner dirs directly under $HOME (the macOS layout);
  #   - the runner dirs one level below a home parent (the Linux fleet
  #     layout: /home/<user>/actions-runner*). The parents are overridable
  #     with $RUNNER_HOME_PARENTS. An explicitly empty list scans nothing.
  #
  # Only the home parent of THIS platform is scanned: on macOS /home is an
  # autofs mount, and a glob under it blocks until the automounter gives up.
  #
  # The bin dirs are derived from the OWNER's home — the directory above the
  # runner dir — and not from $HOME, so the entries point at the tools the
  # user the runner runs as actually has.
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
    # above finds the SAME runner twice. With the symlinked name the bin
    # dirs are spelled differently and all three fragments are prepended
    # again.
    owner_home="$(dirname "$runner_dir")"
    owner_home="$(cd "$owner_home" 2>/dev/null && pwd -P)" || owner_home="$(dirname "$runner_dir")"
    bin_dirs=(
      "$owner_home/.local/bin"
      "$owner_home/.bun/bin"
      "$owner_home/.archgate/bin"
    )
    # Optional extras. The path tests extract this function on its own, so
    # these are added only when the rest of the script has defined them.
    # A hardcoded fourth directory would grow a .path file the tests have
    # already filled with the three dirs above.
    if [ -n "${NPM_PREFIX:-}" ]; then
      bin_dirs+=("$NPM_PREFIX/bin")
    fi
    if declare -F homebrew_prefix >/dev/null 2>&1; then
      local prefix
      if prefix="$(homebrew_prefix 2>/dev/null)" && [ -n "$prefix" ]; then
        bin_dirs+=("$prefix/bin")
      fi
    fi
    current="$(cat "$path_file" 2>/dev/null || true)"
    missing=()
    for d in "${bin_dirs[@]}"; do
      path_file_has "$path_file" "$d" || missing+=("$d")
    done
    if [ "${#missing[@]}" -gt 0 ]; then
      local prepend
      prepend="$(IFS=:; echo "${missing[*]}")"

      # Written to a temporary file in the SAME directory and renamed into
      # place, as the owner of the runner home. A truncate-in-place hands
      # the next job an empty PATH. A rename(2) within the directory is
      # atomic. An empty .path file gets the entries and nothing else.
      owner="$(dir_owner "$runner_dir")"
      if [ -z "$owner" ]; then
        log "WARN: cannot determine the owner of $runner_dir; leaving $path_file untouched"
        continue
      fi
      tmp_file="$path_file.provision.$$"
      # shellcheck disable=SC2016
      # runsvc.sh does PATH=$(cat .path). A newline is not a separator,
      # so the last new dir and the first old entry would leave PATH.
      if ! as_user "$owner" sh -c '
          folded=$(printf "%s" "$2" | tr "\n" ":")
          folded=${folded%:}
          folded=${folded#:}
          if [ -n "$folded" ]; then
            printf "%s:%s\n" "$1" "$folded" > "$3"
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
    curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | run_remote_script
    run_privileged apt-get install -y nodejs
  fi

  # The default npm prefix (/usr) is root-owned, so `npm install -g` fails
  # with EACCES for the runner user. Point the prefix at a directory the
  # runner user owns and is already on PATH.
  #
  # The owner is the TOOL USER — the runner user — and not the user that
  # runs the provisioning: the jobs run `npm install -g` as the runner
  # user, and a prefix that belongs to root is a prefix nobody can write
  # to.
  resolve_tool_dirs
  local user group npm_bin
  user="$TOOL_USER"
  group="$(id -gn "$user" 2>/dev/null || id -gn)"
  npm_bin="${NPM_PREFIX}/bin"
  # The bin dir of the prefix is made writable by the runner user, so
  # that `npm install -g` works as that user. That is only safe when the
  # dir does not also hold a host-wide binary: the write permission on
  # the CONTAINING DIRECTORY is what decides whether the gh inside it can
  # be deleted and replaced. A prefix that shares its bin dir with the
  # GitHub CLI is refused instead of silently handing the gh to the
  # runner user.
  if dir_holds_the_gh "$npm_bin"; then
    log "ERROR: the npm prefix $NPM_PREFIX puts its binaries in $npm_bin"
    log "ERROR: that directory holds the host-wide GitHub CLI ($GH_INSTALL_PATH)"
    log "ERROR: making it writable by $user would let a job replace the gh"
    log "ERROR: set NPM_PREFIX to a directory that is not shared with the host binaries"
    exit 1
  fi
  run_privileged mkdir -p "${NPM_PREFIX}/lib/node_modules" "$npm_bin"
  run_privileged chown -R "${user}:${group}" "${NPM_PREFIX}/lib/node_modules" "$npm_bin"
  as_tool_user npm config set prefix "${NPM_PREFIX}"
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
  # Homebrew refuses to run as root, so the documented root flow has to
  # install as the tool user.
  as_tool_user brew install node
}

ensure_pipx_linux() {
  if command -v pipx >/dev/null 2>&1; then
    log "pipx already present"
    return
  fi
  log "installing python3-pip + pipx"
  run_privileged apt-get install -y python3-pip pipx
  pipx ensurepath >/dev/null 2>&1 || true
}

ensure_pipx_macos() {
  if command -v pipx >/dev/null 2>&1; then
    log "pipx already present"
    return
  fi
  if command -v brew >/dev/null 2>&1; then
    log "installing pipx via Homebrew"
    as_tool_user brew install pipx
    as_tool_user pipx ensurepath >/dev/null 2>&1 || true
  else
    log "WARN: Homebrew not found; skipping pipx"
  fi
}

# The GitHub CLI release to install. The version is pinned by default.
#
# A release that is named at install time has no recorded digest, and
# the upstream checksum file cannot stand in for one: it arrives from
# the same origin as the tarball. "Latest" is therefore refused rather
# than installed on a weaker guarantee.
#
# The diagnostic goes to STDERR: the caller captures stdout in a command
# substitution, so a message on stdout would be swallowed into the
# captured value and the operator would see a bare exit code with no
# explanation at all.
gh_version() {
  if [ "$GH_VERSION" = "latest" ]; then
    log_stderr "ERROR: GH_VERSION=latest is not supported: the GitHub CLI install is pinned"
    log_stderr "ERROR: a release that is named at install time has no recorded sha256"
    log_stderr "ERROR: set GH_VERSION to a release whose digest is recorded in gh_expected_sha256"
    return 1
  fi
  printf '%s' "${GH_VERSION#v}"
}

# True when the on-disk file is a regular file whose bytes are the pinned
# binary. A symlink is not: hashing it follows the link, and the caller
# must not treat the target as the installed gh.
gh_file_matches_pin() {
  local version="$1" arch="$2" actual expected
  [ -f "$GH_INSTALL_PATH" ] || return 1
  [ -L "$GH_INSTALL_PATH" ] && return 1
  expected="$(gh_binary_sha256 "$version" "$arch" || true)"
  [ -n "$expected" ] || return 1
  actual="$(file_sha256 "$GH_INSTALL_PATH")" || return 1
  [ "$actual" = "$expected" ]
}

# The asset architecture for the host. The CLI publishes linux_arm64 and
# linux_amd64; the runner fleet is aarch64.
#
# The diagnostic goes to STDERR: the caller captures stdout in a command
# substitution, so a message on stdout would be swallowed into the
# captured value and the operator would see a bare exit code with no
# explanation at all.
gh_linux_arch() {
  case "$uname_m" in
    aarch64 | arm64) printf 'arm64' ;;
    x86_64 | amd64) printf 'amd64' ;;
    *)
      printf '[provision] ERROR: unsupported architecture %s for the GitHub CLI install\n' \
        "'$uname_m'" >&2
      exit 1
      ;;
  esac
}

# The sha256 of the pinned release assets, keyed by version and
# architecture.
#
# These digests are the ones upstream publishes in
# gh_<version>_checksums.txt, recorded here so the download is checked
# against a constant this repository controls. A checksum file fetched
# from the same origin as the tarball proves nothing on its own: it can
# be tampered with together with the asset it describes.
#
# A version that is not listed here has no trusted digest, and a release
# without a trusted digest is not installed: the next maintainer action,
# bumping GH_VERSION, must not silently swap the trust anchor for one
# that arrives from the same origin as the tarball.
gh_expected_sha256() {
  local version="$1" arch="$2"
  case "${version}-${arch}" in
    2.103.3-arm64)
      printf 'ee32df2323c8847f5fac6cb6b4b69ca75828e9329a8f22f677021f7b3306ce63'
      ;;
    2.103.3-amd64)
      printf '4c806978ffce1cec70853302a56d70ac6da4f19bae7adca4e1a8ab00633dd91d'
      ;;
    *)
      return 1
      ;;
  esac
}

# The sha256 of the gh BINARY inside the pinned release tarball.
#
# The tarball digest above cannot answer "is the file already on disk the
# pinned binary". A version string cannot either: the file is executed to
# obtain it, and a script that prints the right version is then kept and
# chowned root:root. The binary digest is recorded so an already-installed
# file can be checked without executing it and without downloading the
# release again. Computed from the official v2.103.3 linux assets.
gh_binary_sha256() {
  local version="$1" arch="$2"
  case "${version}-${arch}" in
    2.103.3-arm64)
      printf 'a82a52e1bbe05bcd485f8cbbd5aa67e11a76fce6bdad394a2d179e1c24cfe367'
      ;;
    2.103.3-amd64)
      printf 'b768184570b12536be4ac1528893f6340d1c6a7b40b56ad45bca9cd0e87ad34d'
      ;;
    *)
      return 1
      ;;
  esac
}

# The sha256 of the upstream CHECKSUM FILE of a pinned release.
#
# The checksum file is fetched only as corroboration for the digest
# recorded above, and it arrives from the same origin as the asset — so
# the file itself is pinned here as well. A file that is not the one
# this repository recorded is not trusted to corroborate anything: the
# recorded asset digest still decides, and the install continues.
#
# The recorded file is the one this repository keeps next to this
# script; the script itself records only its digest. The file is read by
# the test, never by the install.
gh_checksums_sha256() {
  local version="$1"
  case "$version" in
    2.103.3)
      printf 'f756e7859d90dbb5e85aae5194206c37da220bec9bac23d3d659078a8e766fdc'
      ;;
    *)
      return 1
      ;;
  esac
}

# The sha256 of a file. GNU coreutils and the BSD tools spell this
# differently and the script runs on both.
file_sha256() {
  local file="$1" out
  if command -v sha256sum >/dev/null 2>&1; then
    out="$(sha256sum "$file")" || return 1
  elif command -v shasum >/dev/null 2>&1; then
    out="$(shasum -a 256 "$file")" || return 1
  else
    log "ERROR: no sha256sum or shasum on this host; cannot verify the download"
    return 1
  fi
  # Both print "<digest>  <file>"; the digest is the first field.
  printf '%s' "${out%% *}"
}

# The sha256 upstream publishes for an asset, read from the release's
# checksum file. This is corroboration only — the file arrives from the
# same origin as the asset. Fails when the file cannot be fetched, when
# it is not the file this repository recorded, or when it has no entry
# for the asset.
upstream_sha256() {
  local tmpdir="$1" version="$2" asset="$3" sums candidate digest name
  sums="$tmpdir/checksums.txt"
  if ! curl -fsSL \
    "https://github.com/cli/cli/releases/download/v${version}/gh_${version}_checksums.txt" \
    -o "$sums"; then
    return 1
  fi
  # A pinned release has a pinned checksum FILE as well. A file that is
  # not the recorded one — a re-published checksum, or a tampered one —
  # is not used to corroborate anything: the recorded asset digest
  # decides, and the install continues on it.
  local pinned recorded
  pinned="$(gh_checksums_sha256 "$version" || true)"
  if [ -n "$pinned" ]; then
    recorded="$(file_sha256 "$sums" || true)"
    if [ "$recorded" != "$pinned" ]; then
      # The messages go to stderr: the caller captures stdout, and a
      # message on stdout would end up in the captured digest.
      log_stderr "WARN: the upstream checksum file for $version is not the one this repository recorded"
      log_stderr "WARN: it is not used to corroborate the digest; the recorded digest decides"
      return 1
    fi
  fi
  while IFS= read -r candidate; do
    # The line is "<digest>  <asset>"; the asset name has to match
    # exactly, a prefix is not enough.
    read -r digest name <<<"$candidate"
    if [ "$name" = "$asset" ]; then
      printf '%s' "$digest"
      return 0
    fi
  done <"$sums"
  return 1
}

# Verify a downloaded release asset before anything is unpacked.
#
# The digest that decides is the constant recorded in this file; the
# upstream checksum file only corroborates it. Fails closed — a missing
# digest, an unreadable file or a mismatch all stop the install before
# the archive is unpacked and before the binary is written.
#
# The recorded digest is the ONLY anchor. The upstream checksum file is
# fetched from the same origin as the asset, so it catches a broken
# download but not a compromised release: it may disagree with the
# recorded digest (and then the install stops), but it may never stand
# in for one. A release that is not recorded is not installed.
verify_gh_asset() {
  local tmpdir="$1" file="$2" version="$3" arch="$4" asset="$5"
  local actual expected upstream
  if ! actual="$(file_sha256 "$file")"; then
    log "ERROR: could not compute the sha256 of $asset"
    return 1
  fi

  expected="$(gh_expected_sha256 "$version" "$arch" || true)"

  if [ -z "$expected" ]; then
    log "ERROR: no pinned sha256 for the GitHub CLI ${version} ${arch} asset"
    log "ERROR: add the digest to gh_expected_sha256 for ${version} ${arch}"
    return 1
  fi

  upstream="$(upstream_sha256 "$tmpdir" "$version" "$asset" || true)"

  # The digest that decides.
  if [ "$actual" != "$expected" ]; then
    log "ERROR: sha256 mismatch for $asset"
    log "ERROR: expected $expected"
    log "ERROR: got      $actual"
    return 1
  fi

  # Corroboration: the upstream checksum file has to agree with the digest
  # the install just checked the download against. The file is the one
  # this repository recorded (see upstream_sha256), so a disagreement
  # means the recorded digest and the recorded file disagree with EACH
  # OTHER — one of the two is wrong, and the install stops.
  if [ -z "$upstream" ]; then
    log "WARN: could not use the upstream checksum file for $asset; the pinned digest was used"
  elif [ "$upstream" != "$expected" ]; then
    log "ERROR: the upstream checksum file disagrees with the recorded sha256 for $asset"
    log "ERROR: upstream $upstream, recorded $expected"
    return 1
  fi

  log "verified: $asset matches sha256 $actual"
}

# Download, verify, unpack and install the GitHub CLI release.
#
# Every step is checked and reported: the caller runs this under `if !`,
# which suspends `set -e` for the whole function, so a step that is not
# checked would fail silently. It returns non-zero instead of exiting, so
# the caller can put the caller's EXIT trap back before the script leaves.
install_gh_release() {
  local tmpdir="$1" version="$2" arch="$3" asset="$4" url="$5"
  local binary

  if ! curl -fsSL "$url" -o "$tmpdir/$asset"; then
    log "ERROR: the download of $asset failed"
    return 1
  fi

  if ! verify_gh_asset "$tmpdir" "$tmpdir/$asset" "$version" "$arch" "$asset"; then
    log "ERROR: refusing to install an unverified GitHub CLI"
    return 1
  fi

  if ! tar -xzf "$tmpdir/$asset" -C "$tmpdir"; then
    log "ERROR: could not unpack $asset"
    return 1
  fi

  # The upstream tarball holds one directory named after the asset, with
  # the binary in bin/. The path is spelled out: `find` would match any
  # archive member called "gh" and its traversal order is not guaranteed.
  binary="$tmpdir/gh_${version}_linux_${arch}/bin/gh"
  if [ ! -f "$binary" ]; then
    log "ERROR: $asset does not contain a gh binary"
    return 1
  fi

  # `install` creates the file as its invoking user and runs under sudo, so
  # the result is root-owned with the mode the runner needs. The target
  # directory is not guaranteed to exist on a bare host.
  #
  # A directory that is CREATED here is created with a mode that is not
  # group or other writable: the umask of the host would otherwise hand
  # out a dir that the check below refuses. The mode of a directory that
  # is already there is NOT touched — that dir belongs to the host, not
  # to the provisioning.
  if ! run_privileged mkdir -p -m 0755 "$(dirname "$GH_INSTALL_PATH")"; then
    log "ERROR: could not create $(dirname "$GH_INSTALL_PATH")"
    return 1
  fi
  # The dir that will hold the gh is CHECKED before the binary is staged
  # in it. The check is the only thing between a job and the ability to
  # replace the gh, and it is worth nothing after the fact: a dir that
  # was replaced between the check and the install is exactly the race
  # the check exists to close.
  if ! assert_gh_install_dir_protected; then
    log "ERROR: the GitHub CLI was not installed into a directory that is out of reach"
    return 1
  fi
  # The binary is staged next to the target and moved into place: `install`
  # truncates the destination in place, so replacing a gh that a running
  # job is executing fails with ETXTBSY. A rename(2) within one directory
  # is atomic, so a job sees either the old binary or the new one.
  local staged="${GH_INSTALL_PATH}.new.$$"
  if ! run_privileged install -m 0755 "$binary" "$staged"; then
    log "ERROR: could not install gh to $GH_INSTALL_PATH"
    run_privileged rm -f "$staged" || true
    return 1
  fi
  if ! run_privileged mv -f "$staged" "$GH_INSTALL_PATH"; then
    log "ERROR: could not install gh to $GH_INSTALL_PATH"
    run_privileged rm -f "$staged" || true
    return 1
  fi
}

# The directory that holds the host-wide GitHub CLI.
#
# It is the directory that decides whether the gh can be replaced: the
# write permission on the CONTAINING DIRECTORY is what unlink(2) and
# rename(2) look at, not the owner or the mode of the file inside it.
gh_install_dir() {
  local dir
  dir="$(dirname "$GH_INSTALL_PATH")"
  # The path is canonicalised so that it can be compared with another
  # path: a symlinked /usr/local/bin and the /usr/local it points at
  # are the same directory.
  if [ -d "$dir" ]; then
    (cd "$dir" 2>/dev/null && pwd -P) || printf '%s' "$dir"
  else
    printf '%s' "$dir"
  fi
}

# Whether a directory holds the GitHub CLI, or holds the directory that
# holds it.
#
# A directory that holds the gh must never be writable by the runner
# user: that user could delete the gh and put their own binary in its
# place. A directory that HOLDS the directory that holds the gh counts
# for the same reason — renaming the directory that holds the gh is
# enough.
dir_holds_the_gh() {
  local dir="$1" gh_dir
  [ -n "$dir" ] || return 1
  gh_dir="$(gh_install_dir)"
  if [ "$dir" = "$gh_dir" ]; then
    return 0
  fi
  if [ "${gh_dir#"$dir"/}" != "$gh_dir" ]; then
    return 0
  fi
  return 1
}

# Repair the mode and the owner of an installed gh.
#
# A fresh install gets them from `install -m 0755`. A gh that is already
# there may have been left behind by another job on this shared host with
# a mode that keeps the runner user out — and then every job on the host
# dies with "gh: command not found" while the provisioning reports
# success. The mode is repaired instead of assumed.
#
# The owner is repaired to root:root and NEVER to the user that runs the
# provisioning. /usr/local/bin/gh is a HOST-WIDE binary: every job on
# the host runs it. A gh the runner user owns is a gh that every job —
# and every pull request that runs on this host — can replace with
# something else, and the next job that calls `gh api` then runs the
# attacker's code with its own GITHUB_TOKEN. Handing a host-wide binary
# to the runner user is exactly the privilege the fleet does not grant.
#
# The repair stops at the BINARY. The directory that holds it is not
# repaired: it is checked (see assert_gh_install_dir_protected). Taking
# a directory over is not the provisioning's business — /usr/local/bin
# holds every host-wide binary on the host, and chowning it changes the
# ownership of a directory that belongs to somebody else.
repair_gh_binary() {
  if ! run_privileged chmod 0755 "$GH_INSTALL_PATH"; then
    log "ERROR: could not make $GH_INSTALL_PATH world executable"
    return 1
  fi
  if ! run_privileged chown root:root "$GH_INSTALL_PATH"; then
    log "ERROR: could not set the owner of $GH_INSTALL_PATH to root:root"
    log "ERROR: $GH_INSTALL_PATH is a host-wide binary and must not belong to a job user"
    return 1
  fi
}

# Install the GitHub CLI from the official release tarball.
#
# The GitHub-hosted images ship gh pre-installed; a bare self-hosted host
# does not, so a workflow that calls `gh api` dies with "gh: command not
# found" (exit code 127) while the runner itself looks healthy. The binary
# goes to /usr/local/bin/gh — root-owned, world executable and on the
# default GitHub Actions job PATH — so the non-root runner user can run it.
#
# An already-installed file is kept only when its bytes match the recorded
# binary digest. A version string is not a pin: obtaining one executes the
# file, and a script that prints the right version would then be chowned
# root:root and left in place. A symlink is not the installed binary either
# — sha256sum and chown follow it — so it is replaced, not repaired.
#
# The download is verified against the pinned sha256 before it is unpacked.
# Everything the host runs as root is worth verifying: this binary ends up
# world executable and shared by every job on the fleet.
ensure_gh_linux() {
  local version
  # The release is resolved first: a request for a release that is not
  # pinned is refused before anything else is looked at.
  if ! version="$(gh_version)"; then
    exit 1
  fi

  local arch asset url tmpdir
  # The asset for this host is resolved next: a host that has no gh
  # release for its architecture cannot be provisioned with one at all,
  # and that is worth saying before anything else is tried.
  arch="$(gh_linux_arch)" || {
    log "ERROR: no GitHub CLI asset for this architecture"
    exit 1
  }

  asset="gh_${version}_linux_${arch}.tar.gz"
  url="https://github.com/cli/cli/releases/download/v${version}/${asset}"

  # The install path is checked against the job PATH BEFORE anything is
  # downloaded. A gh outside the PATH a job gets is a gh no job can
  # find, and the check that notices that used to run at the very end —
  # after the release had been downloaded, unpacked and written.
  if ! assert_gh_install_path_on_job_path; then
    exit 1
  fi

  # An existing file is examined only after the directory is known to be
  # out of a job user's reach, and it is never executed. A version string
  # is not a pin. A symlink is not hashed or chowned: both follow it.
  if [ -e "$GH_INSTALL_PATH" ] || [ -L "$GH_INSTALL_PATH" ]; then
    if ! assert_gh_install_dir_protected; then
      exit 1
    fi
    if [ ! -L "$GH_INSTALL_PATH" ] && [ -f "$GH_INSTALL_PATH" ] &&
      gh_file_matches_pin "$version" "$arch"; then
      log "gh already present at $GH_INSTALL_PATH"
      if ! repair_gh_binary; then
        exit 1
      fi
      return
    fi
    log "replacing GitHub CLI at $GH_INSTALL_PATH (the on-disk file is not the pinned binary)"
  else
    log "installing GitHub CLI ${version} (${asset})"
  fi

  # The release has to be pinned, and that is decided BEFORE the
  # download: a release without a recorded digest has no anchor, and the
  # upstream checksum file cannot be one. The next maintainer action,
  # bumping GH_VERSION, must not silently swap the trust anchor for one
  # that arrives from the same origin as the tarball.
  if ! gh_expected_sha256 "$version" "$arch" >/dev/null; then
    log "ERROR: no pinned sha256 for the GitHub CLI ${version} ${arch} asset"
    log "ERROR: add the digest to gh_expected_sha256 for ${version} ${arch}"
    exit 1
  fi

  tmpdir="$(mktemp -d)"
  # The cleanup trap is removed again below. Only the trap this function
  # installed is removed: the one that was in place before is put back, so
  # sourcing this script does not swallow a cleanup the caller registered.
  local previous_exit_trap
  previous_exit_trap="$(trap -p EXIT)"
  # shellcheck disable=SC2064
  trap "rm -rf '$tmpdir'" EXIT
  # The whole install runs in one place and every step of it is checked.
  # A failure in the middle of it — a download that dies, an archive that
  # does not unpack — used to abort the script with `set -e` while THIS
  # trap was still installed, so the caller's trap was lost and the
  # cleanup it registered never ran.
  if ! install_gh_release "$tmpdir" "$version" "$arch" "$asset" "$url"; then
    # The temp dir goes first: a failing `rm` must not leave it behind
    # with no trap left to clean it.
    rm -rf "$tmpdir" || log "WARN: could not remove $tmpdir"
    # The caller's trap has to be back in place before the script leaves,
    # or the cleanup it registered never runs.
    restore_exit_trap "$previous_exit_trap"
    exit 1
  fi
  rm -rf "$tmpdir" || log "WARN: could not remove $tmpdir"
  restore_exit_trap "$previous_exit_trap"
  # The repair runs on every path, not only on the one that finds a gh
  # that is already there: the binary that was just installed is the
  # binary every job on the host runs, and it has to be root-owned and
  # world executable whatever the install path was.
  if ! repair_gh_binary; then
    exit 1
  fi
  log "gh $("$GH_INSTALL_PATH" --version | head -1) installed to $GH_INSTALL_PATH"
}

# Install the GitHub CLI on a macOS host.
#
# The pinned release install is Linux-only: the release tarball is built
# for linux, and the Homebrew prefix on macOS is not on the Linux job
# PATH. Homebrew ships the same CLI, so a macOS host gets it from there.
ensure_gh_macos() {
  if command -v gh >/dev/null 2>&1; then
    log "gh $(gh --version | head -1) already present"
    return
  fi
  if ! command -v brew >/dev/null 2>&1; then
    log "WARN: Homebrew not found; skipping the GitHub CLI install"
    return
  fi
  log "installing the GitHub CLI via Homebrew"
  # Homebrew refuses to run as root, so the documented root flow has to
  # install as the tool user.
  as_tool_user brew install gh
}

# The user-local tools are installed for the TOOL USER — the runner
# user — because a job runs as that user. When the provisioning runs as
# root, the tools land in the runner user's home and NOT in /root: a
# tool in a home the job cannot read is a tool the job cannot run.
ensure_uv() {
  resolve_tool_dirs
  if [ -x "$TOOL_HOME/.local/bin/uv" ]; then
    log "uv already present at $TOOL_HOME/.local/bin/uv"
    return
  fi
  log "installing uv via astral.sh (to $TOOL_HOME/.local/bin/uv)"
  as_tool_user bash -c 'curl -fsSL https://astral.sh/uv/install.sh | sh'
}

ensure_bun() {
  resolve_tool_dirs
  if [ -x "$TOOL_HOME/.bun/bin/bun" ]; then
    log "bun already present at $TOOL_HOME/.bun/bin/bun"
    return
  fi
  log "installing bun (needed to build archgate from source on linux/arm64)"
  as_tool_user bash -c 'curl -fsSL https://bun.sh/install | bash'
}

# Build the native archgate binary on linux/arm64 (no upstream release
# asset for that platform). On linux/x64 and macOS the official install
# script downloads a prebuilt platform binary to the same location.
ensure_archgate() {
  resolve_tool_dirs
  if [ -x "$TOOL_HOME/.archgate/bin/archgate" ]; then
    log "archgate $("$TOOL_HOME/.archgate/bin/archgate" --version 2>/dev/null) already present"
    return
  fi

  if [ "$uname_s" = "Linux" ] && [ "$uname_m" = "aarch64" ]; then
    log "building archgate $ARCHGATE_VERSION from source for linux/arm64"
    if ! command -v bun >/dev/null 2>&1 && [ ! -x "$TOOL_HOME/.bun/bin/bun" ]; then
      log "ERROR: bun is required to build archgate from source; rerun with bun available"
      exit 1
    fi
    if ! command -v git >/dev/null 2>&1; then
      log "ERROR: git is required to clone the archgate source"
      exit 1
    fi
    local tmpdir
    tmpdir="$(tool_tmpdir)" || {
      log "ERROR: could not create a temp dir for the archgate build"
      log "ERROR: the build runs as $TOOL_USER and needs a temp dir it can write to"
      exit 1
    }
    # The trap that was in place before is put back below, so a caller
    # that registered a cleanup of its own does not lose it: `trap - EXIT`
    # clears EVERY EXIT trap, and this function runs after the gh install
    # that already saves and restores it.
    local previous_exit_trap
    previous_exit_trap="$(trap -p EXIT)"
    # shellcheck disable=SC2064
    trap "rm -rf '$tmpdir'" EXIT
    if ! as_tool_user bash -c '
      set -e
      git clone --depth 1 --branch "$1" https://github.com/archgate/cli.git "$2/archgate" 2>&1 | tail -2
      cd "$2/archgate" && bun install >/dev/null
      mkdir -p "$3/.archgate/bin"
      bun build "$2/archgate/src/cli.ts" --compile --bytecode --outfile "$3/.archgate/bin/archgate"
    ' _ "$ARCHGATE_VERSION" "$tmpdir" "$TOOL_HOME"; then
      # The build failed. The caller's trap has to be back in place
      # before the script leaves, or the cleanup it registered never
      # runs — `trap - EXIT` clears EVERY EXIT trap.
      log "ERROR: could not build archgate $ARCHGATE_VERSION from source"
      restore_exit_trap "$previous_exit_trap"
      rm -rf "$tmpdir"
      exit 1
    fi
    log "archgate $("$TOOL_HOME/.archgate/bin/archgate" --version) installed to $TOOL_HOME/.archgate/bin/archgate"
    restore_exit_trap "$previous_exit_trap"
    rm -rf "$tmpdir"
  else
    log "installing archgate $ARCHGATE_VERSION via official install script (to $TOOL_HOME/.archgate/bin)"
    as_tool_user bash -c 'curl -fsSL https://cli.archgate.dev/install-unix | sh'
  fi
}

# Quote an argument for a shell command string.
quote_for_shell() {
  printf "'%s'" "${1//\'/\'\\\'\'}"
}

# Run a command as another user.
#
# Becoming another user needs privilege, and how that privilege is
# obtained depends on who runs the script:
#
#   - Root needs NO escalation command at all: setpriv, runuser and su
#     can all drop to another account. They are tried in that order and
#     sudo comes last — a root host does not always have sudo
#     installed, and requiring it would fail a perfectly good host over
#     a missing helper.
#   - Any other user can only reach another account through a real
#     sudo.
#
# A pinned escalation command that is not a sudo cannot switch users at
# all: it runs the command as the INVOKING user. For the check that
# verifies the runner user that is the worst possible outcome — root
# bypasses the mode bits, so the check would pass for a binary the
# runner user cannot execute. That case is refused with exit code 2
# instead of quietly running the command as the wrong user.
#
# Exit codes: 0 the command ran, 1 it ran and failed, 2 the user could
# not be switched to at all.
as_user() {
  local user="$1"
  shift
  local invoking
  invoking="$(id -un)"
  if [ "$invoking" = "$user" ]; then
    "$@"
    return
  fi

  if [ "$(id -u)" -eq 0 ]; then
    # A pinned escalation command is honoured even for root: an operator
    # who set $PROVISION_SUDO expects it to be the way the script
    # reaches another account. The default is empty on a root host —
    # root needs no escalation — and a command that is not a sudo
    # cannot switch users at all, so only a real sudo is used here.
    if [ -n "$PROVISION_SUDO" ] && is_sudo "$PROVISION_SUDO"; then
      if [ "$NON_INTERACTIVE" = "1" ]; then
        "$PROVISION_SUDO" -n -u "$user" -- "$@"
      else
        "$PROVISION_SUDO" -u "$user" -- "$@"
      fi
      return $?
    fi
    # Root needs NO escalation command at all: setpriv, runuser and su
    # can all drop to another account. They are tried in that order and
    # sudo comes last — a root host does not always have sudo
    # installed, and requiring it would fail a perfectly good host over
    # a missing helper.
    #
    # A failed SWITCH is reported as 2 and not as the status of the
    # command: the command never ran, so its status says nothing about
    # what happened, and the caller has to be able to tell the two
    # apart.
    #
    # The probe that tells the two apart runs `env` and not `true`:
    # `true` is a shell builtin, and a switch helper EXECs the command
    # it was given, so a builtin is not something it can run.
    local status=0
    if command -v setpriv >/dev/null 2>&1; then
      setpriv --reuid="$user" --regid="$(id -g "$user")" --init-groups -- "$@" || status=$?
      if [ "$status" != "0" ] &&
        ! setpriv --reuid="$user" --regid="$(id -g "$user")" --init-groups -- env >/dev/null 2>&1; then
        log "ERROR: could not switch to $user"
        return 2
      fi
      return "$status"
    fi
    if command -v runuser >/dev/null 2>&1; then
      runuser -u "$user" -- "$@" || status=$?
      if [ "$status" != "0" ] && ! runuser -u "$user" -- env >/dev/null 2>&1; then
        log "ERROR: could not switch to $user"
        return 2
      fi
      return "$status"
    fi
    if command -v su >/dev/null 2>&1; then
      # su takes the command as ONE string, so the arguments are quoted
      # into it.
      local quoted="" arg
      for arg in "$@"; do
        quoted="$quoted $(quote_for_shell "$arg")"
      done
      su -s /bin/sh -c "exec$quoted" "$user" || status=$?
      if [ "$status" != "0" ] && ! su -s /bin/sh -c "exec env" "$user" >/dev/null 2>&1; then
        log "ERROR: could not switch to $user"
        return 2
      fi
      return "$status"
    fi
  fi

  local escalate="$PROVISION_SUDO"
  if [ -z "$escalate" ]; then
    escalate="sudo"
  fi
  if ! is_sudo "$escalate"; then
    log "ERROR: cannot run a command as $user"
    log "ERROR: '$escalate' is not a sudo and cannot switch users; the command would run as $invoking"
    return 2
  fi
  if [ "$NON_INTERACTIVE" = "1" ]; then
    "$escalate" -n -u "$user" -- "$@"
  else
    "$escalate" -u "$user" -- "$@"
  fi
}

# Run a command as the user the user-local tools belong to, with that
# user's home and the tool bin dirs on the PATH.
as_tool_user() {
  resolve_tool_dirs
  if [ "$TOOL_USER" = "$(id -un)" ]; then
    env HOME="$TOOL_HOME" PATH="$TOOL_PATH:$PATH" "$@"
    return
  fi
  as_user "$TOOL_USER" env HOME="$TOOL_HOME" PATH="$TOOL_PATH:$PATH" "$@"
}

# A temp dir the TOOL USER can write to.
#
# The installs that run as the tool user need a directory they own:
# mktemp -d makes one owned by the invoking user, and a root-owned 0700
# temp dir cannot be cloned into by the runner user.
tool_tmpdir() {
  local dir
  dir="$(mktemp -d)" || return 1
  if [ "$TOOL_USER" != "$(id -un)" ]; then
    if ! run_privileged chown "$TOOL_USER" "$dir"; then
      rm -rf "$dir"
      return 1
    fi
  fi
  printf '%s' "$dir"
}

# The runner homes on this host, one per line.
#
# The RUNNER_HOMES knob names them with absolute paths. The glob under
# $HOME is the macOS layout. The home-parent scan is the Linux fleet
# layout: a root-run provision has HOME=/root, and the runners live one
# level below /home. An explicitly empty RUNNER_HOME_PARENTS scans nothing.
#
# Both halves of the script use this list, so the runner home that is
# verified is the runner home whose .path file is written.
runner_home_dirs() {
  local dir parent seen=""
  for dir in "${RUNNER_HOME_LIST[@]:-}"; do
    [ -n "$dir" ] || continue
    [ -d "$dir" ] || continue
    case "$seen" in
      *"|$dir|"*) continue ;;
    esac
    seen="$seen|$dir|"
    printf '%s\n' "$dir"
  done
  for dir in "$HOME"/actions-runner*; do
    [ -d "$dir" ] || continue
    case "$seen" in
      *"|$dir|"*) continue ;;
    esac
    seen="$seen|$dir|"
    printf '%s\n' "$dir"
  done
  while IFS= read -r parent; do
    [ -n "$parent" ] || continue
    for dir in "$parent"/*/actions-runner*; do
      [ -d "$dir" ] || continue
      case "$seen" in
        *"|$dir|"*) continue ;;
      esac
      seen="$seen|$dir|"
      printf '%s\n' "$dir"
    done
  done < <(home_parents)
}

# The owners of the runner homes on this host, one per line.
#
# Every existing home is looked at: two runner homes on one host can
# belong to different users, and verifying only the first one would
# leave the second unchecked.
runner_home_owners() {
  local runner_dir owner
  while IFS= read -r runner_dir; do
    [ -n "$runner_dir" ] || continue
    owner="$(stat -c '%U' "$runner_dir" 2>/dev/null || true)"
    if [ -z "$owner" ]; then
      owner="$(stat -f '%Su' "$runner_dir" 2>/dev/null || true)"
    fi
    # An id with no passwd entry comes back as UNKNOWN; that is not a user
    # to run a job as.
    if [ -n "$owner" ] && [ "$owner" != "UNKNOWN" ]; then
      printf '%s\n' "$owner"
    fi
  done < <(runner_home_dirs)
}

# The user the runner service runs as. It is the owner of the runner home
# — never hardcoded, the same way install-disk-cleanup.sh does it — and
# never derived from $HOME either: provisioning runs as root, and root's
# home holds no runner directory, so a $HOME-based lookup always fell
# back to root and the "the runner user can run gh" check verified root
# instead of the user a job actually runs as.
#
# A runner home owned by root is NOT a runner user: root bypasses the
# mode bits, so a check that ran as root would pass for a binary the
# runner user cannot execute. Such a home is skipped here, and the check
# that uses the owner skips it too — it falls back to the
# world-executable proof when no other runner home is left.
runner_user() {
  local owner
  while IFS= read -r owner; do
    [ -n "$owner" ] || continue
    [ "$(id -u "$owner" 2>/dev/null || true)" = "0" ] && continue
    printf '%s' "$owner"
    return 0
  done < <(runner_home_owners)
  return 1
}

# The home directory of a user, from the passwd database.
#
# The shell expands ~user through the same database, so this works on
# Linux and on macOS — macOS has no getent. An unknown user leaves the
# tilde unexpanded, which is how the failure is detected.
user_home() {
  local user="$1" home=""
  # A user name is a passwd entry, not a shell fragment: only the
  # characters a passwd name can hold are accepted.
  case "$user" in
    "" | - | + | *[!A-Za-z0-9._-]*) return 1 ;;
  esac
  home="$(eval "printf '%s' ~${user}" 2>/dev/null || true)"
  case "$home" in
    "" | "~" | "~"*) return 1 ;;
  esac
  printf '%s' "$home"
}

# Whether a file can be executed by EVERY user on the host. The "other"
# execute bit is what says that: the owner and the group bits say
# nothing about the other users.
world_executable() {
  local file="$1" mode other
  mode="$(path_mode "$file")"
  [ -n "$mode" ] || return 1
  other="${mode: -1}"
  case "$other" in
    1 | 3 | 5 | 7) return 0 ;;
    *) return 1 ;;
  esac
}

# The mode of a path, in octal. GNU stat prints it with -c, BSD stat
# with -f; the last three digits are the permission bits.
path_mode() {
  local mode
  mode="$(stat -c '%a' "$1" 2>/dev/null || true)"
  if [ -z "$mode" ]; then
    mode="$(stat -f '%Lp' "$1" 2>/dev/null || true)"
  fi
  printf '%s' "$mode"
}

# The user that owns a path.
path_owner() {
  local owner
  owner="$(stat -c '%U' "$1" 2>/dev/null || true)"
  if [ -z "$owner" ]; then
    owner="$(stat -f '%Su' "$1" 2>/dev/null || true)"
  fi
  printf '%s' "$owner"
}

# Assert that the runner user cannot REPLACE the GitHub CLI.
#
# The binary is root-owned and world executable, and that is not enough:
# unlink(2) and rename(2) are governed by the write permission on the
# CONTAINING DIRECTORY, not by the owner or the mode of the file inside
# it. A root-owned /usr/local/bin/gh inside a runner-owned /usr/local/bin
# can be deleted and replaced by the runner user — and the runner user
# is the identity that executes whatever a pull request asks for — so
# the next job that calls `gh api` runs the attacker's code with the
# job's own GITHUB_TOKEN.
#
# The invariant is therefore about the DIRECTORY that holds the gh:
#   - it belongs to root, and
#   - it is not group or other writable.
#
# Together those two say that only root may replace the gh: the owner
# of the dir is the only account that may write it, and root is that
# account. The check does not have to know which user the jobs run as —
# a dir that only root can write is out of reach of every other account
# on the host, the runner user included.
#
# The check reads the mode and the owner of that directory instead of
# trying a write as the runner user: a write only answers the question
# for the user the script can become, and the whole point is the users
# it CANNOT become.
assert_gh_install_dir_protected() {
  local dir mode owner group_bit other_bit
  dir="$(gh_install_dir)"
  mode="$(path_mode "$dir")"
  if [ -z "$mode" ]; then
    log "ERROR: could not read the mode of $dir"
    log "ERROR: the directory that holds $GH_INSTALL_PATH could not be checked"
    return 1
  fi
  mode="${mode: -3}"

  # The group and the other bits: a dir that a group or every user on
  # the host can write is a dir the gh can be replaced in, no matter who
  # the runner user is.
  group_bit="${mode:1:1}"
  other_bit="${mode:2:1}"
  case "$group_bit" in
    2 | 3 | 6 | 7)
      log "ERROR: $dir is group writable"
      log "ERROR: a member of the group could replace $GH_INSTALL_PATH"
      return 1
      ;;
  esac
  case "$other_bit" in
    2 | 3 | 6 | 7)
      log "ERROR: $dir is writable by every user on this host"
      log "ERROR: any of them could replace $GH_INSTALL_PATH"
      return 1
      ;;
  esac

  # The owner: the dir has to belong to root. A dir that belongs to
  # somebody else is a dir that somebody else can write in — the owner
  # of a directory always can — and that somebody is a job user on this
  # host.
  owner="$(path_owner "$dir")"
  if [ "$owner" != "root" ]; then
    log "ERROR: $dir is not owned by root"
    log "ERROR: only root may replace $GH_INSTALL_PATH"
    log "ERROR: point GH_INSTALL_PATH at a directory that belongs to root, or hand this one over to root by hand"
    return 1
  fi

  log "verified: $dir is owned by root and is not writable by anyone but root"
}

# Assert that the directory that holds the gh is on the PATH a job gets.
#
# A gh outside that PATH is a gh no job can find: the jobs die with
# "gh: command not found" (exit code 127) while the runner itself looks
# healthy. The check runs BEFORE the download, so a host that is pointed
# at the wrong path fails immediately instead of after a release has been
# fetched, unpacked and written.
assert_gh_install_path_on_job_path() {
  local gh_dir
  gh_dir="$(dirname "$GH_INSTALL_PATH")"
  case ":${DEFAULT_JOB_PATH}:" in
    *":${gh_dir}:"*) return 0 ;;
  esac
  log "ERROR: $gh_dir is not on the default job PATH"
  log "ERROR: jobs that call 'gh api' would fail with 'gh: command not found'"
  log "ERROR: the default job PATH is $DEFAULT_JOB_PATH"
  return 1
}

# Assert that the runner user can really run gh. The binary is root-owned
# and world executable by construction, but the job still dies with "gh:
# command not found" when the PATH that carries it is not the one the job
# gets — so the check runs the installed binary under the default job
# PATH, as the runner user.
#
# The check runs as that user and not as root: root bypasses the mode
# bits, so a root-only check would pass for a binary the runner user
# cannot execute.
verify_gh_runnable() {
  if [ "$uname_s" != "Linux" ]; then
    verify_gh_runnable_macos
    return
  fi
  # The gh has to be on the PATH a job gets, and the gh that PATH
  # resolves has to be the one this script installed: an older gh
  # somewhere else on the PATH says nothing about the install — and the
  # job would run that other one.
  if ! assert_gh_install_path_on_job_path; then
    exit 1
  fi
  local job_gh
  if ! job_gh="$(job_path_gh)"; then
    log "ERROR: no gh on the default job PATH"
    log "ERROR: jobs that call 'gh api' would fail with 'gh: command not found'"
    exit 1
  fi
  if [ "$job_gh" != "$GH_INSTALL_PATH" ]; then
    log "ERROR: the default job PATH resolves gh to $job_gh, not to $GH_INSTALL_PATH"
    log "ERROR: jobs that call 'gh api' would run a different GitHub CLI"
    exit 1
  fi

  local owners user status checked=0
  owners="$(runner_home_owners)"
  while IFS= read -r user; do
    [ -n "$user" ] || continue
    if [ "$(id -u "$user" 2>/dev/null || true)" = "0" ]; then
      # A runner home owned by root is NOT a runner user: root bypasses
      # the mode bits, so a check that runs as root passes for a binary
      # the runner user cannot execute. The home is skipped here, the
      # same way runner_user skips it — the two have to agree, or one
      # of them says the host has a runner user and the other says it
      # does not.
      log "WARN: a runner home is owned by root ($user) — that home was skipped"
      continue
    fi
    checked=1
    # The gh is run BY NAME under the job PATH, not by its absolute
    # path: what the jobs do is `gh api`, and the name has to resolve on
    # the PATH they get. Running the absolute path would prove that the
    # file is executable and nothing about the PATH.
    if as_user "$user" env PATH="$DEFAULT_JOB_PATH" gh --version >/dev/null 2>&1; then
      log "verified: $user can run gh on the default job PATH"
    else
      status=$?
      if [ "$status" = "2" ]; then
        log "ERROR: could not verify that $user can run gh"
        log "ERROR: the check could not run as $user, so nothing was verified"
      else
        log "ERROR: $user cannot run gh"
        log "ERROR: jobs that call 'gh api' would fail with 'gh: command not found'"
      fi
      exit 1
    fi
  done <<<"$owners"

  if [ "$checked" = "0" ]; then
    # No runner home on this host — or only one that belongs to root:
    # the runner user cannot be determined, so the check cannot run as
    # that user. That is NOT a reason to fail a host that was just
    # provisioned — a host that is provisioned before the runner is
    # installed is a normal thing to be.
    #
    # The property is PROVEN the other way round instead of skipped: the
    # gh has to be executable by EVERY user on the host, because every
    # job on the host runs it, and the dir that holds it has to be
    # traversable by every user. Both are facts about the mode, and
    # together with the job PATH they say what the check for the runner
    # user would have said.
    log "no runner home found — the runner user check did not run"
    log "the install is verified for every user on this host instead"
    log "set RUNNER_HOMES to the runner home(s) on this host to have it verified as that user"
    if [ ! -x "$GH_INSTALL_PATH" ]; then
      log "ERROR: $GH_INSTALL_PATH is not there"
      exit 1
    fi
    verify_gh_for_every_user "$GH_INSTALL_PATH"
  fi
}

# The gh on the default job PATH, if there is one. A tarball install
# puts it in /usr/local/bin, and that dir is on every job's PATH.
job_path_gh() {
  local dir
  local IFS=:
  for dir in $DEFAULT_JOB_PATH; do
    if [ -x "$dir/gh" ]; then
      printf '%s' "$dir/gh"
      return 0
    fi
  done
  return 1
}

# Assert that the macOS jobs can find the GitHub CLI.
#
# On macOS the pinned release install is Linux-only: gh comes from
# Homebrew, and the Homebrew prefix is not on the default job PATH.
# The prefix is added to the runner's .path file, and this check says
# out loud whether the binary is there — a macOS host that reports
# success without a gh the job can run is the exact failure this check
# exists for.
# True when every runner home's .path file lists the Homebrew prefix.
# No runner home is not a failure: there is no file to check.
macos_prefix_is_on_runner_path() {
  local prefix="$1" runner_dir path_file saw=0
  while IFS= read -r runner_dir; do
    [ -n "$runner_dir" ] || continue
    saw=1
    path_file="$runner_dir/.path"
    if [ ! -f "$path_file" ] || ! path_file_has "$path_file" "$prefix/bin"; then
      log "ERROR: $prefix/bin is not in ${path_file:-the runner .path file}"
      log "ERROR: the runner service builds the job PATH from that file"
      return 1
    fi
  done < <(runner_home_dirs)
  if [ "$saw" -eq 0 ]; then
    return 0
  fi
  return 0
}

verify_gh_runnable_macos() {
  local prefix gh_path job_gh
  prefix="$(homebrew_prefix || true)"
  if [ -n "$prefix" ]; then
    gh_path="$prefix/bin/gh"
    if [ -x "$gh_path" ]; then
      resolve_tool_dirs
      case ":${TOOL_PATH}:" in
        *":${prefix}/bin:"*)
          # TOOL_PATH is filled by resolve_tool_dirs even when no .path
          # file was written. The jobs read the file, not this variable.
          if ! macos_prefix_is_on_runner_path "$prefix"; then
            exit 1
          fi
          log "verified: the GitHub CLI is at $gh_path and $prefix/bin is on the runner PATH"
          verify_gh_runs_as_the_runner_user "$gh_path"
          return
          ;;
      esac
    fi
  fi
  # The gh does not have to come from Homebrew: a gh on the default job
  # PATH is found by every job on the host, Homebrew or not.
  if job_gh="$(job_path_gh)"; then
    log "verified: the GitHub CLI is at $job_gh, on the default job PATH"
    # The same check as the Homebrew branch: the gh has to run as the
    # runner user, because that is the user the jobs run as. With no
    # runner user on the host the property is proved for every user
    # instead.
    verify_gh_runs_as_the_runner_user "$job_gh"
    return
  fi
  log "ERROR: no GitHub CLI found — not in the Homebrew prefix and not on the default job PATH"
  log "ERROR: jobs that call 'gh api' would fail with 'gh: command not found'"
  exit 1
}

# Run the GitHub CLI as the runner user and say out loud what happened.
#
# A gh the runner user cannot execute dies in every job with "gh:
# command not found" while the provisioning reports success, so the
# check runs the binary as that user.
#
# The check CANNOT always run as that user, and then it fails: a host
# without a way to switch accounts has not verified anything, and
# reporting success for an unverified install is the failure this
# check exists for.
#
# A host with NO runner user is a different case: the host was
# provisioned before its runner was installed, which is a normal thing
# to be. The property is proved the other way round instead of skipped
# — the gh has to be executable by every user on the host, because
# every job on the host runs it, and the dir that holds it has to be
# traversable by every user. Both are facts about the mode, and they
# say what the check for the runner user would have said.
verify_gh_runs_as_the_runner_user() {
  local gh_path="$1" runner status=0
  runner="$(runner_user || true)"
  if [ -z "$runner" ]; then
    log "no runner home found — the runner user check did not run"
    log "the install is verified for every user on this host instead"
    log "set RUNNER_HOMES to the runner home(s) on this host to have it verified as that user"
    verify_gh_for_every_user "$gh_path"
    return
  fi
  # The status is captured here and not after the if: the status of an
  # if statement is the status of the branch that ran, and a condition
  # that is false leaves it at zero.
  as_user "$runner" "$gh_path" --version >/dev/null 2>&1 || status=$?
  if [ "$status" = "0" ]; then
    log "verified: $runner can run the GitHub CLI at $gh_path"
    return
  fi
  if [ "$status" = "2" ]; then
    log "ERROR: could not verify that $runner can run $gh_path — the check could not run as $runner"
    log "ERROR: run the provisioning as root, or as a user that can become $runner"
    exit 1
  fi
  log "ERROR: $runner cannot run the GitHub CLI at $gh_path"
  log "ERROR: jobs that call 'gh api' would fail with 'gh: command not found'"
  exit 1
}

# Assert that EVERY user on the host can run the gh.
#
# Every job on the host runs the GitHub CLI, so a gh that only root can
# run is a gh that no job can run. The proof is the mode: the binary
# has to carry the execute bit for "other", and the dir that holds it
# has to be traversable by everybody. Both are facts about the mode, so
# the check does not have to know which user the jobs run as.
verify_gh_for_every_user() {
  local gh_path="$1" gh_dir
  gh_dir="$(dirname "$gh_path")"
  if ! world_executable "$gh_path"; then
    log "ERROR: $gh_path is not executable by every user on this host"
    log "ERROR: jobs that call 'gh api' would fail with 'gh: command not found'"
    exit 1
  fi
  if ! world_executable "$gh_dir"; then
    log "ERROR: $gh_dir is not traversable by every user on this host"
    log "ERROR: jobs that call 'gh api' would fail with 'gh: command not found'"
    exit 1
  fi
  log "verified: $gh_path is executable by every user on this host"
}

main() {
  parse_args "$@"

  # The tool dirs are resolved once, up front: every install and every
  # PATH update below works with the same account and the same home.
  resolve_tool_dirs

  case "$uname_s" in
    Linux)
      run_privileged apt-get update -y
      ensure_node_linux
      ensure_pipx_linux
      # The gh install checks the directory that holds it before it
      # writes the binary into it, and again when the gh is already
      # there: the gh has to be out of the reach of the runner user —
      # not only the binary, the directory that holds it.
      ensure_gh_linux
      ;;
    Darwin)
      ensure_node_macos
      ensure_pipx_macos
      ensure_gh_macos
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

  verify_gh_runnable

  log "host provisioning complete (uv, bun, archgate, gh on PATH)"
}

# Run the provisioning when the script is executed. When it is sourced — the
# tests do that to exercise the individual installers in isolation —
# nothing runs on its own.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
