#!/usr/bin/env bash
# cleanup-host-disk.sh — reclaim disk space on the self-hosted Actions runners.
#
# Installed on the host as /usr/local/bin/runner-disk-cleanup.sh and driven by
# runner-disk-cleanup.timer (every 15 minutes). The timer is a fixed
# heartbeat; the WORK is pressure-adaptive, so most runs cost almost nothing
# and busy periods escalate automatically.
#
# Provenance: this is the portable, repo-tracked copy of the routine that runs
# on the Colima VM (where the host copy lives at
# /usr/local/bin/runner-disk-cleanup.sh). The host copy is the one that was
# proven under real disk pressure — keep the two in sync. Every host-specific
# value is an env-overridable knob here, so the host can be retuned without
# an edit and the same routine runs on the macOS runners and in CI.
#
# ## Heuristics
#
# Every sweep target is scored against the pressure of the filesystem it lives
# on, and the tier decides how aggressive that sweep is:
#
#   tier 0  nominal    (< WATCH_PCT)        skip the sweep entirely
#   tier 1  watch      (WATCH..ELEVATED)    standard age thresholds
#   tier 2  elevated   (ELEVATED..CRITICAL) half the thresholds, caches purged
#                                            regardless of age
#   tier 3  critical   (>= CRITICAL)        the one-unit floor: anything older
#                                            than an hour (a day for the
#                                            daily caches) that is not in use
#
# Tier 3 does NOT mean "prune regardless of age". The age floor is what
# protects a live job's checkout: the runner writes into it continuously, so
# a checkout that is minutes old is never swept, no matter how full the disk
# is.
#
# After a pass, the script re-measures. If the filesystem is still above
# TARGET_PCT it escalates one tier and sweeps again, up to MAX_PASSES times.
# That closed loop is what keeps a busy afternoon from filling the disk: one
# pass is never assumed to be enough.
#
# Each filesystem is scored, tiered AND escalated on its own. Root pressure
# never raises the tier that is applied to the data disk holding the
# checkouts, and data-disk pressure never raises the tier applied to the
# root filesystem's caches.
#
# ## Safety
#
# The VM has neither lsof nor fuser, so liveness is decided two ways:
#
#   1. mtime — the runner writes into an active job's checkout continuously, so
#      a live job's directory is never stale by mtime. The age window is
#      never zero, so this backstop always runs.
#   2. /proc — the working directory and open files of EVERY live process
#      are read directly, and any path under a watched root is treated as in
#      use regardless of age. actions/runner never changes its own working
#      directory (it sets the child's instead), so the process that holds a
#      checkout is the child the runner spawned — node, git, bash. Matching
#      on the runner's own command line would protect nothing.
#
# A path that is in use is NEVER deleted, at any tier. A live process that
# holds a path's DIRECT PARENT also protects it: a process whose working
# directory is the _work root holds every checkout below it. That rule is
# limited to the _work root on purpose — the runner holds _work/_temp open at
# all times, so a general "any parent protects" rule would disable the temp
# sweep permanently, and walking up to the runner home would disable the whole
# sweep, because the runner's own processes always hold that.
#
# ## Mutual exclusion
#
# The routine takes an exclusive flock on LOCK_FILE. The weekly
# runner-cleanup.service on the same host prunes _work as well and has no
# in-use guard of its own, so both units must take the SAME lock: systemd
# serialises a unit against itself, never against a differently named one.
#
# ## It only ever deletes
#
# This routine runs as root. It never creates or moves anything: it only
# removes files and directories, so it cannot leave a root-owned artefact
# under a runner home. That matters — a root-owned _work tree makes every
# job fail with a permission error while the runner still looks healthy.
# Anything that has to be created under a runner home is created by
# install-disk-cleanup.sh, as the runner user, and verified there.
set -uo pipefail

# --- filesystem pressure thresholds (percent used) ---
# Every knob is env-overridable so the tiers can be exercised without waiting
# for real disk pressure, and so an operator can retune without editing.
WATCH_PCT="${WATCH_PCT:-60}"      # below this: do nothing
TARGET_PCT="${TARGET_PCT:-70}"    # aim to get back under this
ELEVATED_PCT="${ELEVATED_PCT:-75}"
CRITICAL_PCT="${CRITICAL_PCT:-85}"
MAX_PASSES="${MAX_PASSES:-3}"

