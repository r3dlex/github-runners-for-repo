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
# ABSOLUTE paths (the same knob install-disk-cleanup.sh uses). The
# provisioning fails when no runner home is found: a check that verified
# the invoking user would pass for a binary the runner user cannot run.
#
# What it ensures:
#   - Node.js + npm (Linux: NodeSource; macOS: Homebrew)
#   - An npm global prefix the runner user can write to (Linux only — the
#     default /usr prefix is root-owned and breaks `npm install -g` with
#     EACCES for the non-root runner user)
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
#     On macOS gh comes from Homebrew: the pinned release install is
#     Linux-only, and the Homebrew prefix is not on the Linux job PATH.
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
set -euo pipefail

NODE_MAJOR="${NODE_MAJOR:-22}"
NPM_PREFIX="${NPM_PREFIX:-/usr/local}"
ARCHGATE_VERSION="${ARCHGATE_VERSION:-v0.60.0}"
# Where the GitHub CLI is installed and which release is installed. The
# release is PINNED: the sha256 of the asset is recorded in this file and
# the download is verified against it, so the install is reproducible and
# a tampered tarball never reaches /usr/local/bin. A pinned release that
# has no recorded digest stops the install. Set GH_VERSION=latest to
# follow the newest release instead — a release that is named in advance
# has no recorded digest yet, so its digest comes from the upstream
# checksum file, which is a weaker guarantee because it arrives from the
# same origin as the tarball.
GH_INSTALL_PATH="${GH_INSTALL_PATH:-/usr/local/bin/gh}"
GH_VERSION="${GH_VERSION:-v2.103.3}"

# The runner homes on this host, as ABSOLUTE paths, separated by spaces.
# Same knob as install-disk-cleanup.sh uses, so an operator points both
# at the same place.
#
# The runner user is derived from the owner of one of these, and never
# from $HOME: provisioning runs as root, and root's home holds no runner
# directory, so a $HOME-based lookup falls back to root — and a check
# that ran as root passes for a binary the runner user cannot execute.
RUNNER_HOMES="${RUNNER_HOMES:-/home/redlexgilgamesh.guest/actions-runner-linux /home/redlexgilgamesh.guest/actions-runner-linux-2}"
read -r -a RUNNER_HOME_LIST <<<"$RUNNER_HOMES"

log() { printf '[provision] %s\n' "$*"; }

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
# behind, so `curl … | $SUDO -E bash -` became `-E bash -` and the shell
# reported "-E: command not found" every time the script ran as root —
# which is how `gh-runners provision` is normally run.
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
      printf '\n# runner-provision PATH\nexport PATH="%s:$PATH"\n' \
        "$(IFS=:; echo "${USER_BIN_DIRS[*]}")" >> "$rc_file"
    fi
  done
}

ensure_user_path_in_runner() {
  # The actions-runner service uses the .path file in the runner dir to
  # set PATH. Prepend our user-local bin dirs so the service can find
  # uv, bun, and archgate.
  local runner_dir
  for runner_dir in "$HOME/actions-runner"*; do
    [ -d "$runner_dir" ] || continue
    local path_file="$runner_dir/.path"
    [ -f "$path_file" ] || continue
    local current
    current="$(cat "$path_file" 2>/dev/null || true)"
    local missing=()
    local d
    for d in "${USER_BIN_DIRS[@]}"; do
      case ":${current}:" in
        *":${d}:"*) ;;
        *) missing+=("$d") ;;
      esac
    done
    if [ "${#missing[@]}" -gt 0 ]; then
      local prepend
      prepend="$(IFS=:; echo "${missing[*]}")"
      printf '%s\n%s\n' "$prepend" "$current" > "$path_file"
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
  local user group
  user="$(id -un)"
  group="$(id -gn)"
  run_privileged mkdir -p "${NPM_PREFIX}/lib/node_modules" "${NPM_PREFIX}/bin"
  run_privileged chown -R "${user}:${group}" "${NPM_PREFIX}/lib/node_modules" "${NPM_PREFIX}/bin"
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
    brew install pipx
    pipx ensurepath >/dev/null 2>&1 || true
  else
    log "WARN: Homebrew not found; skipping pipx"
  fi
}

