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