# The mutual-exclusion lock. This routine and the weekly runner-cleanup
# service on the same host must take the SAME lock file, otherwise they can
# prune the work tree at the same time.
LOCK_FILE="${LOCK_FILE:-/run/runner-disk-cleanup.lock}"

# --- base age thresholds ---
BASE_STALE_HOURS="${BASE_STALE_HOURS:-6}"
BASE_ACTIONS_DAYS="${BASE_ACTIONS_DAYS:-14}"
BASE_TOOL_DAYS="${BASE_TOOL_DAYS:-30}"
BASE_DIAG_DAYS="${BASE_DIAG_DAYS:-7}"
BASE_CACHE_DAYS="${BASE_CACHE_DAYS:-3}"
BASE_ARCHIVE_DAYS="${BASE_ARCHIVE_DAYS:-1}"
BASE_TMP_DAYS="${BASE_TMP_DAYS:-1}"

JOURNAL_MAX="${JOURNAL_MAX:-50M}"
DOCKER_AGE="${DOCKER_AGE:-24h}"

DRY_RUN=0
STATUS_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --status) STATUS_ONLY=1 ;;
  esac
done

log() {
  logger -t runner-disk-cleanup -- "$*" 2>/dev/null || true
  # date -Is is GNU-only; this format is the same on GNU and BSD.
  printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"
}

# --- host layout -----------------------------------------------------------
# The paths that differ between hosts are parameters, not constants, so the
# same routine runs on the Colima VM, on the macOS runners, and in CI.

# Runner directories to sweep, space separated. The Colima VM has two Linux
# runner directories; the macOS runners live under the runner user's home.
RUNNER_HOMES="${RUNNER_HOMES:-/home/redlexgilgamesh.guest/actions-runner-linux /home/redlexgilgamesh.guest/actions-runner-linux-2}"
read -r -a RUNNER_HOME_LIST <<<"$RUNNER_HOMES"

# The home directory whose caches are worth reclaiming — the RUNNER user's, not
# root's. The unit runs as root (it prunes system dirs and docker) and
# systemd does not export HOME for a root unit, so `$HOME` is both unbound
# under `set -u` and the wrong directory when it is set. The runner home is
# <user home>/actions-runner-linux, so the user home is one level up.
HOME_DIR="${HOME_DIR:-}"
if [ -z "$HOME_DIR" ]; then
  if [ "${#RUNNER_HOME_LIST[@]}" -gt 0 ]; then
    HOME_DIR="$(dirname "${RUNNER_HOME_LIST[0]}")"
  else
    HOME_DIR="${HOME:-/root}"
  fi
fi

# The filesystem that holds the runner work. On the Colima VM the runner's
# _work directory is bind-mounted onto the 100G data disk, so it fills
# independently of the 19G root filesystem. When that mount does not exist
# (macOS runners, CI) the work is scored on the filesystem that holds it.
DATA_MOUNT="${DATA_MOUNT:-/mnt/lima-colima}"

# The directory whose stale regular files are reclaimed. A knob so the
# routine can be exercised against a sandbox instead of the real /tmp.
TMP_DIR="${TMP_DIR:-/tmp}"

# The roots this routine is allowed to touch: the runner homes and the home
# that owns the caches. Used to keep the in-use set small.
WATCH_ROOTS=()
for _root in "$HOME_DIR" "${RUNNER_HOME_LIST[@]:-}"; do
  [ -n "$_root" ] || continue
  case " ${WATCH_ROOTS[*]:-} " in
    *" $_root "*) ;;
    *) WATCH_ROOTS+=("$_root") ;;
  esac
done
unset _root

# The same roots, resolved. lsof reports physical paths (/private/tmp/... on
# macOS), so the collected paths are matched against these.
WATCH_ROOTS_PHYS=()
for _root in "${WATCH_ROOTS[@]:-}"; do
  [ -n "$_root" ] || continue
  _phys="$(cd "$_root" 2>/dev/null && pwd -P)" || _phys="$_root"
  [ -n "$_phys" ] || _phys="$_root"
  WATCH_ROOTS_PHYS+=("$_phys")
done
unset _root _phys