# The GitHub CLI release to install. The version is pinned by default; the
# release API is only consulted when GH_VERSION asks for "latest".
#
# A failed lookup returns non-zero instead of exiting: the caller decides
# whether an unreachable release API is fatal (no gh installed) or not (a
# usable gh is already there and must be kept).
gh_version() {
  if [ -n "$GH_VERSION" ] && [ "$GH_VERSION" != "latest" ]; then
    printf '%s' "${GH_VERSION#v}"
    return
  fi
  local tag
  # A failed lookup must not abort the script here: the empty tag is
  # reported with a message of its own below.
  tag="$(curl -fsSL https://api.github.com/repos/cli/cli/releases/latest \
    | sed -n 's/.*"tag_name": *"v\{0,1\}\([^"]*\)".*/\1/p' \
    | head -1 || true)"
  if [ -z "$tag" ]; then
    return 1
  fi
  printf '%s' "$tag"
}

# The numeric core of a version string. "2.103.3" comes out of "v2.103.3",
# "2.103.3" and "gh version 2.103.3 (2026-01-01)" alike, so the installed
# binary and the requested release can be compared.
version_core() {
  printf '%s' "${1:-}" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true
}

# The version of the gh binary that is already installed, empty when there
# is none or when it cannot be run.
installed_gh_version() {
  [ -x "$GH_INSTALL_PATH" ] || return 0
  local out
  out="$("$GH_INSTALL_PATH" --version 2>/dev/null | head -1 || true)"
  version_core "$out"
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
# A version that is not listed here has no trusted digest. An explicitly
# pinned release then stops the install until its digest is added — that
# is the point of pinning. Only a "latest" request, which names no
# release in advance, may fall back to the upstream checksum file.
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
# same origin as the asset. Fails when the file cannot be fetched or has
# no entry for the asset.
upstream_sha256() {
  local tmpdir="$1" version="$2" asset="$3" sums candidate digest name
  sums="$tmpdir/checksums.txt"
  if ! curl -fsSL \
    "https://github.com/cli/cli/releases/download/v${version}/gh_${version}_checksums.txt" \
    -o "$sums"; then
    return 1
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
# $6 is the release that was ASKED for: "v2.103.3" or "latest". Only a
# "latest" request may fall back to the upstream checksum file. A pinned
# release that has no recorded digest stops the install, because that is
# what pinning it means — the next maintainer action, bumping GH_VERSION,
# must not silently swap the trust anchor for one that arrives from the
# same origin as the tarball.
#
# The sixth argument is optional. Without it the release is not known, so
# the upstream checksum file may not stand in for a missing recorded
# digest either: the check fails closed with the message below instead
# of aborting the script with an unbound variable.
verify_gh_asset() {
  local tmpdir="$1" file="$2" version="$3" arch="$4" asset="$5" requested="${6:-}"
  local actual expected upstream
  if ! actual="$(file_sha256 "$file")"; then
    log "ERROR: could not compute the sha256 of $asset"
    return 1
  fi

  expected="$(gh_expected_sha256 "$version" "$arch" || true)"

  if [ -z "$expected" ] && [ "$requested" != "latest" ]; then
    log "ERROR: no pinned sha256 for the GitHub CLI ${version} ${arch} asset"
    log "ERROR: add the digest to gh_expected_sha256 for ${version} ${arch}, or set GH_VERSION=latest to follow the upstream checksum file"
    return 1
  fi

  upstream="$(upstream_sha256 "$tmpdir" "$version" "$asset" || true)"

  if [ -z "$expected" ]; then
    # A "latest" request names no release in advance, so there is no
    # constant to check against. The upstream checksum file is then the
    # only anchor left, and it arrives from the same origin as the asset
    # — a weaker guarantee, so it is said out loud.
    if [ -z "$upstream" ]; then
      log "ERROR: no pinned sha256 for the GitHub CLI ${version} ${arch} asset and no upstream checksum for it"
      log "ERROR: add the digest to gh_expected_sha256, or pin GH_VERSION to a release that has one"
      return 1
    fi
    log "WARN: the GitHub CLI ${version} ${arch} release is not pinned; the digest comes from the upstream checksum file"
    expected="$upstream"
  fi

  # The digest that decides.
  if [ "$actual" != "$expected" ]; then
    log "ERROR: sha256 mismatch for $asset"
    log "ERROR: expected $expected"
    log "ERROR: got      $actual"
    return 1
  fi

  # Corroboration: the upstream checksum file has to agree with the digest
  # the install just checked the download against.
  if [ -z "$upstream" ]; then
    log "WARN: could not read the upstream checksum file for $asset; the pinned digest was used"
  elif [ "$upstream" != "$expected" ]; then
    log "ERROR: the upstream checksum file disagrees with the sha256 for $asset"
    log "ERROR: upstream $upstream, expected $expected"
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

  if ! verify_gh_asset "$tmpdir" "$tmpdir/$asset" "$version" "$arch" "$asset" "$GH_VERSION"; then
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
  if ! run_privileged mkdir -p "$(dirname "$GH_INSTALL_PATH")"; then
    log "ERROR: could not create $(dirname "$GH_INSTALL_PATH")"
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

# Repair the mode and the owner of an installed gh.
#
# A fresh install gets them from `install -m 0755`. A gh that is already
# there may have been left behind by another job on this shared host with
# a mode that keeps the runner user out — and then every job on the host
# dies with "gh: command not found" while the provisioning reports
# success. The mode is repaired instead of assumed.
repair_gh_install() {
  if ! run_privileged chmod 0755 "$GH_INSTALL_PATH"; then
    log "ERROR: could not make $GH_INSTALL_PATH world executable"
    return 1
  fi
  if ! run_privileged chown "$(id -un):$(id -gn)" "$GH_INSTALL_PATH"; then
    log "ERROR: could not set the owner of $GH_INSTALL_PATH"
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
# The installed binary is compared against the requested release: an gh that
# is already the requested version is left alone (the host may have been
# provisioned by hand), a different one is replaced.
#
# The download is verified against the pinned sha256 before it is unpacked.
# Everything the host runs as root is worth verifying: this binary ends up
# world executable and shared by every job on the fleet.
ensure_gh_linux() {
  local installed
  installed="$(installed_gh_version)"

  local version
  version="$(gh_version)" || version=""
  if [ -z "$version" ]; then
    if [ -n "$installed" ]; then
      # The release API is unreachable but a working gh is installed: keep
      # it rather than failing the provisioning of an otherwise good host.
      log "WARN: could not read the latest GitHub CLI release; keeping gh $installed at $GH_INSTALL_PATH"
      if ! repair_gh_install; then
        exit 1
      fi
      return
    fi
    log "ERROR: could not read the latest GitHub CLI release from the GitHub API"
    exit 1
  fi

  local arch asset url tmpdir
  arch="$(gh_linux_arch)" || {
    log "ERROR: no GitHub CLI asset for this architecture"
    exit 1
  }
  asset="gh_${version}_linux_${arch}.tar.gz"
  url="https://github.com/cli/cli/releases/download/v${version}/${asset}"

  if [ -n "$installed" ]; then
    if [ "$(version_core "$installed")" = "$(version_core "$version")" ]; then
      log "gh $installed already present at $GH_INSTALL_PATH"
      # The mode and the owner are repaired, not assumed: the version
      # being right says nothing about who may run the binary.
      if ! repair_gh_install; then
        exit 1
      fi
      return
    fi
    log "updating GitHub CLI ${installed} -> ${version} (${asset})"
  else
    log "installing GitHub CLI ${version} (${asset})"
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
  brew install gh
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
    # The trap that was in place before is put back below, so a caller
    # that registered a cleanup of its own does not lose it: `trap - EXIT`
    # clears EVERY EXIT trap, and this function runs after the gh install
    # that already saves and restores it.
    local previous_exit_trap
    previous_exit_trap="$(trap -p EXIT)"
    # shellcheck disable=SC2064
    trap "rm -rf '$tmpdir'" EXIT
    git clone --depth 1 --branch "$ARCHGATE_VERSION" \
      https://github.com/archgate/cli.git "$tmpdir/archgate" 2>&1 | tail -2
    ( cd "$tmpdir/archgate" && bun install >/dev/null )
    mkdir -p "$HOME/.archgate/bin"
    bun build "$tmpdir/archgate/src/cli.ts" --compile --bytecode \
      --outfile "$HOME/.archgate/bin/archgate"
    log "archgate $(~/.archgate/bin/archgate --version) installed to ~/.archgate/bin/archgate"
    restore_exit_trap "$previous_exit_trap"
    rm -rf "$tmpdir"
  else
    log "installing archgate $ARCHGATE_VERSION via official install script (to ~/.archgate/bin)"
    curl -fsSL https://cli.archgate.dev/install-unix | sh
  fi
}

# Run a command as another user.
#
# The escalation command is the pinned $SUDO, so a sandbox that pins it
# does not escalate at all. Only a real sudo can become another user; any
# other escalation command runs the command as the invoking user. Root
# still needs sudo for this — becoming another user is the one thing it
# cannot do on its own — and it is non-interactive so a missing password
# fails the install instead of hanging on a prompt.
as_user() {
  local user="$1"
  shift
  if [ "$(id -un)" = "$user" ]; then
    "$@"
    return
  fi
  local escalate="$PROVISION_SUDO"
  if [ -z "$escalate" ]; then
    escalate="sudo"
  fi
  if is_sudo "$escalate"; then
    if [ "$NON_INTERACTIVE" = "1" ]; then
      "$escalate" -n -u "$user" -- "$@"
    else
      "$escalate" -u "$user" -- "$@"
    fi
  else
    "$escalate" "$@"
  fi
}

# The user the runner service runs as. It is the owner of the runner home
# — never hardcoded, the same way install-disk-cleanup.sh does it — and
# never derived from $HOME either: provisioning runs as root, and root's
# home holds no runner directory, so a $HOME-based lookup always fell
# back to root and the "the runner user can run gh" check verified root
# instead of the user a job actually runs as.
#
# The runner home is named by the RUNNER_HOMES knob with absolute paths.
# When none of them exists the user cannot be determined, and this fails
# instead of falling back to the invoking user: a fallback would verify
# root and pass vacuously.
runner_user() {
  local runner_dir owner
  for runner_dir in "${RUNNER_HOME_LIST[@]:-}"; do
    [ -n "$runner_dir" ] || continue
    [ -d "$runner_dir" ] || continue
    owner="$(stat -c '%U' "$runner_dir" 2>/dev/null || true)"
    if [ -z "$owner" ]; then
      owner="$(stat -f '%Su' "$runner_dir" 2>/dev/null || true)"
    fi
    # An id with no passwd entry comes back as UNKNOWN; that is not a user
    # to run a job as.
    if [ -n "$owner" ] && [ "$owner" != "UNKNOWN" ]; then
      printf '%s' "$owner"
      return 0
    fi
  done
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
    # The pinned release install is Linux-only; on macOS gh comes from
    # Homebrew, whose prefix is not on the Linux job PATH.
    return
  fi
  local user
  if ! user="$(runner_user)"; then
    log "ERROR: no runner home found — cannot verify the runner user"
    log "ERROR: set RUNNER_HOMES to the runner home(s) on this host"
    log "ERROR: a check that ran as the invoking user would pass for a gh the runner user cannot run"
    exit 1
  fi
  # The binary that is verified is the one this script installed, not
  # whatever `gh` the PATH happens to resolve to: an older gh somewhere
  # else on the PATH says nothing about the install.
  local gh_dir
  gh_dir="$(dirname "$GH_INSTALL_PATH")"
  case ":${DEFAULT_JOB_PATH}:" in
    *":${gh_dir}:"*) ;;
    *)
      log "ERROR: $gh_dir is not on the default job PATH"
      log "ERROR: jobs that call 'gh api' would fail with 'gh: command not found'"
      exit 1
      ;;
  esac
  if as_user "$user" env PATH="$DEFAULT_JOB_PATH" "$GH_INSTALL_PATH" --version >/dev/null 2>&1; then
    log "verified: $user can run gh at $GH_INSTALL_PATH"
  else
    log "ERROR: $user cannot run gh at $GH_INSTALL_PATH"
    log "ERROR: jobs that call 'gh api' would fail with 'gh: command not found'"
    exit 1
  fi
}

main() {
  parse_args "$@"

  case "$uname_s" in
    Linux)
      run_privileged apt-get update -y
      ensure_node_linux
      ensure_pipx_linux
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
