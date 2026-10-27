#!/usr/bin/env bash
#
# install-disk-cleanup.sh — install the runner disk-cleanup routine on a
# self-hosted host and enable its timer.
#
# The Colima VM that hosts the Linux runners has a 19G root filesystem. When
# it filled up, both Linux runners went offline and jobs wedged in
# post-cleanup, starving the whole org. The routine that keeps the root
# filesystem from filling again is a small script plus two systemd units,
# driven by a 15-minute timer.
#
# This script is idempotent: it checks before it installs, so re-running it
# is safe and fast. It installs:
#
#   - /usr/local/bin/runner-disk-cleanup.sh      (from cleanup-host-disk.sh)
#   - /etc/systemd/system/runner-disk-cleanup.service
#   - /etc/systemd/system/runner-disk-cleanup.timer
#
# and enables the timer, then runs the routine once so a fresh install
# reclaims space immediately instead of waiting for the first tick.
#
# The script is installed under the name the systemd unit references
# (runner-disk-cleanup.sh); cleanup-host-disk.sh is the name it carries in
# this repository.
#
# It NEVER stops the runner services. A routine install must not take a
# runner offline: the actions.runner.* units are left running untouched, and
# only the cleanup timer is enabled. (The one-off bind-mount install did stop
# them; that was not this script's job.)
#
# The runner's .path file is updated the same way scripts/provision-host.sh
# does it — the install directory is prepended so the runner service can
# resolve the routine by name.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_SCRIPT="$SCRIPT_DIR/cleanup-host-disk.sh"
INSTALL_PATH="${INSTALL_PATH:-/usr/local/bin/runner-disk-cleanup.sh}"
UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"
SERVICE_UNIT="$UNIT_DIR/runner-disk-cleanup.service"
TIMER_UNIT="$UNIT_DIR/runner-disk-cleanup.timer"

# The runner directories whose .path file is updated. Same knob as the
# cleanup routine itself, so an operator points both at the same place.
RUNNER_HOMES="${RUNNER_HOMES:-/home/redlexgilgamesh.guest/actions-runner-linux /home/redlexgilgamesh.guest/actions-runner-linux-2}"
read -r -a RUNNER_HOME_LIST <<<"$RUNNER_HOMES"

log() { printf '[install-disk-cleanup] %s\n' "$*"; }

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  SUDO="sudo"
fi

uname_s="$(uname -s)"

install_script() {
  if [ ! -f "$SOURCE_SCRIPT" ]; then
    log "ERROR: $SOURCE_SCRIPT not found (run this from the repository checkout)"
    exit 1
  fi
  $SUDO install -m 0755 "$SOURCE_SCRIPT" "$INSTALL_PATH"
  log "installed $INSTALL_PATH"
}

install_units() {
  $SUDO tee "$SERVICE_UNIT" >/dev/null <<UNIT
[Unit]
Description=Reclaim disk space used by the self-hosted GitHub Actions runners
After=local-fs.target

[Service]
Type=oneshot
ExecStart=${INSTALL_PATH}
Environment=HOME=/root
Nice=19
IOSchedulingClass=idle
# The sweep must never wedge the timer slot; a hung prune is worse than a
# skipped one, because the next pass would also queue behind it.
TimeoutStartSec=600
UNIT

  $SUDO tee "$TIMER_UNIT" >/dev/null <<'UNIT'
[Unit]
Description=Run the runner disk cleanup every 15 minutes

[Timer]
OnBootSec=3min
OnUnitActiveSec=15min
AccuracySec=1min
Unit=runner-disk-cleanup.service

[Install]
WantedBy=timers.target
UNIT

  log "installed $SERVICE_UNIT"
  log "installed $TIMER_UNIT"
}

enable_timer() {
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable --now runner-disk-cleanup.timer
  log "enabled runner-disk-cleanup.timer (every 15 minutes)"
}

run_once() {
  if $SUDO systemctl start runner-disk-cleanup.service; then
    log "ran the cleanup routine once"
  else
    log "ERROR: the cleanup routine failed; inspect it with:"
    log "  sudo journalctl -u runner-disk-cleanup.service -n 50"
    exit 1
  fi
}

# Prepend the install directory to each runner's .path file, exactly like
# scripts/provision-host.sh does for the toolchain directories. The file is
# only rewritten when the directory is missing from it.
ensure_runner_path() {
  local runner_dir path_file current bin_dir
  bin_dir="$(dirname "$INSTALL_PATH")"
  for runner_dir in "${RUNNER_HOME_LIST[@]:-}"; do
    [ -n "$runner_dir" ] || continue
    [ -d "$runner_dir" ] || continue
    path_file="$runner_dir/.path"
    [ -f "$path_file" ] || continue
    current="$(cat "$path_file" 2>/dev/null || true)"
    case ":${current}:" in
      *":${bin_dir}:"*) continue ;;
    esac
    printf '%s\n%s\n' "$bin_dir" "$current" > "$path_file"
    log "updated $path_file (added $bin_dir)"
  done
}

case "$uname_s" in
  Linux)
    install_script
    install_units
    enable_timer
    ensure_runner_path
    run_once
    log "disk cleanup installed (timer enabled, routine verified)"
    log "the runner services were left running"
    ;;
  Darwin)
    log "macOS: systemd timers are Linux-only, nothing installed"
    log "macOS: the Linux runners live in the Colima VM — run this script there"
    ;;
  *)
    log "ERROR: unsupported OS '$uname_s' (expected Linux or Darwin)"
    exit 1
    ;;
esac
