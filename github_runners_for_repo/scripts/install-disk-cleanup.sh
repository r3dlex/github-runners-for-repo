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
# ## Sandbox mode
#
# The defaults point at the LIVE fleet: the routine goes to
# /usr/local/bin, the unit files go to /etc/systemd/system and the runner
# homes are the two Colima runners. A run that changes ONE of those knobs
# therefore mixes a sandbox value with a live one, and the mix is what
# breaks the fleet. The installer refuses to guess which of the two the
# operator meant:
#
#   - SANDBOX=1 makes the run a verification. The runner homes, the
#     install path and the unit directory are all confined to
#     SANDBOX_ROOT (default: $TMPDIR/install-disk-cleanup-sandbox);
#     anything that resolves outside it is refused with a non-zero exit
#     before a single file is touched. The timer is not enabled and the
#     routine is not started.
#   - Without SANDBOX, a run is either entirely LIVE (the live default
#     for all three knobs) or entirely ISOLATED (its unit files go to a
#     directory systemd does not read). A MIX is refused before anything
#     is written:
#       - the install directory changed but the runner homes did not:
#         the run would write the verification's directory into the LIVE
#         runners' .path file, and every job on them would start with a
#         dead PATH entry;
#       - the install path changed but the unit directory is still a
#         live systemd directory: the run would rewrite the LIVE
#         runner-disk-cleanup.service to execute the new path, and the
#         live timer would then fail on every tick;
#       - the unit directory is not a systemd directory but the install
#         path is still the live one: enabling the timer would enable
#         the LIVE runner-disk-cleanup.timer.
#     The "name RUNNER_HOMES explicitly and you are fine" advice that
#     used to be in this file was wrong: naming the runner homes alone
#     leaves the unit directory live, and the run rewrites the live unit.
#   - The install directory is only added to a .path file when it exists
#     on disk.
#
# ## The .path file is never pruned
#
# The installer only ever PREPENDS its install directory; it never
# removes an entry. The live runners' .path carries a colon-joined line
# (/usr/games:/usr/local/games:/snap/bin) whose fragments do not exist on
# the Colima VM. A cleanup that dropped "missing" fragments — or a whole
# line because one of its fragments was missing — would silently strip
# PATH entries the runners are supposed to have. A stale entry costs a
# failed lookup; a missing one costs a broken tool. If that ever has to
# change, prune per FRAGMENT and never per line.
#
# ## Locking
#
# The routine takes the lock itself (an exclusive flock on
# /run/runner-disk-cleanup.lock), so the 15-minute
# runner-disk-cleanup.timer unit deliberately carries NO outer
# /usr/bin/flock: a unit that locks a lock it already holds deadlocks
# the timer against itself. The weekly runner-cleanup.service on the host
# is a different unit and its script takes no lock of its own, so IT
# must keep its own `flock -w 300` wrapper. Both units have to use the
# same lock file.
#
set -euo pipefail

# Umask 022 so the unit files this script writes are 0644 (systemd refuses
# world-writable unit files) and any directory it creates is 0755. The
# runner-owned trees are additionally chmod'ed explicitly below.
umask 022

