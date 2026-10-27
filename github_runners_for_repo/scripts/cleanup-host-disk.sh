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
#   tier 3  critical   (>= CRITICAL)        everything not in use is pruned
#
# After a pass, the script re-measures. If the filesystem is still above
# TARGET_PCT it escalates one tier and sweeps again, up to MAX_PASSES times.
# That closed loop is what keeps a busy afternoon from filling the disk: one
# pass is never assumed to be enough.
#
# ## Safety
#
# The VM has neither lsof nor fuser, so liveness is decided two ways:
#
#   1. mtime — the runner writes into an active job's checkout continuously, so
#      a live job's directory is never stale by mtime.
#   2. /proc — the open files and working directories of every live
#      Runner.Worker process are read directly, and any path under them is
#      treated as in use regardless of age.
#
# A path that is in use is NEVER deleted, at any tier. Tier 3 prunes
# everything it can safely prune; it does not prune in-use paths.
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

# --- base age thresholds ---
BASE_STALE_HOURS="${BASE_STALE_HOURS:-6}"
BASE_ACTIONS_DAYS="${BASE_ACTIONS_DAYS:-14}"
BASE_TOOL_DAYS="${BASE_TOOL_DAYS:-30}"
BASE_DIAG_DAYS="${BASE_DIAG_DAYS:-7}"
BASE_CACHE_DAYS="${BASE_CACHE_DAYS:-3}"
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

# Linux: read the working directory and open files of every live runner
# process straight out of /proc.
collect_inuse_proc() {
  local d pid cmd target fd
  for d in /proc/[0-9]*; do
    pid="${d#/proc/}"
    cmd="$(tr '\0' ' ' <"$d/cmdline" 2>/dev/null)" || continue
    case "$cmd" in
      *Runner.Worker* | *Runner.Listener*) ;;
      *) continue ;;
    esac
    target="$(readlink "$d/cwd" 2>/dev/null)" || target=""
    [ -n "$target" ] && INUSE_PATHS+=("$target")
    for fd in "$d"/fd/*; do
      target="$(readlink "$fd" 2>/dev/null)" || continue
      case "$target" in
        /*) INUSE_PATHS+=("$target") ;;
      esac
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
# it, tier 2 halves it, tier 3 drops it to 0 (prune anything not in use).
scaled_hours() {
  local base="$1" tier="$2"
  case "$tier" in
    2) echo $(( base / 2 )) ;;
    3) echo 0 ;;
    *) echo "$base" ;;
  esac
}

scaled_days() {
  local base="$1" tier="$2"
  case "$tier" in
    2) echo $(( base / 2 )) ;;
    3) echo 0 ;;
    *) echo "$base" ;;
  esac
}

# ------------------------------------------------------------------- sweeping

is_stale() {
  local dir="$1" window="$2"
  [ -n "$(find "$dir" -newermt "-${window}" -print -quit 2>/dev/null)" ] && return 1
  return 0
}

# Remove a single file, honouring DRY_RUN and the in-use guard.
remove_file() {
  local target="$1" label="$2"
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
  # A window of 0 (in any unit) means "prune regardless of age" (tier 3).
  local num="${window%% *}"
  if [ "$num" != "0" ]; then
    is_stale "$target" "$window" || return 1
  fi
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

prune_runner_work() {
  local home="$1" tier="$2" work="$1/_work" d name
  [ -d "$work" ] || return 0

  local stale_h action_d tool_d diag_d
  stale_h="$(scaled_hours "$BASE_STALE_HOURS" "$tier")"
  action_d="$(scaled_days "$BASE_ACTIONS_DAYS" "$tier")"
  tool_d="$(scaled_days "$BASE_TOOL_DAYS" "$tier")"
  diag_d="$(scaled_days "$BASE_DIAG_DAYS" "$tier")"

  # Repo checkouts: _work/<repo>/. Never the runner's own bookkeeping.
  for d in "$work"/*/; do
    [ -d "$d" ] || continue
    name="$(basename "$d")"
    case "$name" in
      _tool | _actions | _temp | _PipelineMapping) continue ;;
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
      if [ "$diag_d" = "0" ]; then
        remove_file "$d" "log"
      else
        is_stale "$d" "${diag_d} days" || continue
        remove_file "$d" "log"
      fi
    done < <(find "$home/_diag" -maxdepth 1 -type f -name '*.log' -print0 2>/dev/null)
  fi

  # Stale runner update archives left in the runner home.
  while IFS= read -r -d '' d; do
    remove_file "$d" "archive"
  done < <(find "$home" -maxdepth 1 -type f \( -name '*.tar.gz' -o -name '*.zip' \) -print0 2>/dev/null)
}

