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
#
set -euo pipefail

NODE_MAJOR="${NODE_MAJOR:-22}"
NPM_PREFIX="${NPM_PREFIX:-/usr/local}"

log() { printf '[provision] %s\n' "$*"; }

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  SUDO="sudo"
fi

uname_s="$(uname -s)"

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

log "host provisioning complete"