log() { printf '[install-disk-cleanup] %s\n' "$*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_SCRIPT="$SCRIPT_DIR/cleanup-host-disk.sh"

# The defaults point at the LIVE fleet. Whether the operator changed a knob
# is recorded BEFORE the defaults are applied, because "the install
# directory was changed but the runner homes were not" is exactly the
# mixed-mode run that mutates the live runners.
DEFAULT_INSTALL_PATH="/usr/local/bin/runner-disk-cleanup.sh"
DEFAULT_UNIT_DIR="/etc/systemd/system"
DEFAULT_RUNNER_HOMES="/home/redlexgilgamesh.guest/actions-runner-linux /home/redlexgilgamesh.guest/actions-runner-linux-2"

INSTALL_PATH_OVERRIDDEN=0
if [ -n "${INSTALL_PATH+x}" ]; then INSTALL_PATH_OVERRIDDEN=1; fi
UNIT_DIR_OVERRIDDEN=0
if [ -n "${UNIT_DIR+x}" ]; then UNIT_DIR_OVERRIDDEN=1; fi
RUNNER_HOMES_OVERRIDDEN=0
if [ -n "${RUNNER_HOMES+x}" ]; then RUNNER_HOMES_OVERRIDDEN=1; fi

INSTALL_PATH="${INSTALL_PATH:-$DEFAULT_INSTALL_PATH}"
UNIT_DIR="${UNIT_DIR:-$DEFAULT_UNIT_DIR}"
SERVICE_UNIT="$UNIT_DIR/runner-disk-cleanup.service"
TIMER_UNIT="$UNIT_DIR/runner-disk-cleanup.timer"

# The runner directories whose .path file is updated. Same knob as the
# cleanup routine itself, so an operator points both at the same place.
#
# The list is SPACE separated: the value is split on whitespace, so a path
# that contains a space cannot be expressed here.
RUNNER_HOMES="${RUNNER_HOMES:-$DEFAULT_RUNNER_HOMES}"
read -r -a RUNNER_HOME_LIST <<<"$RUNNER_HOMES"

# The sandbox opt-in. SANDBOX=1 turns the run into a verification that is
# confined to SANDBOX_ROOT; see the header for the contract.
SANDBOX="${SANDBOX:-0}"
case "$SANDBOX" in
  1 | true | yes | on) SANDBOX=1 ;;
  0 | false | no | off) SANDBOX=0 ;;
  *)
    log "ERROR: SANDBOX must be 0 or 1 (got '$SANDBOX')"
    exit 1
    ;;
esac
SANDBOX_ROOT="${SANDBOX_ROOT:-${TMPDIR:-/tmp}/install-disk-cleanup-sandbox}"