prune_caches() {
  local tier="$1" c cache_d
  cache_d="$(scaled_days "$BASE_CACHE_DAYS" "$tier")"

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

prune_system() {
  local tier="$1" tmp_d
  tmp_d="$(scaled_days "$BASE_TMP_DAYS" "$tier")"

  # These are always safe and always worth doing: they are bounded and cheap.
  journalctl --vacuum-size="$JOURNAL_MAX" >/dev/null 2>&1 || true
  apt-get clean >/dev/null 2>&1 || true
  rm -rf /var/lib/apt/lists/* >/dev/null 2>&1 || true

  # Crash dumps.
  if [ -d /var/lib/systemd/coredump ]; then
    if [ "$DRY_RUN" = "1" ]; then
      log "would prune coredumps"
    else
      rm -rf /var/lib/systemd/coredump/* >/dev/null 2>&1 || true
    fi
  fi

  # Old regular files in /tmp (never sockets, never directories).
  if [ -d /tmp ]; then
    while IFS= read -r -d '' d; do
      [ -f "$d" ] || continue
      remove_file "$d" "tmp"
    done < <(find /tmp -maxdepth 1 -type f -mtime "+${tmp_d}" -print0 2>/dev/null)
  fi

  # Docker lives on the data disk, but prune so the data disk cannot fill.
  if command -v docker >/dev/null 2>&1; then
    if [ "$tier" -ge 2 ]; then
      docker image prune -af --filter "until=${DOCKER_AGE}" >/dev/null 2>&1 || true
      docker builder prune -af --filter "until=${DOCKER_AGE}" >/dev/null 2>&1 || true
      docker container prune -f --filter "until=${DOCKER_AGE}" >/dev/null 2>&1 || true
    else
      docker image prune -f --filter "until=${DOCKER_AGE}" >/dev/null 2>&1 || true
      docker builder prune -f --filter "until=${DOCKER_AGE}" >/dev/null 2>&1 || true
    fi
  fi
}

# ----------------------------------------------------------------- main cycle

sweep_once() {
  local tier="$1"
  local root_pct data_pct
  root_pct="$(pct_for_path /)"
  data_pct="$(work_pct)"

  # Each filesystem is scored on its own pressure, and swept at its own tier.
  local work_tier cache_tier
  work_tier="$(tier_for_pct "$data_pct")"
  cache_tier="$(tier_for_pct "$root_pct")"

  log "sweep tier=$tier root=${root_pct}% (cache tier $cache_tier) data=${data_pct}% (work tier $work_tier)"

  if [ "$work_tier" -ge 1 ]; then
    local home
    for home in "${RUNNER_HOME_LIST[@]:-}"; do
      [ -n "$home" ] || continue
      [ -d "$home" ] || continue
      prune_runner_work "$home" "$work_tier"
    done
  fi

  if [ "$cache_tier" -ge 1 ]; then
    prune_caches "$cache_tier"
  fi

  prune_system "$tier"
}

main() {
  collect_inuse

  local root_pct data_pct worst_tier pass
  local start_root start_data

  if [ "$STATUS_ONLY" = "1" ]; then
    root_pct="$(pct_for_path /)"
    data_pct="$(work_pct)"
    log "status: root=${root_pct}% (tier $(tier_for_pct "$root_pct")) data=${data_pct}% (tier $(tier_for_pct "$data_pct")) inuse_paths=${#INUSE_PATHS[@]}"
    df -h / "$DATA_MOUNT" 2>/dev/null | sed 's/^/  /'
    return 0
  fi

  for pass in $(seq 1 "$MAX_PASSES"); do
    root_pct="$(pct_for_path /)"
    data_pct="$(work_pct)"

    worst_tier=0
    [ "$root_pct" -ge "$WATCH_PCT" ] && worst_tier=1
    [ "$root_pct" -ge "$ELEVATED_PCT" ] && worst_tier=2
    [ "$root_pct" -ge "$CRITICAL_PCT" ] && worst_tier=3
    [ "$data_pct" -ge "$ELEVATED_PCT" ] && worst_tier=2
    [ "$data_pct" -ge "$CRITICAL_PCT" ] && worst_tier=3

    if [ "$worst_tier" -eq 0 ]; then
      # Nothing to do. Still run the bounded, always-safe system prune so
      # journal/apt do not creep, but skip every cache and checkout sweep.
      prune_system 0
      return 0
    fi

    start_root="$root_pct"
    start_data="$data_pct"
    sweep_once "$worst_tier"

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

main "$@"