# True when a path lives under one of the watched roots.
under_watched_root() {
  local p="$1" root
  for root in "${WATCH_ROOTS_PHYS[@]:-}"; do
    [ -n "$root" ] || continue
    case "$p" in
      "$root" | "$root"/*) return 0 ;;
    esac
  done
  return 1
}

# ---------------------------------------------------------------- in-use paths

INUSE_PATHS=()

# Linux: read the working directory and open files of EVERY live process
# straight out of /proc.
#
# There is deliberately no command-line filter here. actions/runner never
# calls Directory.SetCurrentDirectory, so the runner process itself does not
# hold the checkout; the child it spawns (node, git, bash) inherits the
# checkout as its working directory. Filtering on "Runner.Worker" therefore
# protected nothing at all — at tier 3 that meant a live job's checkout
# could be deleted out from under it.
#
# Every path under a watched root is kept; everything else is dropped, so the
# in-use set stays small no matter how many processes the host runs.
collect_inuse_proc() {
  local d target fd
  for d in /proc/[0-9]*; do
    target="$(readlink "$d/cwd" 2>/dev/null)" || target=""
    if [ -n "$target" ] && under_watched_root "$target"; then
      INUSE_PATHS+=("$target")
    fi
    for fd in "$d"/fd/*; do
      target="$(readlink "$fd" 2>/dev/null)" || continue
      case "$target" in
        /*) ;;
        *) continue ;;
      esac
      under_watched_root "$target" || continue
      INUSE_PATHS+=("$target")
    done
  done
}

# Fallback for hosts without /proc (macOS): lsof lists the open files of
# every process the caller can see. On the VM neither lsof nor fuser exists,
# so this path never runs there and the mtime rule stands alone.
collect_inuse_lsof() {
  local target
  while IFS= read -r target; do
    [ -n "$target" ] || continue
    # lsof reports every open file on the machine; only paths under a
    # watched root can ever be pruned, so the set is filtered down to those.
    under_watched_root "$target" || continue
    INUSE_PATHS+=("$target")
  done < <(lsof -Fn 2>/dev/null | sed -n 's/^n//p')
}

collect_inuse() {
  INUSE_PATHS=()
  if [ -d /proc/self ]; then
    collect_inuse_proc
  elif command -v lsof >/dev/null 2>&1; then
    collect_inuse_lsof
  fi
}

# A candidate is in use if any live path is equal to it or lives beneath it.
# A live path that is the candidate's DIRECT PARENT also protects it: a
# process whose working directory is the _work root holds every checkout
# below it, and without this rule that checkout was reported as prunable
# while a job was running in it.
#
# Only ONE level up is checked. The runner's own process holds the runner
# home, so walking all the way to the root of the watched tree would mark
# every candidate in use and disable the sweep entirely.
is_in_use() {
  local cand="$1" p physical
  # Compare on a slash-normalised candidate. The checkout glob ("$work"/*/)
  # yields a trailing slash, and "/work/repo/" does not prefix-match
  # "/work/repo/file" — without this the guard silently misses a live job's
  # checkout and, at tier 3, deletes it.
  while [ "$cand" != "/" ] && [ "${cand%/}" != "$cand" ]; do cand="${cand%/}"; done
  # Compare the physical path as well: on macOS lsof reports the resolved
  # path (/private/tmp/...) while the candidate is the logical one (/tmp/...).
  physical=""
  if [ -d "$cand" ]; then
    physical="$(cd "$cand" 2>/dev/null && pwd -P)" || physical=""
  fi
  for p in "${INUSE_PATHS[@]:-}"; do
    [ -n "$p" ] || continue
    case "$p" in
      "$cand" | "$cand"/*) return 0 ;;
    esac
    if [ -n "$physical" ]; then
      case "$p" in
        "$physical" | "$physical"/*) return 0 ;;
      esac
    fi
    # A live path that is the candidate's DIRECT PARENT protects it too: a
    # process whose working directory is the _work root holds every checkout
    # below it, and without this rule that checkout was reported as prunable
    # while a job was running in it.
    #
    # The rule is deliberately limited to the _work root. The runner itself
    # holds _work/_temp open at all times, and treating that as protective
    # would disable the temp sweep permanently; walking further up (to the
    # runner home) would disable the whole sweep, because the runner's own
    # processes always hold that.
    case "$(dirname "$cand")" in
      */_work) case "$p" in */_work) return 0 ;; esac ;;
    esac
  done
  return 1
}

# ------------------------------------------------------------------- pressure

# Percent used of the filesystem that contains $1.
pct_for_path() {
  local p="$1" line
  line="$(df -P "$p" 2>/dev/null | awk 'NR==2 {gsub(/%/,"",$5); print $5}')"
  [ -n "$line" ] || { echo 0; return; }
  echo "$line"
}

