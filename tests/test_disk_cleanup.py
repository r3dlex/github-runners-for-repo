"""Sandbox tests for the runner disk-cleanup routine.

These drive github_runners_for_repo/scripts/cleanup-host-disk.sh directly
against a throw-away runner home. They are the regression tests for the two
defects that made the routine destructive:

  - a tier-3 sweep could delete a LIVE job's checkout, because the age
    window collapsed to zero (removing the mtime backstop) and because the
    liveness filter only looked at the runner's own command line, which
    never holds the checkout;
  - an orphaned action watermark ("<version>.completed") bricked
    actions/checkout forever, because the sweep only removed directories
    and the runner skips the download when the watermark is present.

The script is SOURCED by these tests, not executed, so the sweep functions
can be driven in isolation without the system-level prune (journal, apt,
docker) running against the machine that hosts the test.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "github_runners_for_repo"
    / "scripts"
    / "cleanup-host-disk.sh"
)

INSTALLER = (
    Path(__file__).resolve().parents[1]
    / "github_runners_for_repo"
    / "scripts"
    / "install-disk-cleanup.sh"
)

DAY = 86400.0


def _age(path: Path, days: float) -> None:
    """Backdate a path and everything under it.

    The sweep decides "stale" with `find -newermt`, which looks at the
    directory AND its contents, so both have to be backdated for a fixture
    to look old.
    """
    stamp = time.time() - days * DAY
    for target in [path, *sorted(path.rglob("*"))]:
        os.utime(target, (stamp, stamp))


def _env(home: Path, tmp_path: Path) -> dict[str, str]:
    """Environment that pins the routine to the sandbox.

    Every threshold is 0, so `tier_for_pct` returns the top tier whatever
    the real disk usage is: the tests exercise the most aggressive sweep.
    """
    env = os.environ.copy()
    env.update(
        {
            "RUNNER_HOMES": str(home),
            "HOME_DIR": str(tmp_path),
            # No data mount: the work is scored on the filesystem that holds
            # the sandbox, which is what the percentages above assume.
            "DATA_MOUNT": str(tmp_path / "no-data-mount"),
            "TMP_DIR": str(tmp_path / "tmp"),
            "LOCK_FILE": str(tmp_path / "cleanup.lock"),
            "WATCH_PCT": "0",
            "ELEVATED_PCT": "0",
            "CRITICAL_PCT": "0",
            "TARGET_PCT": "0",
            "MAX_PASSES": "1",
        }
    )
    return env


def _sweep(
    home: Path,
    tmp_path: Path,
    tier: int = 3,
    dry_run: bool = False,
    collect: bool = True,
) -> str:
    """Run the sweep path of the routine over the sandbox runner home.

    This is the same sequence `main` runs: collect the live paths, prune the
    work tree at the given tier, then repair the orphaned action
    watermarks.

    `collect` controls the liveness scan. It is switched off in the tests
    that are about the AGE floor, so that a checkout can only survive
    because of its age and not because something else happens to be holding
    it — the two protections are proven independently.
    """
    source = f'source "$CLEANUP_SCRIPT"{" --dry-run" if dry_run else ""}'
    collect_cmd = "collect_inuse; " if collect else ""
    script = (
        f"{source}; "
        f"{collect_cmd}"
        'prune_runner_work "$RUNNER_HOME" "$TIER"; '
        'prune_orphan_action_markers "$RUNNER_HOME/_work/_actions"'
    )
    env = _env(home, tmp_path)
    env.update(
        {
            "CLEANUP_SCRIPT": str(SCRIPT),
            "RUNNER_HOME": str(home),
            "TIER": str(tier),
        }
    )
    result = subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"sweep failed: {result.returncode}\n{result.stderr}"
    return result.stdout


@pytest.fixture
def runner_home(tmp_path: Path) -> Path:
    home = tmp_path / "actions-runner-linux"
    (home / "_work").mkdir(parents=True)
    (tmp_path / "tmp").mkdir()
    return home


class TestActionWatermark:
    """BLOCKER 2: an orphaned watermark must never brick actions/checkout.

    The runner writes the watermark as a SIBLING of the version directory
    (_work/_actions/<owner>/<repo>/v1.completed next to .../v1/). The
    version directory is a directory, so a sweep that prunes directories
    leaves the watermark behind — and the runner then skips the download
    with the action manifest missing, forever.
    """

    def test_pruned_version_takes_its_watermark_with_it(
        self, runner_home: Path, tmp_path: Path
    ) -> None:
        actions = runner_home / "_work" / "_actions" / "owner" / "repo"
        version = actions / "v1"
        version.mkdir(parents=True)
        marker = actions / "v1.completed"
        marker.write_text("")
        _age(actions, 30)

        _sweep(runner_home, tmp_path, collect=False)

        assert not version.exists(), "the stale action version should be pruned"
        assert not marker.exists(), (
            "the watermark of a pruned action version must be removed with it, "
            "otherwise actions/checkout skips the download forever"
        )

    def test_watermark_survives_while_its_version_directory_exists(
        self, runner_home: Path, tmp_path: Path
    ) -> None:
        actions = runner_home / "_work" / "_actions" / "owner" / "repo"
        version = actions / "v2"
        version.mkdir(parents=True)
        marker = actions / "v2.completed"
        marker.write_text("")

        _sweep(runner_home, tmp_path, collect=False)

        assert version.exists()
        assert marker.exists(), (
            "a watermark whose version directory is still there is doing its "
            "job and must not be removed"
        )

    def test_orphan_watermark_from_an_earlier_run_is_repaired(
        self, runner_home: Path, tmp_path: Path
    ) -> None:
        actions = runner_home / "_work" / "_actions" / "owner" / "repo"
        actions.mkdir(parents=True)
        marker = actions / "v3.completed"
        marker.write_text("")

        _sweep(runner_home, tmp_path, collect=False)

        assert not marker.exists(), (
            "a watermark whose version directory is already gone is the brick "
            "this routine has to repair"
        )


class TestTier3Floor:
    """BLOCKER 1: a tier-3 sweep must never delete a live job's checkout."""

    def test_recent_checkout_survives_tier3(self, runner_home: Path, tmp_path: Path) -> None:
        checkout = runner_home / "_work" / "recent-repo"
        checkout.mkdir()
        (checkout / "file.txt").write_text("work in progress")

        # The liveness scan is off: the checkout survives because of the
        # age floor, not because something happens to be holding it.
        _sweep(runner_home, tmp_path, tier=3, collect=False)

        assert checkout.exists(), (
            "the tier-3 age floor must keep a checkout the runner is still writing to"
        )

    def test_live_checkout_held_by_a_process_is_not_pruned(
        self, runner_home: Path, tmp_path: Path
    ) -> None:
        checkout = runner_home / "_work" / "live-repo"
        checkout.mkdir()
        # Old enough that only the liveness check can save it.
        _age(checkout, 30)

        # actions/runner never changes its own working directory: the
        # checkout is held by the CHILD it spawned, whose cwd is the
        # checkout. This is what the /proc scan has to find.
        holder = subprocess.Popen(["sleep", "30"], cwd=checkout)
        try:
            time.sleep(0.5)
            output = _sweep(runner_home, tmp_path, tier=3)
        finally:
            holder.terminate()
            holder.wait()

        assert checkout.exists(), (
            f"a checkout held by a live process must never be pruned: {output}"
        )

    def test_stale_unused_checkout_is_still_pruned_at_tier3(
        self, runner_home: Path, tmp_path: Path
    ) -> None:
        checkout = runner_home / "_work" / "abandoned-repo"
        checkout.mkdir()
        _age(checkout, 30)

        _sweep(runner_home, tmp_path, tier=3)

        assert not checkout.exists(), (
            "the age floor must not stop the sweep from reclaiming an abandoned checkout"
        )