# The absolute, symlink-resolved form of a path, whether or not it exists.
#
# `readlink -f` is GNU-only, so the longest existing prefix is resolved
# with cd+pwd -P and the missing tail is appended unchanged. A containment
# check needs this: on macOS /tmp is a symlink to /private/tmp, and a
# relative path must not slip past one.
resolve_path() {
  local target="$1" tail="" head
  case "$target" in
    /*) ;;
    *) target="$PWD/$target" ;;
  esac
  while [ ! -d "$target" ]; do
    if [ "$target" = "/" ]; then break; fi
    tail="/$(basename -- "$target")$tail"
    target="$(dirname -- "$target")"
  done
  if [ -d "$target" ]; then
    head="$(cd -- "$target" 2>/dev/null && pwd -P)" || head="$target"
  else
    head="/"
  fi
  case "$head" in
    /) printf '%s' "/${tail#/}" ;;
    *) printf '%s' "${head%/}$tail" ;;
  esac
}

# True when $1 is $2 or lives under it. Both sides must already have been
# resolved with resolve_path.
path_under() {
  local path="$1" root="$2"
  case "$path" in
    "$root") return 0 ;;
    "$root"/*) return 0 ;;
  esac
  return 1
}

# The systemd unit directories. A unit file written into one of them is a
# LIVE unit: systemd reads it on the next daemon-reload and runs it.
#
# The list is a knob ONLY so the sandbox tests can point the live directory
# at a sandbox directory and prove that a refused run leaves the live unit
# file untouched. On the host it is never set: the installer is run as root
# from a shell that carries no such variable, and the default is the real
# set of directories systemd reads.
SYSTEMD_UNIT_DIRS="${SYSTEMD_UNIT_DIRS:-/etc/systemd/system /run/systemd/system /usr/lib/systemd/system /lib/systemd/system}"

# True when the directory is one systemd reads units from.
is_systemd_unit_dir() {
  local dir="$1" candidate
  dir="$(resolve_path "$dir")"
  for candidate in $SYSTEMD_UNIT_DIRS; do
    candidate="$(resolve_path "$candidate")"
    if [ "$dir" = "$candidate" ]; then
      return 0
    fi
    case "$dir" in
      "$candidate"/*) return 0 ;;
    esac
  done
  return 1
}

# The runner homes as absolute, resolved paths, joined by a space.
#
# A relative runner home would make every path derived from it relative as
# well, and the routine's cache sweep would then delete paths relative to
# the unit's working directory (/).
resolved_runner_homes() {
  local -a homes=() out=() home
  local joined=""
  read -r -a homes <<<"$1"
  for home in "${homes[@]:-}"; do
    [ -n "$home" ] || continue
    out+=("$(resolve_path "$home")")
  done
  for home in "${out[@]:-}"; do
    [ -n "$home" ] || continue
    joined="$joined $home"
  done
  printf '%s' "${joined# }"
}

# Replace the runner home list with the resolved one.
normalize_runner_homes() {
  local joined
  joined="$(resolved_runner_homes "$RUNNER_HOMES")"
  RUNNER_HOME_LIST=()
  if [ -n "$joined" ]; then
    read -r -a RUNNER_HOME_LIST <<<"$joined"
  fi
}

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

# A sandbox run writes only into the sandbox, which the invoking user owns,
# so it never needs sudo — and must never wait for a password prompt.
if [ "$SANDBOX" = "1" ]; then
  SUDO=""
fi

SANDBOX_ROOT_RESOLVED="$(resolve_path "$SANDBOX_ROOT")"

if [ "$SANDBOX" = "1" ]; then
  # Everything the run writes into is forced into the sandbox unless the
  # operator names it explicitly.
  if [ "$RUNNER_HOMES_OVERRIDDEN" -eq 0 ]; then
    RUNNER_HOMES="$SANDBOX_ROOT/actions-runner-linux"
  fi
  if [ "$INSTALL_PATH_OVERRIDDEN" -eq 0 ]; then
    INSTALL_PATH="$SANDBOX_ROOT/bin/runner-disk-cleanup.sh"
  fi
  if [ "$UNIT_DIR_OVERRIDDEN" -eq 0 ]; then
    UNIT_DIR="$SANDBOX_ROOT/systemd"
  fi
  SERVICE_UNIT="$UNIT_DIR/runner-disk-cleanup.service"
  TIMER_UNIT="$UNIT_DIR/runner-disk-cleanup.timer"
  read -r -a RUNNER_HOME_LIST <<<"$RUNNER_HOMES"
  normalize_runner_homes

  # Fail closed: a sandbox run that reaches outside its root is refused
  # before anything is touched.
  for sandbox_target in "${RUNNER_HOME_LIST[@]:-}" "$INSTALL_PATH" "$UNIT_DIR"; do
    [ -n "$sandbox_target" ] || continue
    sandbox_target_resolved="$(resolve_path "$sandbox_target")"
    if ! path_under "$sandbox_target_resolved" "$SANDBOX_ROOT_RESOLVED"; then
      log "ERROR: SANDBOX=1, but $sandbox_target resolves to $sandbox_target_resolved,"
      log "ERROR: which is outside the sandbox root $SANDBOX_ROOT_RESOLVED."
      log "ERROR: refusing to touch anything outside the sandbox."
      exit 1
    fi
  done
  log "sandbox mode: everything stays under $SANDBOX_ROOT_RESOLVED"
else
  # The runner homes are compared RESOLVED: a home that is spelled through
  # a symlink (/home/<user>.linux -> <user>.guest) IS the live home, and a
  # run that names it has to be treated as one.
  runner_homes_resolved="$(resolved_runner_homes "$RUNNER_HOMES")"
  default_runner_homes_resolved="$(resolved_runner_homes "$DEFAULT_RUNNER_HOMES")"
  install_path_resolved="$(resolve_path "$INSTALL_PATH")"
  default_install_path_resolved="$(resolve_path "$DEFAULT_INSTALL_PATH")"

  # A run that changes the install directory but not the runner homes
  # would write that directory into the LIVE runners' .path file, which is
  # the pollution that put a dead PATH entry into every job on the fleet.
  install_bin_dir="$(resolve_path "$(dirname -- "$INSTALL_PATH")")"
  default_bin_dir="$(resolve_path "$(dirname -- "$DEFAULT_INSTALL_PATH")")"
  if [ "$runner_homes_resolved" = "$default_runner_homes_resolved" ] &&
    [ "$install_bin_dir" != "$default_bin_dir" ]; then
    log "ERROR: INSTALL_PATH was overridden to $INSTALL_PATH but RUNNER_HOMES was not."
    log "ERROR: the default RUNNER_HOMES point at the LIVE runners, so this run"
    log "ERROR: would write $install_bin_dir into their .path file."
    log "ERROR: set RUNNER_HOMES explicitly, or run with SANDBOX=1."
    exit 1
  fi

  # The mirror image of the rule above, and the one that used to be
  # missing: a run that changes the install path but leaves the unit
  # directory at the LIVE one rewrites
  # /etc/systemd/system/runner-disk-cleanup.service to execute the new
  # path. The live timer runs that unit every 15 minutes, so a path that
  # does not exist — or that the sweep removes — makes the disk cleanup
  # die silently on every tick, the root filesystem fills, and both Linux
  # runners go offline with zero-step jobs.
  #
  # The guard does not care whether RUNNER_HOMES was named: a run is
  # either entirely LIVE (the live defaults for all three knobs) or
  # entirely ISOLATED (its units go to a directory systemd does not
  # read). The documented "name RUNNER_HOMES and you are fine" advice was
  # wrong, and following it broke the live unit.
  if is_systemd_unit_dir "$UNIT_DIR" &&
    [ "$install_path_resolved" != "$default_install_path_resolved" ]; then
    log "ERROR: UNIT_DIR is a live systemd unit directory ($UNIT_DIR) and INSTALL_PATH is not the default ($INSTALL_PATH)."
    log "ERROR: this run would rewrite the LIVE runner-disk-cleanup.service to"
    log "ERROR: execute $INSTALL_PATH, and the live timer would then fail on every tick."
    log "ERROR: set UNIT_DIR to a directory systemd does not read, or run with SANDBOX=1."
    exit 1
  fi

  # And the other way round: units that systemd does not read, but that
  # point at the LIVE install path, would enable the LIVE timer while the
  # operator believes the run is isolated.
  if ! is_systemd_unit_dir "$UNIT_DIR" &&
    [ "$install_path_resolved" = "$default_install_path_resolved" ]; then
    log "ERROR: UNIT_DIR is not a systemd unit directory ($UNIT_DIR) but INSTALL_PATH is the live default ($INSTALL_PATH)."
    log "ERROR: enabling the timer would enable the LIVE runner-disk-cleanup.timer."
    log "ERROR: set INSTALL_PATH to a non-default path, or use the live defaults for both."
    exit 1
  fi

  normalize_runner_homes
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

# The user the runner service actually runs as, taken from its systemd unit.
#
# The unit is the only place the real user is written down. The owner of the
# runner home is just a proxy for it, and that proxy is wrong exactly in the
# case this install exists to repair: a root-owned runner home.
runner_service_user() {
  local unit user
  for unit in "$UNIT_DIR"/actions.runner.*.service; do
    [ -f "$unit" ] || continue
    user="$(sed -n 's/^User[[:space:]]*=[[:space:]]*//p' "$unit" 2>/dev/null | head -n 1)"
    [ -n "$user" ] || continue
    # A unit can name a user that does not exist on this host; that is not a
    # user to chown anything to.
    id "$user" >/dev/null 2>&1 || continue
    printf '%s' "$user"
    return 0
  done
  return 1
}