# Percent used of the filesystem that holds the runner work.
work_pct() {
  local home
  if [ -d "$DATA_MOUNT" ]; then
    pct_for_path "$DATA_MOUNT"
    return
  fi
  for home in "${RUNNER_HOME_LIST[@]:-}"; do
    if [ -n "$home" ] && [ -d "$home/_work" ]; then
      pct_for_path "$home/_work"
      return
    fi
  done
  pct_for_path "/"
}

# Tier for a given percentage.
tier_for_pct() {
  local pct="$1"
  if [ "$pct" -ge "$CRITICAL_PCT" ]; then echo 3
  elif [ "$pct" -ge "$ELEVATED_PCT" ]; then echo 2
  elif [ "$pct" -ge "$WATCH_PCT" ]; then echo 1
  else echo 0
  fi
}

# Age threshold for a tier. Tier 0 keeps the base value (unused), tier 1 keeps
# it, tier 2 halves it, tier 3 floors it at ONE unit of the caller's window
# (an hour for the checkout windows, a day for the daily caches).
#
# The floor is never zero. A zero window means "prune regardless of age":
# find -newermt "-0 days" matches nothing, so every path looks stale and the
# mtime backstop disappears — at tier 3 that deleted live jobs' checkouts.
# One unit is safely below the job timeout, so a live job's checkout is
# always inside the protected window.
scaled_age() {
  local base="$1" tier="$2" n
  case "$tier" in
    2) n=$(( base / 2 )) ;;
    3) n=1 ;;
    *) n="$base" ;;
  esac
  [ "$n" -lt 1 ] && n=1
  echo "$n"
}

# ------------------------------------------------------------------- sweeping

# True when the directory holds nothing newer than the window, i.e. it is
# old enough to prune.
is_stale() {
  local dir="$1" window="$2" num="${2%% *}"
  # A zero (or unparseable) window would make every path look stale and
  # remove the mtime backstop entirely. Fail closed instead: such a window
  # never prunes anything.
  case "$num" in
    '' | *[!0-9]*) return 1 ;;
  esac
  [ "$num" -ge 1 ] || return 1
  [ -n "$(find "$dir" -newermt "-${window}" -print -quit 2>/dev/null)" ] && return 1
  return 0
}

# Remove a single file, honouring DRY_RUN and the in-use guard.
remove_file() {
  local target="$1" label="$2"
  is_in_use "$target" && { log "skipped (in use) $target"; return 1; }
  # Re-collect immediately before the destructive step: the set collected at
  # the top of the pass is a snapshot, and a job that started since it was
  # taken holds this path RIGHT NOW. The decision is made on a set that is
  # current at the moment of the deletion.
  collect_inuse
  is_in_use "$target" && { log "skipped (in use) $target"; return 1; }
  if [ "$DRY_RUN" = "1" ]; then
    log "would prune $label $target"
    return 0
  fi
  rm -f -- "$target" 2>/dev/null && { log "pruned $label $target"; return 0; }
  return 1
}

# Remove a candidate only if it is stale AND not in use. Returns 0 if removed.
try_remove() {
  local target="$1" window="$2" label="$3"
  # Never remove something a live job holds, at any tier.
  is_in_use "$target" && { log "skipped (in use) $target"; return 1; }
  # The age window is ALWAYS checked. There is no "prune regardless of age"
  # mode: the window is what keeps a live job's checkout alive.
  is_stale "$target" "$window" || return 1
  # Re-collect immediately before the destructive step. The set collected at
  # the top of the pass is a snapshot taken BEFORE the sweep started, so a
  # job that picked up a checkout while the sweep was running is not in it:
  # without this refresh the sweep would delete the checkout of a job that
  # is running RIGHT NOW. The refresh costs one /proc scan per path that is
  # actually about to be deleted, so the cost tracks the deletions and not
  # the number of candidates that were examined.
  collect_inuse
  is_in_use "$target" && { log "skipped (in use) $target"; return 1; }
  if [ "$DRY_RUN" = "1" ]; then
    log "would prune $label $target"
    return 0
  fi
  rm -rf -- "$target" 2>/dev/null && { log "pruned $label $target"; return 0; }
  return 1
}

