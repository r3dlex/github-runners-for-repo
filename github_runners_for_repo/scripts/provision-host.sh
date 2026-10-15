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