# The user that must own the runner home, and the group to chown it with.
# Prints "<owner> <group>"; returns 1 when it cannot be determined.
#
# Normally that user is the owner of the runner home. A ROOT-owned runner
# home is the failure state this install repairs, and the owner of the
# directory cannot be the answer in that case: the recursive chown becomes a
# no-op and the write check trivially passes, so the install reports success
# while the non-root runner service still cannot write its work directory.
# The runner service's own unit names the real user, so it is asked instead.
runner_owner_and_group() {
  local runner_dir="$1" owner group svc_owner
  owner="$(dir_owner "$runner_dir")"
  if [ -z "$owner" ]; then
    log "WARN: cannot determine the owner of $runner_dir"
    return 1
  fi
  # Read the group before the chown — the chown changes it.
  group="$(dir_group "$runner_dir")"
  case "$owner" in
    root | 0)
      svc_owner="$(runner_service_user || true)"
      if [ -z "$svc_owner" ]; then
        log "WARN: $runner_dir is root-owned and no runner service unit names a user"
        return 1
      fi
      log "$runner_dir is root-owned; the runner service runs as $svc_owner"
      owner="$svc_owner"
      group="$owner"
      ;;
  esac
  if [ -z "$group" ]; then
    group="$(id -gn "$owner" 2>/dev/null || printf '%s' "$owner")"
  fi
  printf '%s %s' "$owner" "$group"
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
  # The unit files are written to a temporary file in the SAME directory
  # and renamed into place. `tee` truncates the live unit in place, and a
  # `systemctl daemon-reload` that races the write loads a half-written
  # unit; a rename(2) within the directory is atomic, so systemd only
  # ever sees the complete unit.
  local service_tmp timer_tmp
  service_tmp="$UNIT_DIR/.runner-disk-cleanup.service.$$"
  timer_tmp="$UNIT_DIR/.runner-disk-cleanup.timer.$$"

  $SUDO tee "$service_tmp" >/dev/null <<UNIT
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
  $SUDO chmod 0644 "$service_tmp"
  $SUDO mv -f -- "$service_tmp" "$SERVICE_UNIT"

  $SUDO tee "$timer_tmp" >/dev/null <<'UNIT'
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
  $SUDO chmod 0644 "$timer_tmp"
  $SUDO mv -f -- "$timer_tmp" "$TIMER_UNIT"

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
  local runner_dir owner group cred owner_group
  for runner_dir in "${RUNNER_HOME_LIST[@]:-}"; do
    [ -n "$runner_dir" ] || continue
    if [ ! -d "$runner_dir" ]; then
      log "WARN: runner home $runner_dir does not exist; skipping it"
      continue
    fi
    # A sandbox run must not change the ownership of anything outside its
    # root: the live runners' tree is not a verification target, and a
    # recursive chown there is a fleet-wide mutation.
    if [ "$SANDBOX" = "1" ] && ! path_under "$(resolve_path "$runner_dir")" "$SANDBOX_ROOT_RESOLVED"; then
      log "WARN: $runner_dir is outside the sandbox root $SANDBOX_ROOT_RESOLVED; leaving its ownership untouched"
      continue
    fi

    if ! owner_group="$(runner_owner_and_group "$runner_dir")"; then
      log "WARN: the user that must own $runner_dir could not be determined; leaving it untouched"
      continue
    fi
    owner="${owner_group%% *}"
    group="${owner_group##* }"

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
  local runner_dir owner rc=0 owner_group
  for runner_dir in "${RUNNER_HOME_LIST[@]:-}"; do
    [ -n "$runner_dir" ] || continue
    if [ ! -d "$runner_dir" ]; then
      log "WARN: runner home $runner_dir does not exist; skipping the write check"
      continue
    fi

    if ! owner_group="$(runner_owner_and_group "$runner_dir")"; then
      log "WARN: skipping the write check for $runner_dir"
      continue
    fi
    owner="${owner_group%% *}"

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
  # A unit that systemd does not read cannot be enabled — and
  # `systemctl enable runner-disk-cleanup.timer` resolves the NAME, so it
  # would enable the LIVE timer while the operator believes the run is
  # isolated. The live fleet's timer is not this run's to touch.
  if ! is_systemd_unit_dir "$UNIT_DIR"; then
    log "the unit directory $UNIT_DIR is not a systemd unit directory;"
    log "the timer was not enabled (enabling it would enable the LIVE timer)"
    return 0
  fi
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable --now runner-disk-cleanup.timer
  log "enabled runner-disk-cleanup.timer (every 15 minutes)"
}