# Prune every directory at exactly depth [mind..maxd] under $parent.
prune_at_depth() {
  local parent="$1" mind="$2" maxd="$3" window="$4" label="$5" d
  [ -d "$parent" ] || return 0
  while IFS= read -r -d '' d; do
    try_remove "$d" "$window" "$label"
  done < <(find "$parent" -mindepth "$mind" -maxdepth "$maxd" -type d -print0 2>/dev/null)
}

# Delete the action watermarks whose version directory is gone.
#
# The runner writes the watermark for a downloaded action as a SIBLING file
# next to the version directory: _work/_actions/<owner>/<repo>/v1.completed
# next to _work/_actions/<owner>/<repo>/v1/. The version directory is a
# directory, so a sweep that only prunes directories removes v1/ and leaves
# v1.completed behind.
#
# That orphan bricks actions/checkout permanently: the runner sees the
# watermark, skips the download, and the action manifest is missing, so the
# step fails. It never self-heals — the runner never removes the watermark
# and the sweep never removed one. So the watermark is removed exactly when
# its version directory is gone, which makes the runner download the action
# again on the next job.
prune_orphan_action_markers() {
  local actions_root="$1" marker version_dir
  [ -d "$actions_root" ] || return 0
  while IFS= read -r -d '' marker; do
    version_dir="${marker%.completed}"
    # Still has its version directory: the watermark is doing its job.
    [ -d "$version_dir" ] && continue
    remove_file "$marker" "action-marker"
  done < <(find "$actions_root" -mindepth 3 -maxdepth 3 -type f -name '*.completed' -print0 2>/dev/null)
}

prune_runner_work() {
  local home="$1" tier="$2" work="$1/_work" d name
  [ -d "$work" ] || return 0

  local stale_h action_d tool_d diag_d archive_d
  stale_h="$(scaled_age "$BASE_STALE_HOURS" "$tier")"
  action_d="$(scaled_age "$BASE_ACTIONS_DAYS" "$tier")"
  tool_d="$(scaled_age "$BASE_TOOL_DAYS" "$tier")"
  diag_d="$(scaled_age "$BASE_DIAG_DAYS" "$tier")"
  archive_d="$(scaled_age "$BASE_ARCHIVE_DAYS" "$tier")"

  # Repo checkouts: _work/<repo>/. Never the runner's own bookkeeping.
  for d in "$work"/*/; do
    [ -d "$d" ] || continue
    name="$(basename "$d")"
    case "$name" in
      # _update is the runner's self-update staging area: SelfUpdaterV2
      # downloads the new binaries there before copying them into
      # bin.<version>/externals.<version>. Removing it mid-update aborts the
      # copy and can leave a partial bin.<version> behind — a runner that
      # cannot start, which is the exact failure this routine exists to
      # prevent.
      _tool | _actions | _temp | _PipelineMapping | _update) continue ;;
    esac
    try_remove "$d" "${stale_h} hours" "checkout"
  done

  # Per-job temp dirs: _work/_temp/<uuid>/.
  prune_at_depth "$work/_temp" 1 1 "${stale_h} hours" "temp"

  # Downloaded actions: _work/_actions/<owner>/<repo>/<version>/.
  prune_at_depth "$work/_actions" 3 3 "${action_d} days" "action"

  # Tool cache: _work/_tool/<tool>/<version>/.
  prune_at_depth "$work/_tool" 2 2 "${tool_d} days" "tool"

  # Runner worker logs: _diag/*.log.
  if [ -d "$home/_diag" ]; then
    while IFS= read -r -d '' d; do
      is_stale "$d" "${diag_d} days" || continue
      remove_file "$d" "log"
    done < <(find "$home/_diag" -maxdepth 1 -type f -name '*.log' -print0 2>/dev/null)
  fi

  # Stale runner update archives left in the runner home. The archive window
  # applies here too: the runner downloads its own update tarball into the
  # runner home, and deleting a fresh one aborts the update it belongs to.
  while IFS= read -r -d '' d; do
    is_stale "$d" "${archive_d} days" || continue
    remove_file "$d" "archive"
  done < <(find "$home" -maxdepth 1 -type f \( -name '*.tar.gz' -o -name '*.zip' \) -print0 2>/dev/null)
}

prune_caches() {
  local tier="$1" c cache_d
  cache_d="$(scaled_age "$BASE_CACHE_DAYS" "$tier")"

  for c in "$HOME_DIR/.cache/pip" "$HOME_DIR/.cache/uv" "$HOME_DIR/.npm/_cacache" \
           "$HOME_DIR/.bun/install/cache" "$HOME_DIR/.cache/pre-commit" \
           "$HOME_DIR/.cargo/registry/cache" "$HOME_DIR/.cargo/registry/src" \
           "$HOME_DIR/.rustup/downloads"; do
    [ -d "$c" ] || continue
    try_remove "$c" "${cache_d} days" "cache"
  done

  # Any other cold entry under ~/.cache.
  if [ -d "$HOME_DIR/.cache" ]; then
    for c in "$HOME_DIR/.cache"/*/; do
      [ -d "$c" ] || continue
      case "$c" in
        */pip/ | */uv/ | */pre-commit/) continue ;;
      esac
      try_remove "$c" "${cache_d} days" "cache"
    done
  fi
}

