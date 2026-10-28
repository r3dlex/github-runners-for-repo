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

# --force is required to overwrite a host copy of the routine that differs
# from the repo copy. The host copy is the one that has been proven under
# real disk pressure, so replacing it is an explicit decision, never a
# side effect of re-running the installer.
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    -h | --help)
      log "usage: $0 [--force]"
      log "  --force  overwrite a host copy of the routine that differs from"
      log "           the repo copy (the old one is backed up next to it)"
      exit 0
      ;;
    *)
      log "ERROR: unknown argument '$arg' (see --help)"
      exit 1
      ;;
  esac
done

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

  if [ -f "$INSTALL_PATH" ]; then
    if cmp -s "$SOURCE_SCRIPT" "$INSTALL_PATH"; then
      log "$INSTALL_PATH already matches the repo copy"
    else
      # The host copy is the one proven under real disk pressure, and it is
      # NOT the same file as the repo copy: the repo copy parameterises the
      # host-specific paths and adds the liveness and dry-run guards. So
      # replacing it is a decision the operator has to make, and the old copy
      # is kept next to the new one so it can be put back.
      if [ "$FORCE" != "1" ]; then
        log "ERROR: $INSTALL_PATH differs from $SOURCE_SCRIPT."
        log "ERROR: the host copy is the one proven under real disk pressure."
        log "ERROR: back it up and re-run with --force to overwrite it:"
        log "ERROR:   $0 --force"
        exit 1
      fi
      if $SUDO cp -p -- "$INSTALL_PATH" "$INSTALL_PATH.bak" 2>/dev/null; then
        log "backed up the previous $INSTALL_PATH to $INSTALL_PATH.bak"
      else
        log "ERROR: could not back up $INSTALL_PATH to $INSTALL_PATH.bak; refusing to overwrite it"
        exit 1
      fi
    fi
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
  local runner_dir owner group cred
  for runner_dir in "${RUNNER_HOME_LIST[@]:-}"; do
    [ -n "$runner_dir" ] || continue
    if [ ! -d "$runner_dir" ]; then
      log "WARN: runner home $runner_dir does not exist; skipping it"
      continue
    fi

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

    # The credential files must not be group- or world-writable. "u+rwX"
    # never removes group/other write, so it leaves a 0664 .credentials
    # exactly as it found it — and any other non-root user on the host can
    # then read (and rewrite) the runner's credentials.
    for cred in "$runner_dir/.credentials" "$runner_dir/.credentials_rsaparams"; do
      [ -f "$cred" ] || continue
      if ! $SUDO chmod 0600 "$cred"; then
        log "WARN: could not restrict the permissions of $cred"
      fi
    done

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
    if [ ! -d "$runner_dir" ]; then
      log "WARN: runner home $runner_dir does not exist; skipping the write check"
      continue
    fi

    owner="$(dir_owner "$runner_dir")"
    if [ -z "$owner" ]; then
      log "WARN: cannot determine the owner of $runner_dir; skipping the write check"
      continue
    fi

    # The check runs in a separate shell as the runner user; the single
    # quotes are deliberate, $1 is expanded by that shell at runtime.
    #
    # EVERY directory the runner writes into is probed, not just _work: a
    # root-owned _actions (where the actions are unpacked) or _diag (where
    # the worker logs go) produces exactly the same "assigned, zero steps"
    # failure as a root-owned _work, and a check that only looks at _work
    # reports success while the runner is still broken.
    # shellcheck disable=SC2016
    if as_user "$owner" sh -c '
        set -e
        home="$1"
        for target in "$home" "$home/_work" "$home/_work/_tool" "$home/_work/_temp" "$home/_work/_actions" "$home/_diag"; do
          mkdir -p "$target"
          probe="$target/.wtest"
          touch "$probe"
          rm -f "$probe"
        done
      ' sh "$runner_dir"; then
      log "verified: $owner can write $runner_dir and its work tree"
    else
      log "ERROR: $owner cannot write $runner_dir or its work tree"
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

# True when the .path file already lists the directory.
#
# The runner reads .path as a list of PATH entries, one per line, and this
# installer writes one directory per line. The membership test therefore has
# to be a LINE test: the old colon-delimited token match against the whole
# file never matched the format this installer writes, so the entry was
# prepended again on every single run (3 lines, then 4, then 5).
#
# Older writers (scripts/provision-host.sh) put several directories on one
# colon-joined line, so the colon form is checked as well — otherwise the
# first install after such a write would duplicate the entry once.
path_file_has() {
  local path_file="$1" bin_dir="$2"
  grep -Fxq -- "$bin_dir" "$path_file" 2>/dev/null && return 0
  case ":$(tr '\n' ':' <"$path_file" 2>/dev/null):" in
    *":${bin_dir}:"*) return 0 ;;
  esac
  return 1
}

# Prepend the install directory to each runner's .path file, exactly like
# scripts/provision-host.sh does for the toolchain directories. The file is
# only rewritten when the directory is missing from it.
#
# The write is atomic (write to a temporary file, then rename) and it runs
# AS THE RUNNER USER: the runner reads this file when it starts a job, so a
# truncated file or a root-owned one breaks the next job.
ensure_runner_path() {
  local runner_dir path_file current bin_dir owner tmp_file rc=0
  bin_dir="$(dirname "$INSTALL_PATH")"
  for runner_dir in "${RUNNER_HOME_LIST[@]:-}"; do
    [ -n "$runner_dir" ] || continue
    if [ ! -d "$runner_dir" ]; then
      log "WARN: runner home $runner_dir does not exist; skipping it"
      continue
    fi
    path_file="$runner_dir/.path"
    [ -f "$path_file" ] || continue
    path_file_has "$path_file" "$bin_dir" && continue

    owner="$(dir_owner "$runner_dir")"
    if [ -z "$owner" ]; then
      log "WARN: cannot determine the owner of $runner_dir; leaving $path_file untouched"
      rc=1
      continue
    fi

    current="$(cat "$path_file" 2>/dev/null || true)"
    tmp_file="$path_file.tmp.$$"
    # Written as the runner user, so the runner owns the result and no
    # root-owned artefact is left behind. The single quotes are deliberate:
    # $1..$3 are expanded by that shell at runtime.
    # shellcheck disable=SC2016
    if ! as_user "$owner" sh -c '
        printf "%s\n%s\n" "$1" "$2" > "$3"
      ' sh "$bin_dir" "$current" "$tmp_file"; then
      log "ERROR: could not write $tmp_file as $owner"
      rc=1
      continue
    fi
    # rename(2) within the same directory is atomic: the runner either sees
    # the old file or the new one, never a half-written one.
    if ! as_user "$owner" mv -f -- "$tmp_file" "$path_file"; then
      log "ERROR: could not replace $path_file with $tmp_file"
      rm -f -- "$tmp_file" 2>/dev/null || true
      rc=1
      continue
    fi
    log "updated $path_file (added $bin_dir)"
  done
  return "$rc"
}

# Run the install when the script is executed. When it is sourced — the
# sandbox tests do that to exercise the .path handling in isolation —
# nothing is installed and the functions are simply available.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
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
    if ! ensure_runner_path; then
      log "WARN: at least one runner's .path file could not be updated"
      log "WARN: the runner service may not find the routine by name"
    fi
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
fi
