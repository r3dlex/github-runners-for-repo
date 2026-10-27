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
# ## Ownership
#
# The runner service runs as a non-root user. Anything this script leaves
# under a runner home must be owned by that user: a root-owned _work tree
# makes every job fail instantly with "Access to the path ... is denied"
# while the runner itself looks perfectly healthy (assigned, zero steps
# executed). The owning user is DERIVED from the runner home — never
# hardcoded — and the install verifies at the end that the runner user can
# actually write into the work directory. If it cannot, the install fails
# closed instead of reporting success.
#
# The runner's .path file is updated the same way scripts/provision-host.sh
# does it — the install directory is prepended so the runner service can
# resolve the routine by name.
#
set -euo pipefail

# Umask 022 so the unit files this script writes are 0644 (systemd refuses
# world-writable unit files) and any directory it creates is 0755. The
# runner-owned trees are additionally chmod'ed explicitly below.
umask 022

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

# The user that owns a directory. GNU stat and BSD stat spell this
# differently, and the installer runs on both (systemd on Linux, launchd on
# macOS), so both are tried. Never hardcoded: the runner service runs as
# the owner of its runner home.
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

# The group that owns a directory, same GNU/BSD split as dir_owner.
dir_group() {
  local dir="$1" group numeric
  group="$(stat -c '%G' "$dir" 2>/dev/null || true)"
  if [ -z "$group" ]; then
    group="$(stat -f '%Sg' "$dir" 2>/dev/null || true)"
  fi
  if [ -z "$group" ] || [ "$group" = "UNKNOWN" ]; then
    numeric="$(stat -c '%g' "$dir" 2>/dev/null || true)"
    if [ -z "$numeric" ]; then
      numeric="$(stat -f '%g' "$dir" 2>/dev/null || true)"
    fi
    [ -n "$numeric" ] && group="$numeric"
  fi
  printf '%s' "$group"
}

# Run a command as another user. Root uses sudo directly; a non-root
# installer uses non-interactive sudo so a missing password fails the
# install instead of hanging on a prompt.
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

# Hand the runner home back to the user that owns it.
#
# The runner service runs as a non-root user and writes into _work, _diag
# and the checkout directories below them. When a root-run install creates
# or copies any of those, they end up root-owned and every job fails with
# "Access to the path ... is denied" while the runner looks healthy. So the
# whole runner home — including the work tree, which on the Colima VM is a
# bind-mount of the data disk — is chowned recursively to the runner user,
# and every file is made writable by its owner.
fix_runner_ownership() {
  local runner_dir owner group
  for runner_dir in "${RUNNER_HOME_LIST[@]:-}"; do
    [ -n "$runner_dir" ] || continue
    [ -d "$runner_dir" ] || continue

    owner="$(dir_owner "$runner_dir")"
    if [ -z "$owner" ]; then
      log "WARN: cannot determine the owner of $runner_dir; leaving it untouched"
      continue
    fi
    # Read the group before the chown — the chown changes it.
    group="$(dir_group "$runner_dir")"
    if [ -z "$group" ]; then
      group="$(id -gn "$owner" 2>/dev/null || printf '%s' "$owner")"
    fi

    # The whole tree, recursively: a root-owned subdirectory is exactly what
    # breaks the job, and it hides behind a healthy-looking runner.
    if ! $SUDO chown -R "$owner:$group" "$runner_dir"; then
      log "ERROR: could not hand $runner_dir to $owner:$group"
      log "ERROR: the runner service would fail every job with a permission error"
      exit 1
    fi
    # u+rwX: read/write for the owner, and the execute bit on directories
    # (and on files that already had it) so the runner can traverse them.
    $SUDO chmod -R u+rwX "$runner_dir"

    # These are created by the runner itself, but it must be able to: create
    # them AS the runner user so they are owned correctly from the start
    # instead of being root-owned leftovers.
    as_user "$owner" mkdir -p "$runner_dir/_work/_tool" \
                               "$runner_dir/_work/_temp" \
                               "$runner_dir/_work/_actions" 2>/dev/null || true

    log "runner home $runner_dir owned by $owner:$group"
  done
}

# Assert that the runner user can really write into the work directory.
# This is the check that catches the root-owned-work-tree failure: the
# runner is assigned, the job dies in three seconds with zero steps, and
# nothing in the runner's own health check reports a problem.
verify_runner_writable() {
  local runner_dir owner rc=0
  for runner_dir in "${RUNNER_HOME_LIST[@]:-}"; do
    [ -n "$runner_dir" ] || continue
    [ -d "$runner_dir" ] || continue

    owner="$(dir_owner "$runner_dir")"
    if [ -z "$owner" ]; then
      log "WARN: cannot determine the owner of $runner_dir; skipping the write check"
      continue
    fi

    # The check runs in a separate shell as the runner user; the single
    # quotes are deliberate, $1 is expanded by that shell at runtime.
    # shellcheck disable=SC2016
    if as_user "$owner" sh -c '
        set -e
        work="$1"
        test -w "$work"
        mkdir -p "$work/_tool"
        touch "$work/.wtest"
        rm -f "$work/.wtest"
        touch "$work/_tool/.wtest"
        rm -f "$work/_tool/.wtest"
      ' sh "$runner_dir/_work"; then
      log "verified: $owner can write $runner_dir/_work"
    else
      log "ERROR: $owner cannot write $runner_dir/_work"
      log "ERROR: the runner service would fail every job with a permission error"
      rc=1
    fi
  done
  return "$rc"
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
  local runner_dir path_file current bin_dir owner
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
    # This file lives under the runner home: hand it back to the runner
    # user so a root-run install does not leave a root-owned artefact
    # behind.
    owner="$(dir_owner "$runner_dir")"
    if [ -n "$owner" ]; then
      $SUDO chown "$owner" "$path_file" 2>/dev/null || true
    fi
    log "updated $path_file (added $bin_dir)"
  done
}

case "$uname_s" in
  Linux)
    install_script
    install_units
    fix_runner_ownership
    # Fail closed: a runner that cannot write its work directory is worse
    # than no install at all, so this must not report success.
    if ! verify_runner_writable; then
      log "ERROR: install aborted — the runner user cannot write its work directory"
      exit 1
    fi
    ensure_runner_path
    enable_timer
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