# The system-level prune.
#
# $1 is the tier of the ROOT filesystem (journal, apt, /tmp live there) and
# $2 is the tier of the DATA filesystem (docker's storage lives there). The
# two are kept apart on purpose: root pressure must not make the sweep
# aggressive with the data disk that holds the checkouts, and vice versa.
#
# Every destructive step is behind the dry-run guard. "docker image prune
# -af" removes EVERY unused image, so an unguarded call in a routine that
# fires every 15 minutes is a foot-gun, not a cleanup.
prune_system() {
  local root_tier="$1" data_tier="$2" tmp_d
  tmp_d="$(scaled_age "$BASE_TMP_DAYS" "$root_tier")"

  # These are always safe and always worth doing: they are bounded and cheap.
  if [ "$DRY_RUN" = "1" ]; then
    log "would vacuum the journal to $JOURNAL_MAX"
  else
    journalctl --vacuum-size="$JOURNAL_MAX" >/dev/null 2>&1 || true
  fi

  if [ "$DRY_RUN" = "1" ]; then
    log "would clean the apt cache"
  else
    apt-get clean >/dev/null 2>&1 || true
  fi

  # The package INDEX is only dropped from tier 2 up. Dropping it on every
  # tick empties the index, and the next "apt-get install" then fails with
  # "Unable to locate package" until somebody runs "apt-get update".
  # "apt-get clean" alone reclaims most of the space anyway.
  if [ "$root_tier" -ge 2 ]; then
    if [ "$DRY_RUN" = "1" ]; then
      log "would drop the apt package lists"
    else
      rm -rf /var/lib/apt/lists/* >/dev/null 2>&1 || true
    fi
  fi

  # Crash dumps.
  if [ -d /var/lib/systemd/coredump ]; then
    if [ "$DRY_RUN" = "1" ]; then
      log "would prune coredumps"
    else
      rm -rf /var/lib/systemd/coredump/* >/dev/null 2>&1 || true
    fi
  fi

  # Old regular files in the temporary directory (never sockets, never
  # directories).
  if [ -d "$TMP_DIR" ]; then
    while IFS= read -r -d '' d; do
      [ -f "$d" ] || continue
      remove_file "$d" "tmp"
    done < <(find "$TMP_DIR" -maxdepth 1 -type f -mtime "+${tmp_d}" -print0 2>/dev/null)
  fi

  # Docker's storage lives on the data disk, so docker is pruned at the DATA
  # tier: a full root filesystem must not wipe every unused image.
  #
  # The scope is deliberately narrow, because this daemon also hosts the
  # user's own long-lived containers and their state is not this routine's to
  # destroy:
  #
  #   - only DANGLING images are removed ("image prune" WITHOUT -a): an image
  #     that is tagged but not currently running belongs to the user, and
  #     removing it breaks the next "docker run" that expects it;
  #   - only DANGLING build cache is removed at the lower tiers; at the top
  #     tier all UNUSED build cache goes, which is still only cache;
  #   - stopped containers are never removed at all: a stopped container is
  #     the user's state, not cache, and "container prune" deletes it
  #     irreversibly.
  if command -v docker >/dev/null 2>&1; then
    if [ "$DRY_RUN" = "1" ]; then
      log "would prune dangling docker images (older than $DOCKER_AGE)"
      if [ "$data_tier" -ge 2 ]; then
        log "would prune all unused docker build cache (older than $DOCKER_AGE)"
      else
        log "would prune dangling docker build cache (older than $DOCKER_AGE)"
      fi
    else
      docker image prune -f --filter "until=${DOCKER_AGE}" >/dev/null 2>&1 || true
      if [ "$data_tier" -ge 2 ]; then
        docker builder prune -af --filter "until=${DOCKER_AGE}" >/dev/null 2>&1 || true
      else
        docker builder prune -f --filter "until=${DOCKER_AGE}" >/dev/null 2>&1 || true
      fi
    fi
  fi
}

# ----------------------------------------------------------------- main cycle

# The tier a pass runs at. The tier is CARRIED across passes and escalates by
# one while the filesystem is still over target, so a busy afternoon keeps
# pushing even when the percentage does not move. It never drops below the
# tier the current pressure implies, and never rises above tier 3.
#
# Each filesystem has its own carried tier: root pressure must not escalate
# the sweep of the data disk that holds the checkouts.
escalate_tier() {
  local previous="$1" current="$2" next
  # No pressure at all: nothing to escalate, and nothing to sweep.
  if [ "$current" -le 0 ]; then echo 0; return; fi
  next=$(( previous + 1 ))
  [ "$next" -gt 3 ] && next=3
  [ "$current" -gt "$next" ] && next="$current"
  echo "$next"
}

# Take the mutual-exclusion lock. Returns non-zero (and does not exit) when
# the lock is already held by another cleanup run — the weekly
# runner-cleanup.service on this host prunes the work tree too and has no
# in-use guard of its own.
acquire_lock() {
  if ! command -v flock >/dev/null 2>&1; then
    log "WARN: flock is unavailable; running without mutual exclusion"
    return 0
  fi
  # Check that the lock file can be created before handing it to exec: a
  # failed exec redirection is reported by the shell itself and cannot be
  # silenced, so it is checked (and reported) before it can happen.
  if ! touch "$LOCK_FILE" 2>/dev/null; then
    log "WARN: cannot open the lock file $LOCK_FILE; running without mutual exclusion"
    return 0
  fi
  if ! exec 9>"$LOCK_FILE" 2>/dev/null; then
    log "WARN: cannot open the lock file $LOCK_FILE; running without mutual exclusion"
    return 0
  fi
  if ! flock -n 9; then
    log "another disk-cleanup run holds $LOCK_FILE; skipping this tick"
    return 1
  fi
  return 0
}

sweep_once() {
  local data_tier="$1" root_tier="$2"
  local root_pct data_pct
  root_pct="$(pct_for_path /)"
  data_pct="$(work_pct)"

  log "sweep root=${root_pct}% (root tier $root_tier) data=${data_pct}% (data tier $data_tier)"

  local home
  for home in "${RUNNER_HOME_LIST[@]:-}"; do
    [ -n "$home" ] || continue
    if [ ! -d "$home" ]; then
      log "WARN: runner home $home does not exist; skipping it"
      continue
    fi
    if [ "$data_tier" -ge 1 ]; then
      prune_runner_work "$home" "$data_tier"
    fi
    # Repair orphaned action watermarks at EVERY tier, including tier 0: a
    # watermark whose version directory is gone makes actions/checkout skip
    # the download forever, and that must not wait for disk pressure.
    prune_orphan_action_markers "$home/_work/_actions"
  done

  if [ "$root_tier" -ge 1 ]; then
    prune_caches "$root_tier"
  fi

  prune_system "$root_tier" "$data_tier"
}

# ---------------------------------------------------------- interpreter repair
#
# uv keeps its managed CPython trees under
#   <user home>/.local/share/uv/python/cpython-<version>-<platform>/bin/
# and those interpreters are exactly the ones `uv` inspects when it resolves an
# interpreter for a job. A tree whose interpreter lost its execute bit (mode
# 0444 instead of 0755) makes every such resolution fail with
#   Failed to query Python interpreter at .../bin/python3.14
#     cause: Permission denied (os error 13)
# which breaks CI on the fleet even though the interpreter itself is intact and
# the host is otherwise healthy. It is not a disk-pressure problem, so it must
# not be gated on the pressure tiers.
#
# The repair is cheap and idempotent: it walks the handful of managed trees and
# restores the execute bit on any interpreter missing it. It runs on every
# sweep, including tier 0, so a tree that loses the bit again is corrected
# within one timer interval rather than at the next provisioning run.
repair_uv_interpreters() {
  local home tree interp fixed=0
  for home in "${RUNNER_HOME_LIST[@]:-}"; do
    [ -n "$home" ] || continue
    # The managed trees live in the USER's home, one level above the runner
    # directory.
    home="$(dirname "$home")"
    [ -d "$home/.local/share/uv/python" ] || continue
    for tree in "$home"/.local/share/uv/python/*/bin; do
      [ -d "$tree" ] || continue
      for interp in "$tree"/python3.*; do
        [ -f "$interp" ] || continue
        [ -x "$interp" ] && continue
        if [ "$DRY_RUN" = "1" ]; then
          log "would restore the execute bit on $interp"
        else
          # 0755, not `u+x`: uv installs these interpreters 0755, and restoring
          # the mode uv would have installed keeps the tree consistent with its
          # siblings instead of leaving it at 0455.
          if chmod 0755 "$interp" 2>/dev/null; then
            log "restored the execute bit on $interp"
            fixed=1
          else
            log "WARNING: could not restore the execute bit on $interp"
          fi
        fi
      done
    done
  done
  [ "$fixed" = "1" ] && log "repaired uv-managed interpreter execute bits"
  return 0
}