run_once() {
  # The same reason as in enable_timer: `systemctl start` resolves the
  # unit NAME, so it would run the LIVE service. An isolated run verifies
  # the installed routine directly instead.
  if ! is_systemd_unit_dir "$UNIT_DIR"; then
    log "the unit directory $UNIT_DIR is not a systemd unit directory;"
    log "verifying the installed routine directly instead of through systemd"
    if "$INSTALL_PATH" --status >/dev/null 2>&1; then
      log "the installed routine at $INSTALL_PATH ran (--status)"
    else
      log "ERROR: the installed routine at $INSTALL_PATH failed to run"
      exit 1
    fi
    return 0
  fi
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
  # The fragment is compared as a FIXED string. The old `case` pattern only
  # matched literally because the expansion happened to sit inside double
  # quotes — an expansion that lost its quotes would turn the fragment
  # into a glob, and a directory whose name contains ".", "*" or "[" would
  # then match far more than it should: a home directory called
  # "runner[1]" would look like it was already in the file, and the entry
  # would never be added and the runner would lose the tool.
  # The stream is wrapped in colons so that a fragment at either end of
  # the file is still delimited: the first fragment of the file has no
  # colon in front of it.
  printf '%s' ":$(tr '\n' ':' <"$path_file" 2>/dev/null):" |
    grep -Fq -- ":${bin_dir}:" && return 0
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
  # The install directory has to exist before it goes into a .path file: a
  # dead entry costs every job on that runner a failed lookup, and the
  # guarantee the entry exists for ("the routine resolves by name") is
  # dead by default when the directory is missing.
  if [ ! -d "$bin_dir" ]; then
    log "WARN: the install directory $bin_dir does not exist; not adding it to any .path file"
    return 0
  fi
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
    #
    # An empty .path file gets the entry and nothing else: a trailing blank
    # line is a PATH fragment that does not exist.
    # shellcheck disable=SC2016
    if ! as_user "$owner" sh -c '
        if [ -n "$2" ]; then
          printf "%s\n%s\n" "$1" "$2" > "$3"
        else
          printf "%s\n" "$1" > "$3"
        fi
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

# Create the directories a sandbox install writes into, so a verification
# run can complete end to end. Nothing outside the sandbox is touched.
sandbox_prepare() {
  local runner_dir
  [ "$SANDBOX" = "1" ] || return 0
  mkdir -p "$(dirname -- "$INSTALL_PATH")" "$UNIT_DIR"
  for runner_dir in "${RUNNER_HOME_LIST[@]:-}"; do
    [ -n "$runner_dir" ] || continue
    mkdir -p "$runner_dir"
  done
}

# The install itself. Enabling the timer and running the routine once are
# the only steps that need a live systemd, so a sandbox run skips them.
run_install() {
  sandbox_prepare
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
  if [ "$SANDBOX" = "1" ]; then
    log "sandbox mode: the timer was not enabled and the routine was not started"
    log "sandbox install complete (nothing outside $SANDBOX_ROOT_RESOLVED was touched)"
    return 0
  fi
  enable_timer
  run_once
  log "disk cleanup installed (timer enabled, routine verified)"
}

# Run the install when the script is executed. When it is sourced — the
# sandbox tests do that to exercise the .path handling in isolation —
# nothing is installed and the functions are simply available.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
case "$uname_s" in
  Linux)
    run_install
    log "the runner services were left running"
    ;;
  Darwin)
    if [ "$SANDBOX" = "1" ]; then
      log "sandbox mode: installing into $SANDBOX_ROOT_RESOLVED (macOS has no systemd)"
      run_install
    else
      log "macOS: systemd timers are Linux-only, nothing installed"
      log "macOS: the Linux runners live in the Colima VM — run this script there"
    fi
    ;;
  *)
    log "ERROR: unsupported OS '$uname_s' (expected Linux or Darwin)"
    exit 1
    ;;
esac
fi