class TestDryRun:
    """MAJOR 3: --dry-run has to be dry."""

    def test_dry_run_removes_nothing(self, runner_home: Path, tmp_path: Path) -> None:
        checkout = runner_home / "_work" / "abandoned-repo"
        checkout.mkdir()
        _age(checkout, 30)

        output = _sweep(runner_home, tmp_path, tier=3, dry_run=True, collect=False)

        assert checkout.exists(), "--dry-run must not remove anything"
        assert "would prune" in output


class TestRunnerPathFile:
    """MAJOR 5: the install directory belongs in .path exactly once.

    The membership test used to be a colon-delimited token match against
    the whole file while the installer writes one entry per line, so the
    entry never matched and was prepended again on every single run (three
    lines, then four, then five).
    """

    @staticmethod
    def _update(home: Path, install_path: Path) -> None:
        env = os.environ.copy()
        env.update(
            {
                "RUNNER_HOMES": str(home),
                "INSTALL_PATH": str(install_path),
            }
        )
        result = subprocess.run(
            ["bash", "-c", 'source "$INSTALLER"; ensure_runner_path'],
            env={**env, "INSTALLER": str(INSTALLER)},
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"ensure_runner_path failed: {result.stderr}"

    def test_install_dir_is_added_exactly_once(self, tmp_path: Path) -> None:
        home = tmp_path / "actions-runner-linux"
        home.mkdir()
        path_file = home / ".path"
        path_file.write_text("/usr/local/bin:/usr/bin\n")
        install_path = tmp_path / "opt" / "bin" / "runner-disk-cleanup.sh"
        install_path.parent.mkdir(parents=True)

        self._update(home, install_path)
        self._update(home, install_path)

        lines = path_file.read_text().splitlines()
        assert lines.count(str(install_path.parent)) == 1, (
            f"the install directory must be added exactly once, got {lines}"
        )
        assert len(lines) == 2, f"the .path file must not grow on every run: {lines}"

    def test_entry_inside_a_colon_joined_line_is_not_duplicated(self, tmp_path: Path) -> None:
        home = tmp_path / "actions-runner-linux"
        home.mkdir()
        path_file = home / ".path"
        install_path = tmp_path / "opt" / "bin" / "runner-disk-cleanup.sh"
        install_path.parent.mkdir(parents=True)
        # The format the runners actually have: several directories on one
        # colon-joined line.
        path_file.write_text(f"{install_path.parent}:/usr/bin\n")

        self._update(home, install_path)

        lines = path_file.read_text().splitlines()
        assert lines == [f"{install_path.parent}:/usr/bin"], (
            f"an entry that is already there must not be added again, got {lines}"
        )


class TestTiers:
    """The tier ladder itself."""

    def test_tier3_uses_a_one_unit_floor_not_zero(self, tmp_path: Path) -> None:
        script = (
            'source "$CLEANUP_SCRIPT"; '
            'scaled_age "$BASE_STALE_HOURS" 3; '
            'scaled_age "$BASE_ACTIONS_DAYS" 3'
        )
        env = _env(tmp_path / "actions-runner-linux", tmp_path)
        env["CLEANUP_SCRIPT"] = str(SCRIPT)
        result = subprocess.run(
            ["bash", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.split() == ["1", "1"], (
            "tier 3 must floor the age window at one unit, never zero"
        )

    def test_tier2_halves_the_window(self, tmp_path: Path) -> None:
        script = 'source "$CLEANUP_SCRIPT"; scaled_age "$BASE_STALE_HOURS" 2'
        env = _env(tmp_path / "actions-runner-linux", tmp_path)
        env["CLEANUP_SCRIPT"] = str(SCRIPT)
        result = subprocess.run(
            ["bash", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "3"


def _uv_tree(tmp_path: Path) -> tuple[Path, Path]:
    """Build a fake uv managed-Python tree.

    uv keeps its managed interpreters under
    ``<user home>/.local/share/uv/python/<tree>/bin/``, and the repair looks
    for them one level ABOVE the runner home, so the runner home has to sit
    in the same user home.

    Returns the runner home and the tree's bin directory.
    """
    user_home = tmp_path / "home"
    runner_home = user_home / "actions-runner-linux"
    runner_home.mkdir(parents=True)
    bin_dir = (
        user_home
        / ".local"
        / "share"
        / "uv"
        / "python"
        / "cpython-3.14.5-linux-aarch64-gnu"
        / "bin"
    )
    bin_dir.mkdir(parents=True)
    return runner_home, bin_dir


def _repair(runner_home: Path, tmp_path: Path, dry_run: bool = False) -> str:
    """Run repair_uv_interpreters against the fake tree."""
    env = _env(runner_home, tmp_path)
    env["CLEANUP_SCRIPT"] = str(SCRIPT)
    source = f'source "$CLEANUP_SCRIPT"{" --dry-run" if dry_run else ""}'
    result = subprocess.run(
        ["bash", "-c", f"{source}; repair_uv_interpreters"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"repair failed: {result.returncode}\n{result.stderr}"
    return result.stdout


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


class TestUvInterpreterRepair:
    """A managed interpreter that lost its execute bit breaks every uv call.

    uv inspects its managed interpreters when it resolves one for a job. A
    tree whose interpreter is mode 0444 makes that inspection fail with
    "Permission denied (os error 13)", which breaks CI on the fleet even
    though the interpreter itself is intact. It is not a disk-pressure
    problem, so the repair is not gated on the pressure tiers.
    """

    def test_non_executable_interpreter_is_repaired(self, tmp_path: Path) -> None:
        runner_home, bin_dir = _uv_tree(tmp_path)
        interp = bin_dir / "python3.14"
        interp.write_text("#!/bin/sh\n")
        interp.chmod(0o444)

        output = _repair(runner_home, tmp_path)

        assert _mode(interp) == 0o755, (
            f"the execute bit must be restored, mode is {oct(_mode(interp))}"
        )
        assert "restored the execute bit" in output

    def test_dry_run_reports_but_does_not_mutate(self, tmp_path: Path) -> None:
        runner_home, bin_dir = _uv_tree(tmp_path)
        interp = bin_dir / "python3.14"
        interp.write_text("#!/bin/sh\n")
        interp.chmod(0o444)

        output = _repair(runner_home, tmp_path, dry_run=True)

        assert _mode(interp) == 0o444, "--dry-run must not change the mode"
        assert "would restore the execute bit" in output

    def test_non_interpreter_file_is_left_alone(self, tmp_path: Path) -> None:
        runner_home, bin_dir = _uv_tree(tmp_path)
        other = bin_dir / "README"
        other.write_text("not an interpreter")
        other.chmod(0o444)

        _repair(runner_home, tmp_path)

        assert _mode(other) == 0o444, "a file that is not an interpreter must not be touched"

    def test_second_run_repairs_nothing(self, tmp_path: Path) -> None:
        runner_home, bin_dir = _uv_tree(tmp_path)
        interp = bin_dir / "python3.14"
        interp.write_text("#!/bin/sh\n")
        interp.chmod(0o444)

        _repair(runner_home, tmp_path)
        output = _repair(runner_home, tmp_path)

        assert "restored the execute bit" not in output, (
            "the repair must be idempotent: the second run has nothing to do"
        )

    def test_executable_interpreter_is_left_alone(self, tmp_path: Path) -> None:
        runner_home, bin_dir = _uv_tree(tmp_path)
        interp = bin_dir / "python3.14"
        interp.write_text("#!/bin/sh\n")
        interp.chmod(0o755)

        output = _repair(runner_home, tmp_path)

        assert _mode(interp) == 0o755
        assert "restored the execute bit" not in output


class TestNeverPruned:
    """Directories the sweep must never touch, whatever their age."""

    def test_self_update_staging_dir_is_never_pruned(
        self, runner_home: Path, tmp_path: Path
    ) -> None:
        staging = runner_home / "_work" / "_update"
        staging.mkdir(parents=True)
        (staging / "bin.2.334.0").mkdir()
        _age(staging, 30)

        _sweep(runner_home, tmp_path, tier=3, collect=False)

        assert staging.exists(), (
            "_work/_update is where the runner stages a self-update: deleting "
            "it mid-update aborts the copy and can leave a runner that cannot "
            "start"
        )

    def test_recent_worker_log_is_kept(self, runner_home: Path, tmp_path: Path) -> None:
        diag = runner_home / "_diag"
        diag.mkdir()
        worker_log = diag / "Worker_2026-10-28.log"
        worker_log.write_text("runner worker log")

        _sweep(runner_home, tmp_path, tier=3, collect=False)

        assert worker_log.exists(), (
            "the worker logs are the evidence for the 'assigned, zero steps' "
            "signature; they must not be wiped regardless of disk pressure"
        )

    def test_fresh_update_archive_is_kept(self, runner_home: Path, tmp_path: Path) -> None:
        archive = runner_home / "actions-runner-linux-x64-2.334.0.tar.gz"
        archive.write_text("runner archive")

        _sweep(runner_home, tmp_path, tier=1, collect=False)

        assert archive.exists(), (
            "the runner downloads its own update archive into the runner "
            "home; deleting a fresh one aborts the update it belongs to"
        )


class TestRunnerOwnerDerivation:
    """The owner of the runner home is only a proxy for the runner user.

    A root-owned runner home is the failure state the installer repairs. The
    owner of the directory cannot be the answer in that case: the recursive
    chown becomes a no-op and the write check trivially passes, so the
    install reports success while the non-root runner service still cannot
    write. The runner service's own unit names the real user.
    """

    @staticmethod
    def _resolve(
        tmp_path: Path, home_owner: str, unit_user: str | None
    ) -> subprocess.CompletedProcess[str]:
        unit_dir = tmp_path / "units"
        unit_dir.mkdir(exist_ok=True)
        if unit_user is not None:
            (unit_dir / "actions.runner.test.service").write_text(f"[Service]\nUser={unit_user}\n")
        runner_home = tmp_path / "runner"
        runner_home.mkdir(exist_ok=True)
        env = os.environ.copy()
        env.update(
            {
                "RUNNER_HOMES": str(runner_home),
                "UNIT_DIR": str(unit_dir),
                "INSTALLER": str(INSTALLER),
            }
        )
        script = (
            'source "$INSTALLER"; '
            f"dir_owner() {{ echo {home_owner}; }}; "
            "dir_group() { echo root; }; "
            'runner_owner_and_group "${RUNNER_HOME_LIST[0]}"'
        )
        return subprocess.run(
            ["bash", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_root_owned_home_uses_the_runner_service_user(self, tmp_path: Path) -> None:
        current_user = subprocess.run(
            ["id", "-un"], capture_output=True, text=True, check=True
        ).stdout.strip()

        result = self._resolve(tmp_path, "root", current_user)

        assert result.returncode == 0, result.stderr
        resolved = result.stdout.strip().splitlines()[-1].split()
        assert resolved[0] == current_user, (
            f"a root-owned runner home must resolve to the runner service's user, got {resolved}"
        )

    def test_root_owned_home_without_a_unit_is_refused(self, tmp_path: Path) -> None:
        result = self._resolve(tmp_path, "root", None)

        assert result.returncode != 0, (
            "without a runner service unit the real user cannot be known, so "
            "the install must refuse rather than chown to root and report "
            "success"
        )
        assert "root-owned" in result.stdout

    def test_normal_owner_is_used_unchanged(self, tmp_path: Path) -> None:
        current_user = subprocess.run(
            ["id", "-un"], capture_output=True, text=True, check=True
        ).stdout.strip()

        result = self._resolve(tmp_path, current_user, None)

        assert result.returncode == 0, result.stderr
        resolved = result.stdout.strip().splitlines()[-1].split()
        assert resolved[0] == current_user

    def test_service_unit_user_is_parsed_with_spaces(self, tmp_path: Path) -> None:
        unit_dir = tmp_path / "units"
        unit_dir.mkdir()
        current_user = subprocess.run(
            ["id", "-un"], capture_output=True, text=True, check=True
        ).stdout.strip()
        (unit_dir / "actions.runner.test.service").write_text(f"[Service]\nUser = {current_user}\n")
        runner_home = tmp_path / "runner"
        runner_home.mkdir(exist_ok=True)
        env = os.environ.copy()
        env.update(
            {
                "RUNNER_HOMES": str(runner_home),
                "UNIT_DIR": str(unit_dir),
                "INSTALLER": str(INSTALLER),
            }
        )
        script = 'source "$INSTALLER"; runner_service_user'
        result = subprocess.run(
            ["bash", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.stdout.strip() == current_user


class TestInUseSnapshot:
    """NEW-7: the liveness check is re-evaluated, not a one-shot snapshot.

    The set of live paths is collected at the top of a pass. A job that
    picks up a checkout AFTER that snapshot is not in the set, so a sweep
    that decides on the snapshot alone deletes the checkout of a job that
    is running right now. The check therefore has to be re-run immediately
    before every destructive step.
    """

    @staticmethod
    def _sweep_with_late_liveness(home: Path, tmp_path: Path, live: Path, tier: int = 3) -> str:
        """Sweep in which `live` becomes held AFTER the pass snapshot.

        `collect_inuse` is replaced with a deterministic double: the first
        call — the snapshot taken at the top of the pass — reports nothing,
        every later call reports `live` as held by a running job. A sweep
        that decides on the snapshot alone removes the checkout; a sweep
        that re-collects immediately before the destructive step keeps it.
        """
        script = "\n".join(
            [
                'source "$CLEANUP_SCRIPT"',
                "_calls=0",
                "collect_inuse() {",
                "  _calls=$((_calls + 1))",
                '  if [ "$_calls" -ge 2 ]; then',
                '    INUSE_PATHS=("$LIVE_CHECKOUT")',
                "  else",
                "    INUSE_PATHS=()",
                "  fi",
                "}",
                "collect_inuse",
                'prune_runner_work "$RUNNER_HOME" "$TIER"',
            ]
        )
        env = _env(home, tmp_path)
        env.update(
            {
                "CLEANUP_SCRIPT": str(SCRIPT),
                "RUNNER_HOME": str(home),
                "TIER": str(tier),
                "LIVE_CHECKOUT": str(live),
            }
        )
        result = subprocess.run(
            ["bash", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"sweep failed: {result.stderr}"
        return result.stdout

    def test_a_checkout_that_becomes_live_after_the_snapshot_survives(
        self, runner_home: Path, tmp_path: Path
    ) -> None:
        """A job that starts during the pass must keep its checkout."""
        checkout = runner_home / "_work" / "late-repo"
        checkout.mkdir()
        (checkout / "file.txt").write_text("work in progress")
        # Old enough that only the liveness check can save it.
        _age(checkout, 30)

        output = self._sweep_with_late_liveness(runner_home, tmp_path, checkout, tier=3)

        assert checkout.exists(), (
            "a checkout that a running job picked up during the sweep must "
            f"not be deleted: {output}"
        )

    def test_a_file_held_open_by_a_live_process_protects_the_checkout(
        self, runner_home: Path, tmp_path: Path
    ) -> None:
        """Protection comes from HOLDING the path, not from naming it.

        The holder's working directory is somewhere else and its command
        line mentions nothing about the checkout: the only thing that ties
        it to the checkout is the open file descriptor, which is what the
        /proc (Linux) and lsof (macOS) scans look for.
        """
        checkout = runner_home / "_work" / "fd-repo"
        checkout.mkdir()
        target = checkout / "artifact.bin"
        target.write_text("build output")
        _age(checkout, 30)

        holder = subprocess.Popen(
            ["bash", "-c", 'exec 9<"$1"; sleep 30', "_", str(target)],
            cwd=tmp_path,
        )
        try:
            time.sleep(0.5)
            output = _sweep(runner_home, tmp_path, tier=3)
        finally:
            holder.terminate()
            holder.wait()

        assert checkout.exists(), (
            f"a checkout whose file is held open by a live process must not be pruned: {output}"
        )

    def test_a_process_that_only_names_the_path_does_not_protect_it(
        self, runner_home: Path, tmp_path: Path
    ) -> None:
        """The detection is not a command-line match.

        A process whose command line mentions the checkout but holds
        nothing open in it is not a live job. Documenting this pins the
        contract: protection comes from the working directory or an open
        descriptor, so the scan cannot be fooled into protecting a stale
        checkout by name.
        """
        checkout = runner_home / "_work" / "named-repo"
        checkout.mkdir()
        _age(checkout, 30)

        holder = subprocess.Popen(
            ["bash", "-c", "sleep 30", "_", str(checkout)],
            cwd=tmp_path,
        )
        try:
            time.sleep(0.5)
            output = _sweep(runner_home, tmp_path, tier=3)
        finally:
            holder.terminate()
            holder.wait()

        assert not checkout.exists(), (
            "a checkout that nothing holds open is prunable even when a "
            f"process mentions it on its command line: {output}"
        )


class TestSweepExclusions:
    """NEW-7: the runner's own directories are excluded BEFORE the prune.

    The exclusion is a `continue` in the candidate loop, so the directory
    is never handed to the pruner at all — not "offered and then skipped".
    """

    @staticmethod
    def _excluded(runner_home: Path) -> list[str]:
        return ["_tool", "_actions", "_temp", "_PipelineMapping", "_update"]

    def test_the_runner_s_own_directories_are_never_offered_to_the_pruner(
        self, runner_home: Path, tmp_path: Path
    ) -> None:
        """Every excluded directory survives, and is never even considered.

        The directories are left empty on purpose: the routine prunes the
        CHILDREN of _temp, _tool and _actions by design (per-job temp dirs,
        tool cache, action versions), so only the roots themselves are
        excluded. An empty, stale root is exactly what the checkout loop
        would delete if the exclusion were missing.
        """
        for name in self._excluded(runner_home):
            directory = runner_home / "_work" / name
            directory.mkdir(parents=True, exist_ok=True)
            _age(directory, 30)

        output = _sweep(runner_home, tmp_path, tier=3)

        for name in self._excluded(runner_home):
            directory = runner_home / "_work" / name
            assert directory.exists(), f"{name} must never be pruned"
            assert str(directory) not in output, (
                f"{name} must be excluded before the destructive step, not "
                f"offered to it and skipped afterwards: {output}"
            )