main() {
  local root_pct data_pct pass
  local root_tier data_tier
  local start_root start_data

  if [ "$STATUS_ONLY" = "1" ]; then
    collect_inuse
    root_pct="$(pct_for_path /)"
    data_pct="$(work_pct)"
    log "status: root=${root_pct}% (tier $(tier_for_pct "$root_pct")) data=${data_pct}% (tier $(tier_for_pct "$data_pct")) inuse_paths=${#INUSE_PATHS[@]}"
    df -h / "$DATA_MOUNT" 2>/dev/null | sed 's/^/  /'
    return 0
  fi

  # Mutual exclusion: never run at the same time as another cleanup run on
  # this host.
  acquire_lock || return 0

  # A broken interpreter is not a disk-pressure problem, so this repair runs
  # before the tier loop and is not skipped at tier 0.
  repair_uv_interpreters

  root_tier=0
  data_tier=0

  for pass in $(seq 1 "$MAX_PASSES"); do
    root_pct="$(pct_for_path /)"
    data_pct="$(work_pct)"

    # Each filesystem keeps its own tier, escalated independently.
    root_tier="$(escalate_tier "$root_tier" "$(tier_for_pct "$root_pct")")"
    data_tier="$(escalate_tier "$data_tier" "$(tier_for_pct "$data_pct")")"

    if [ "$root_tier" -eq 0 ] && [ "$data_tier" -eq 0 ]; then
      # Nothing to do. Still run the bounded, always-safe system prune so
      # journal/apt do not creep, but skip every cache and checkout sweep.
      sweep_once 0 0
      return 0
    fi

    # The in-use set is re-collected at the top of EVERY pass: a job that
    # starts while an earlier pass is running must be protected by the next
    # one, not by a snapshot taken before the first pass began.
    collect_inuse

    start_root="$root_pct"
    start_data="$data_pct"
    sweep_once "$data_tier" "$root_tier"

    root_pct="$(pct_for_path /)"
    data_pct="$(work_pct)"

    if [ "$root_pct" -le "$TARGET_PCT" ] && [ "$data_pct" -le "$TARGET_PCT" ]; then
      log "pass $pass: within target (root ${root_pct}%, data ${data_pct}%)"
      return 0
    fi

    if [ "$root_pct" -ge "$start_root" ] && [ "$data_pct" -ge "$start_data" ]; then
      log "pass $pass: no progress (root ${start_root}%->${root_pct}%, data ${start_data}%->${data_pct}%)"
    else
      log "pass $pass: progress (root ${start_root}%->${root_pct}%, data ${start_data}%->${data_pct}%)"
    fi
  done

  root_pct="$(pct_for_path /)"
  data_pct="$(work_pct)"
  if [ "$root_pct" -gt "$TARGET_PCT" ] || [ "$data_pct" -gt "$TARGET_PCT" ]; then
    log "WARNING: still above target after $MAX_PASSES passes (root ${root_pct}%, data ${data_pct}%). Manual cleanup needed: check \$HOME_DIR/.rustup toolchains, \$HOME_DIR/.proto, and \$HOME_DIR/.local."
  fi
}

# Run the routine when the script is executed. When it is sourced — the
# sandbox tests do that to exercise the sweep functions in isolation —
# nothing runs on its own.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
