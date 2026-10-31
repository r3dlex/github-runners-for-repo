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
import platform
import shutil
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


def _make_runnable(binary: Path) -> None:
    """Make a COPIED system binary executable on macOS.

    A copy of an Apple platform binary keeps its signature but is killed
    with SIGKILL the moment it is executed outside its original location:
    the platform loader refuses the relocated signed image, so the process
    dies before it can hold anything. The signature is removed and an
    ad-hoc one applied instead, which is what a locally built binary
    looks like.

    On Linux there is no such restriction, so the copy runs as it is.
    """
    if platform.system() != "Darwin":
        return
    subprocess.run(
        ["codesign", "--remove-signature", str(binary)],
        check=False,
        capture_output=True,
    )
    result = subprocess.run(
        ["codesign", "-s", "-", str(binary)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"could not sign {binary}: {result.stderr}"


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


def _sweep_caches(home: Path, tmp_path: Path, tier: int = 3, dry_run: bool = False) -> str:
    """Run the cache sweep of the routine over the sandbox home.

    The cache sweep is the one that reclaims the shared home: the download
    caches and the installed toolchain trees.
    """
    source = f'source "$CLEANUP_SCRIPT"{" --dry-run" if dry_run else ""}'
    script = f'{source}; prune_caches "$TIER"'
    env = _env(home, tmp_path)
    env.update({"CLEANUP_SCRIPT": str(SCRIPT), "TIER": str(tier)})
    result = subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"cache sweep failed: {result.returncode}\n{result.stderr}"
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
                # The unit directory is moved out of systemd's way: an
                # install that changes the install path while leaving the
                # units in a live systemd directory is refused (it would
                # rewrite the live runner-disk-cleanup.service).
                "UNIT_DIR": str(home.parent / "units"),
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

    def test_the_colon_joined_line_of_the_live_runners_survives(self, tmp_path: Path) -> None:
        """A line is never dropped because one of its fragments is missing.

        The live runners carry a colon-joined line whose fragments
        (/usr/games, /usr/local/games, /snap/bin) do not exist on the
        Colima VM. A cleanup that pruned "missing" fragments — or a whole
        line because a fragment was missing — would silently strip PATH
        entries the runners are supposed to have.
        """
        home = tmp_path / "actions-runner-linux"
        home.mkdir()
        path_file = home / ".path"
        joined = "/usr/games:/usr/local/games:/snap/bin"
        path_file.write_text(f"{joined}\n")
        install_path = tmp_path / "opt" / "bin" / "runner-disk-cleanup.sh"
        install_path.parent.mkdir(parents=True)

        self._update(home, install_path)

        lines = path_file.read_text().splitlines()
        assert joined in lines, (
            f"the colon-joined line must survive the install untouched, got {lines}"
        )

    def test_a_fragment_with_glob_characters_is_not_taken_for_present(self, tmp_path: Path) -> None:
        """The membership test must compare FIXED strings.

        The fragment was interpolated into a `case` pattern, so it was a
        glob: the install directory "opt[1]" made the pattern match the
        DIFFERENT directory "opt1", the entry was judged to be there
        already, and the runner never got the routine on its PATH.
        """
        home = tmp_path / "actions-runner-linux"
        home.mkdir()
        path_file = home / ".path"
        install_path = tmp_path / "opt[1]" / "bin" / "runner-disk-cleanup.sh"
        install_path.parent.mkdir(parents=True)
        # A DIFFERENT directory whose name the glob would match.
        path_file.write_text(f"{tmp_path / 'opt1' / 'bin'}\n")

        self._update(home, install_path)

        content = path_file.read_text()
        assert str(install_path.parent) in content, (
            f"the install directory must be added: {content!r}"
        )

    def test_a_missing_install_directory_is_not_added(self, tmp_path: Path) -> None:
        """The guarantee has to be alive: a dead entry helps nobody.

        The install directory is what the runner resolves the routine by,
        so it has to exist before it goes into a .path file.
        """
        home = tmp_path / "actions-runner-linux"
        home.mkdir()
        path_file = home / ".path"
        path_file.write_text("/usr/local/bin\n")
        install_path = tmp_path / "verify" / "missing" / "runner-disk-cleanup.sh"

        env = os.environ.copy()
        env.update(
            {
                "RUNNER_HOMES": str(home),
                "INSTALL_PATH": str(install_path),
                "UNIT_DIR": str(home.parent / "units"),
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
        assert path_file.read_text() == "/usr/local/bin\n", (
            "a directory that does not exist must not go into a .path file"
        )
        assert "does not exist" in result.stdout, f"the skip has to be logged, got {result.stdout}"


def _installer(overrides: dict[str, str], command: str) -> subprocess.CompletedProcess[str]:
    """Source the installer with the given environment overrides.

    Every knob the installer reads is removed from the inherited
    environment first, so a test can assert what happens when the
    operator did NOT set it.
    """
    env = os.environ.copy()
    for knob in (
        "SANDBOX",
        "SANDBOX_ROOT",
        "RUNNER_HOMES",
        "INSTALL_PATH",
        "UNIT_DIR",
        "SYSTEMD_UNIT_DIRS",
    ):
        env.pop(knob, None)
    env.update(overrides)
    env["INSTALLER"] = str(INSTALLER)
    return subprocess.run(
        ["bash", "-c", command],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


class TestInstallerSandbox:
    """A sandbox run must never mutate the live fleet.

    The live defect: a verification that overrode INSTALL_PATH but not
    RUNNER_HOMES wrote the verification's install directory into the LIVE
    Colima runners' .path file, so every job on them started with a dead
    PATH entry. The installer now fails closed instead of guessing.
    """

    def test_sandbox_refuses_a_runner_home_outside_the_sandbox(self, tmp_path: Path) -> None:
        sandbox = tmp_path / "sandbox"
        live = tmp_path / "actions-runner-linux"
        live.mkdir()
        path_file = live / ".path"
        path_file.write_text("/usr/local/bin\n")

        result = _installer(
            {
                "SANDBOX": "1",
                "SANDBOX_ROOT": str(sandbox),
                "RUNNER_HOMES": str(live),
                "INSTALL_PATH": str(sandbox / "bin" / "runner-disk-cleanup.sh"),
            },
            'source "$INSTALLER"',
        )

        assert result.returncode != 0, (
            "a sandbox run that reaches outside the sandbox root must fail closed"
        )
        assert "sandbox" in result.stdout.lower(), (
            f"the refusal has to say why, got {result.stdout}"
        )
        assert path_file.read_text() == "/usr/local/bin\n", (
            "the runner home outside the sandbox must not be touched"
        )

    def test_sandbox_refuses_an_install_path_outside_the_sandbox(self, tmp_path: Path) -> None:
        sandbox = tmp_path / "sandbox"

        result = _installer(
            {
                "SANDBOX": "1",
                "SANDBOX_ROOT": str(sandbox),
                "INSTALL_PATH": "/usr/local/bin/runner-disk-cleanup.sh",
            },
            'source "$INSTALLER"',
        )

        assert result.returncode != 0, (
            "a sandbox run must not install into the host's /usr/local/bin"
        )
        assert "sandbox" in result.stdout.lower(), (
            f"the refusal has to say why, got {result.stdout}"
        )

    def test_sandbox_forces_the_runner_homes_into_the_sandbox(self, tmp_path: Path) -> None:
        sandbox = tmp_path / "sandbox"

        result = _installer(
            {"SANDBOX": "1", "SANDBOX_ROOT": str(sandbox)},
            'source "$INSTALLER"; printf "HOMES=%s\\n" "${RUNNER_HOME_LIST[*]}"',
        )

        assert result.returncode == 0, result.stderr
        marked = [line for line in result.stdout.splitlines() if line.startswith("HOMES=")]
        assert marked, f"the runner homes have to be reported, got {result.stdout}"
        homes = marked[0][len("HOMES=") :].split()
        assert homes, "the sandbox must still have a runner home"
        for home in homes:
            assert str(sandbox) in home, f"{home} must live inside the sandbox"

    def test_overriding_only_the_install_path_does_not_touch_the_live_runners(
        self, tmp_path: Path
    ) -> None:
        """The live incident, as a regression test.

        RUNNER_HOMES is not set, so the defaults point at the LIVE
        runners. The install directory is a verification directory, so
        without a guard this run would put that directory into the live
        runners' .path file.
        """
        result = _installer(
            {"INSTALL_PATH": str(tmp_path / "verify" / "sandbox" / "runner-disk-cleanup.sh")},
            'source "$INSTALLER"',
        )

        assert result.returncode != 0, (
            "changing the install directory without naming the runner homes "
            "would write it into the LIVE runners' .path file"
        )
        assert "RUNNER_HOMES" in result.stdout, (
            f"the refusal has to name the knob the operator has to set, got {result.stdout}"
        )

    def test_naming_the_runner_homes_explicitly_is_not_enough(self, tmp_path: Path) -> None:
        """BLOCKER 2: the documented flow must not rewrite the live unit.

        The documentation told the operator that naming RUNNER_HOMES was
        enough to make a verification run safe. It is not: the unit
        directory was still the live /etc/systemd/system, so the run
        rewrote the LIVE runner-disk-cleanup.service to execute the
        verification's path. The live timer then failed on every tick and
        the root filesystem filled up.
        """
        home = tmp_path / "actions-runner-linux"
        home.mkdir()
        # The "live" systemd directory, pointed at a sandbox so the test
        # can look at it. SYSTEMD_UNIT_DIRS is the installer's own knob
        # for it; on the host it is the real set of directories systemd
        # reads.
        live_units = tmp_path / "live-systemd"
        live_units.mkdir()

        result = _installer(
            {
                "RUNNER_HOMES": str(home),
                "INSTALL_PATH": str(tmp_path / "verify" / "runner-disk-cleanup.sh"),
                "UNIT_DIR": str(live_units),
                "SYSTEMD_UNIT_DIRS": str(live_units),
            },
            'source "$INSTALLER"',
        )

        assert result.returncode != 0, (
            "a run that changes the install path while its units still go "
            "to a live systemd directory must be refused"
        )
        assert "UNIT_DIR" in result.stdout, (
            f"the refusal has to name the knob the operator has to set, got {result.stdout}"
        )
        assert not (live_units / "runner-disk-cleanup.service").exists(), (
            "the live unit file must not be rewritten by a refused run"
        )
        assert not (live_units / "runner-disk-cleanup.timer").exists(), (
            "the live timer file must not be rewritten by a refused run"
        )

    def test_a_fully_isolated_run_is_allowed(self, tmp_path: Path) -> None:
        """The guard must not block the legitimate isolated run.

        A verification run names the runner homes, the install path AND a
        unit directory that systemd does not read. Nothing live is
        touched, so it is allowed.
        """
        home = tmp_path / "actions-runner-linux"
        home.mkdir()

        result = _installer(
            {
                "RUNNER_HOMES": str(home),
                "INSTALL_PATH": str(tmp_path / "verify" / "runner-disk-cleanup.sh"),
                "UNIT_DIR": str(tmp_path / "units"),
            },
            'source "$INSTALLER"',
        )

        assert result.returncode == 0, f"{result.stdout}{result.stderr}"

    def test_the_live_run_is_still_allowed(self, tmp_path: Path) -> None:
        """The live defaults are a coherent run and must not be refused."""
        result = _installer({}, 'source "$INSTALLER"')

        assert result.returncode == 0, f"{result.stdout}{result.stderr}"

    def test_units_outside_systemd_with_the_live_install_path_are_refused(
        self, tmp_path: Path
    ) -> None:
        """The mirror image: an isolated unit directory cannot enable the
        LIVE timer.

        The unit files would go somewhere systemd never reads, but the
        install path is still the live one, so the run would enable the
        LIVE runner-disk-cleanup.timer while the operator believes the run
        is isolated.
        """
        result = _installer(
            {"UNIT_DIR": str(tmp_path / "units")},
            'source "$INSTALLER"',
        )

        assert result.returncode != 0, (
            "a run that moves the units away from systemd but keeps the live "
            "install path must be refused"
        )
        assert "INSTALL_PATH" in result.stdout, (
            f"the refusal has to name the knob the operator has to set, got {result.stdout}"
        )

    def test_fix_runner_ownership_leaves_a_home_outside_the_sandbox_alone(
        self, tmp_path: Path
    ) -> None:
        """The ownership fix is a recursive chown — it stays in the sandbox."""
        sandbox = tmp_path / "sandbox"
        inside = sandbox / "actions-runner-linux"
        inside.mkdir(parents=True)
        outside = tmp_path / "actions-runner-linux"
        outside.mkdir()
        marker = outside / "keep-me"
        marker.write_text("untouched")

        result = _installer(
            {"SANDBOX": "1", "SANDBOX_ROOT": str(sandbox)},
            # SUDO is emptied so the test can never block on a password
            # prompt: the guard has to fire before any chown is attempted.
            f'source "$INSTALLER"; SUDO=""; RUNNER_HOME_LIST=("{outside}"); fix_runner_ownership',
        )

        assert "outside the sandbox" in result.stdout, (
            f"the ownership fix must refuse a home outside the sandbox, got {result.stdout}"
        )
        assert marker.exists(), "the home outside the sandbox must be left alone"


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
                # The install path is a sandbox path as well: a run that
                # points the unit directory away from systemd while leaving
                # the install path at the live default is a mixed run and
                # is refused (see the installer's guard).
                "INSTALL_PATH": str(tmp_path / "verify" / "runner-disk-cleanup.sh"),
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
                "INSTALL_PATH": str(tmp_path / "verify" / "runner-disk-cleanup.sh"),
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


def _managed_interpreter(home: Path, version: str = "3.11") -> Path:
    """A uv managed CPython tree under the sandbox home."""
    tree = home / ".local" / "share" / "uv" / "python" / f"cpython-{version}-linux-aarch64-gnu"
    (tree / "bin").mkdir(parents=True)
    interpreter = tree / "bin" / "python3"
    interpreter.write_text("#!/bin/sh\n")
    # The interpreter is executable, like the real one: the recency check
    # only looks at executables, and a fixture that is not one would not
    # exercise it.
    interpreter.chmod(0o755)
    return tree


def _mise_install(home: Path, version: str = "3.12") -> Path:
    """A mise-installed interpreter under the sandbox home."""
    install = home / ".local" / "share" / "mise" / "installs" / "python" / version
    (install / "bin").mkdir(parents=True)
    (install / "bin" / "python").write_text("#!/bin/sh\n")
    return install


def _rust_toolchain(home: Path, name: str = "stable-aarch64") -> Path:
    """A rustup toolchain under the sandbox home."""
    toolchain = home / ".rustup" / "toolchains" / name
    (toolchain / "bin").mkdir(parents=True)
    (toolchain / "bin" / "cargo").write_text("#!/bin/sh\n")
    return toolchain


class TestToolchainCaches:
    """The installed toolchain trees are what grows the root filesystem.

    The two Colima runners share ONE home, so every interpreter a job
    resolves is installed once and kept for every job that follows: ten
    managed CPythons, the mise installs and the rust toolchains were all
    still there while the routine swept only the download caches. The root
    filesystem filled up in spite of it.
    """

    def test_a_stale_managed_interpreter_is_pruned(self, runner_home: Path, tmp_path: Path) -> None:
        tree = _managed_interpreter(tmp_path)
        _age(tree, 60)

        _sweep_caches(runner_home, tmp_path, tier=3)

        assert not tree.exists(), (
            "a managed interpreter nobody has touched for months has to be "
            "reclaimed: it is what fills the root filesystem"
        )

    def test_a_stale_mise_install_is_pruned(self, runner_home: Path, tmp_path: Path) -> None:
        install = _mise_install(tmp_path)
        _age(install, 60)

        _sweep_caches(runner_home, tmp_path, tier=3)

        assert not install.exists(), "a stale mise install has to be reclaimed"

    def test_a_stale_rust_toolchain_is_pruned(self, runner_home: Path, tmp_path: Path) -> None:
        toolchain = _rust_toolchain(tmp_path)
        _age(toolchain, 60)

        _sweep_caches(runner_home, tmp_path, tier=3)

        assert not toolchain.exists(), "a stale rust toolchain has to be reclaimed"

    def test_a_fresh_managed_interpreter_survives(self, runner_home: Path, tmp_path: Path) -> None:
        """The age floor still protects a tree that was just installed."""
        tree = _managed_interpreter(tmp_path)

        _sweep_caches(runner_home, tmp_path, tier=3)

        assert tree.exists(), (
            "a managed interpreter that was just installed must survive: the "
            "next job that resolves it would have to download it again"
        )

    def test_a_managed_interpreter_held_by_a_live_process_survives(
        self, runner_home: Path, tmp_path: Path
    ) -> None:
        """The liveness guard applies to the toolchains as well.

        The interpreter of a running job is an open file of that job. A
        sweep that removed it would pull the interpreter out from under the
        process that is executing it.
        """
        tree = _managed_interpreter(tmp_path)
        _age(tree, 60)
        target = tree / "bin" / "python3"

        holder = subprocess.Popen(
            ["bash", "-c", 'exec 9<"$1"; sleep 30', "_", str(target)],
            cwd=tmp_path,
        )
        try:
            time.sleep(0.5)
            _sweep_caches(runner_home, tmp_path, tier=3)
        finally:
            holder.terminate()
            holder.wait()

        assert tree.exists(), "a toolchain a live process holds open must never be pruned"

    def test_the_toolchain_sweep_waits_for_real_pressure(
        self, runner_home: Path, tmp_path: Path
    ) -> None:
        """Nothing is reclaimed while the disk is merely under watch.

        A miss costs a re-download of the whole toolchain, and the routine
        is meant to cost almost nothing while the disk is still fine. The
        toolchain trees are therefore only reclaimed from the elevated tier
        up.
        """
        tree = _managed_interpreter(tmp_path)
        _age(tree, 60)

        _sweep_caches(runner_home, tmp_path, tier=1)

        assert tree.exists(), (
            "the watch tier must not touch the installed toolchains: only a "
            "disk under real pressure justifies a re-download"
        )

    def test_the_user_site_packages_is_never_pruned(
        self, runner_home: Path, tmp_path: Path
    ) -> None:
        """The shims in ~/.local/bin depend on the site-packages.

        ruff, black and the archgate wrapper are installed into the user's
        site-packages and resolved by name. Removing it breaks every tool
        the runners resolve that way.
        """
        site_packages = tmp_path / ".local" / "lib" / "python3.11" / "site-packages"
        (site_packages / "pkg").mkdir(parents=True)
        (site_packages / "pkg" / "__init__.py").write_text("")
        _age(site_packages, 60)

        _sweep_caches(runner_home, tmp_path, tier=3)

        assert site_packages.exists(), (
            "the user's site-packages must never be swept: the ~/.local/bin "
            "shims are installed into it"
        )


class TestCacheExclusions:
    """The caches that are not caches."""

    def test_the_playwright_browsers_are_never_pruned(
        self, runner_home: Path, tmp_path: Path
    ) -> None:
        """The E2E leg needs the exact browsers that were downloaded.

        ~/.cache/ms-playwright holds ~929 MB of chromium, headless shell
        and ffmpeg. The generic ~/.cache sweep only kept it while
        something inside it was fresh, so a few days without a browser
        install was enough for the tree to disappear — and the next E2E
        job then failed with "Executable doesn't exist at
        ~/.cache/ms-playwright/chromium-1208/...".
        """
        browsers = tmp_path / ".cache" / "ms-playwright" / "chromium-1208"
        browsers.mkdir(parents=True)
        (browsers / "chrome").write_text("binary")
        _age(browsers, 60)

        _sweep_caches(runner_home, tmp_path, tier=3)

        assert browsers.exists(), (
            "the Playwright browsers must never be swept: the E2E leg needs "
            "the binaries that were downloaded"
        )

    def test_the_proto_download_cache_is_pruned(self, runner_home: Path, tmp_path: Path) -> None:
        """The protobuf download cache is a pure download cache."""
        cache = tmp_path / ".proto" / "cache"
        cache.mkdir(parents=True)
        (cache / "descriptor.bin").write_text("cache entry")
        _age(cache, 60)

        _sweep_caches(runner_home, tmp_path, tier=3)

        assert not cache.exists(), "the protobuf download cache has to be reclaimed"

    def test_a_cold_cache_is_still_pruned(self, runner_home: Path, tmp_path: Path) -> None:
        """The generic sweep still reclaims everything else under ~/.cache."""
        cache = tmp_path / ".cache" / "something-cold"
        cache.mkdir(parents=True)
        (cache / "entry").write_text("cache entry")
        _age(cache, 60)

        _sweep_caches(runner_home, tmp_path, tier=3)

        assert not cache.exists(), "a cold cache entry has to be reclaimed"


def _has_all_flag(calls: list[str]) -> bool:
    """True when any of the recorded calls carries docker's "all" flag.

    docker spells it "-a", "-af" or "--all" depending on the subcommand,
    so the flag is looked for as a whole word.
    """
    return any(flag in {"-a", "-af", "--all"} for call in calls for flag in call.split())


class TestDockerPruneScope:
    """The docker prune stays inside the promise the documentation makes.

    The routine runs on a daemon that hosts the user's own long-lived
    containers. Only DANGLING images and DANGLING build cache are removed:
    "docker builder prune -a" drops the whole unused build cache, which is
    ~12 GB on the fleet host and rebuilds every image from scratch.
    """

    @staticmethod
    def _prune_system(tmp_path: Path) -> list[str]:
        """Run prune_system with recording stand-ins for the host tools.

        The stand-ins for `rm`, `journalctl`, `apt-get` and `docker` record
        their arguments instead of doing anything, so the test never
        touches the machine it runs on.
        """
        fake_bin = tmp_path / "fake-bin"
        fake_bin.mkdir()
        record = tmp_path / "calls.log"
        for name in ("docker", "rm", "journalctl", "apt-get"):
            stand_in = fake_bin / name
            stand_in.write_text(
                f'#!/bin/sh\nprintf "%s %s\\n" "{name}" "$*" >> "$CALL_RECORD"\nexit 0\n'
            )
            stand_in.chmod(0o755)

        env = _env(tmp_path / "runner", tmp_path)
        env.update(
            {
                "CLEANUP_SCRIPT": str(SCRIPT),
                "PATH": f"{fake_bin}:{env['PATH']}",
                "CALL_RECORD": str(record),
            }
        )
        result = subprocess.run(
            ["bash", "-c", 'source "$CLEANUP_SCRIPT"; prune_system 0 2'],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"prune_system failed: {result.stderr}"
        if not record.exists():
            return []
        return record.read_text().splitlines()

    def test_only_dangling_images_are_pruned(self, tmp_path: Path) -> None:
        calls = self._prune_system(tmp_path)
        image_calls = [c for c in calls if c.startswith("docker image prune")]
        assert image_calls, f"the images have to be pruned: {calls}"
        assert not _has_all_flag(image_calls), (
            f"a tagged image belongs to the user and must not be removed: {calls}"
        )

    def test_the_whole_unused_build_cache_is_not_pruned(self, tmp_path: Path) -> None:
        calls = self._prune_system(tmp_path)
        builder_calls = [c for c in calls if c.startswith("docker builder prune")]
        assert builder_calls, f"the build cache has to be pruned: {calls}"
        assert not _has_all_flag(builder_calls), (
            f"only the DANGLING build cache may be removed: {calls}"
        )

    def test_no_container_is_ever_pruned(self, tmp_path: Path) -> None:
        calls = self._prune_system(tmp_path)
        assert not [c for c in calls if c.startswith("docker container prune")], (
            f"a stopped container is the user's state, not cache: {calls}"
        )


class TestProcCollector:
    """The two /proc collectors have to return the same set.

    The scan runs before every destructive step, so it has to be cheap:
    a readlink fork per open descriptor costs ~490 ms on the fleet host,
    which is minutes of lock time per pass. The fast collector reads the
    whole host with a handful of processes instead — but it may only
    return the same paths, or a live checkout could be deleted.
    """

    @staticmethod
    def _collect(home: Path, tmp_path: Path, function: str) -> list[str]:
        env = _env(home, tmp_path)
        env.update({"CLEANUP_SCRIPT": str(SCRIPT), "COLLECTOR": function})
        script = (
            'source "$CLEANUP_SCRIPT"; '
            "INUSE_PATHS=(); "
            '"$COLLECTOR"; '
            'printf "%s\\n" "${INUSE_PATHS[@]}"'
        )
        result = subprocess.run(
            ["bash", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"collector failed: {result.stderr}"
        return sorted(line for line in result.stdout.splitlines() if line)

    def test_both_collectors_return_the_same_paths(self, runner_home: Path, tmp_path: Path) -> None:
        if not Path("/proc/self").exists():
            pytest.skip("the /proc collector only runs on Linux")

        checkout = runner_home / "_work" / "live-repo"
        checkout.mkdir(parents=True)
        target = checkout / "artifact.bin"
        target.write_text("build output")

        holder = subprocess.Popen(
            ["bash", "-c", 'exec 9<"$1"; sleep 30', "_", str(target)],
            cwd=tmp_path,
        )
        try:
            time.sleep(0.5)
            fast = self._collect(runner_home, tmp_path, "collect_inuse_proc_ls")
            slow = self._collect(runner_home, tmp_path, "collect_inuse_proc_readlink")
        finally:
            holder.terminate()
            holder.wait()

        assert fast == slow, f"the two collectors must return the same paths: {fast} != {slow}"
        assert any(str(target) in path for path in fast), (
            f"the open descriptor must be found: {fast}"
        )


def _fake_proc(tmp_path: Path) -> Path:
    """A synthetic /proc for the liveness collectors.

    PROC_ROOT is the routine's own knob for the process filesystem. The
    liveness rules are the safety net of this routine, so they have to be
    provable on every platform — not only on the Linux fleet, where the
    real /proc happens to exist.
    """
    proc = tmp_path / "proc"
    (proc / "self").mkdir(parents=True)
    return proc


def _fake_pid(proc: Path, pid: str, comm: str = "runner") -> Path:
    """A process directory in the synthetic /proc."""
    d = proc / pid
    d.mkdir(parents=True, exist_ok=True)
    (d / "comm").write_text(f"{comm}\n")
    return d


class TestExecutableLiveness:
    """BLOCKER 1: the executable of a live process must protect its tree.

    The liveness guard could not see a RUNNING executable: both
    collectors only read /proc/<pid>/cwd and /proc/<pid>/fd/*, and
    /proc/<pid>/exe is a magic symlink — the kernel keeps no descriptor
    for the executable image. The toolchain sweep deletes exactly that
    class of path (a managed CPython, a mise install, a rust toolchain),
    so the guard did not protect the paths this routine adds.
    """

    @staticmethod
    def _sweep_caches_with_proc(home: Path, tmp_path: Path, proc: Path, tier: int = 3) -> str:
        """Run the cache sweep against a synthetic /proc."""
        env = _env(home, tmp_path)
        env.update(
            {
                "CLEANUP_SCRIPT": str(SCRIPT),
                "TIER": str(tier),
                "PROC_ROOT": str(proc),
            }
        )
        result = subprocess.run(
            [
                "bash",
                "-c",
                'source "$CLEANUP_SCRIPT"; collect_inuse; prune_caches "$TIER"',
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"cache sweep failed: {result.stderr}"
        return result.stdout

    def test_a_managed_interpreter_a_live_process_runs_survives(
        self, runner_home: Path, tmp_path: Path
    ) -> None:
        """The tree that a job is EXECUTING must never be swept.

        The interpreter of a running job is not an open file of that
        job: the kernel keeps no descriptor for the executable image, so
        /proc/<pid>/fd says nothing about it. Without reading
        /proc/<pid>/exe the sweep deletes the interpreter out from under
        the process that is running it, and every child the job spawns
        afterwards fails with ENOENT.
        """
        tree = _managed_interpreter(tmp_path)
        _age(tree, 60)
        target = tree / "bin" / "python3"

        proc = _fake_proc(tmp_path)
        pid = _fake_pid(proc, "4242")
        (pid / "exe").symlink_to(target)

        output = self._sweep_caches_with_proc(runner_home, tmp_path, proc)

        assert tree.exists(), (
            f"the managed interpreter a live process is running must never be swept: {output}"
        )

    @staticmethod
    def _exec_paths(proc: Path, home: Path, tmp_path: Path, collector: str) -> list[str]:
        env = _env(home, tmp_path)
        env.update({"CLEANUP_SCRIPT": str(SCRIPT), "PROC_ROOT": str(proc)})
        script = (
            'source "$CLEANUP_SCRIPT"; INUSE_PATHS=(); EXEC_PATHS=(); '
            '"$COLLECTOR"; printf "%s\\n" "${EXEC_PATHS[@]:-}"'
        )
        env["COLLECTOR"] = collector
        result = subprocess.run(
            ["bash", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"collector failed: {result.stderr}"
        return [line for line in result.stdout.splitlines() if line]

    def test_both_collectors_see_the_executable_of_a_live_process(
        self, runner_home: Path, tmp_path: Path
    ) -> None:
        """Both collectors have to look at /proc/<pid>/exe.

        The two collectors are proven to return the same set, so both
        have to read the executable — otherwise the fast collector would
        report a live interpreter as free and the slow one would protect
        it, and which one runs depends on whether the host has `ls`.
        """
        tree = _managed_interpreter(tmp_path)
        target = tree / "bin" / "python3"

        proc = _fake_proc(tmp_path)
        pid = _fake_pid(proc, "4242")
        (pid / "exe").symlink_to(target)

        for collector in ("collect_inuse_proc_ls", "collect_inuse_proc_readlink"):
            found = self._exec_paths(proc, runner_home, tmp_path, collector)
            assert str(target) in found, (
                f"{collector} must see the executable of a live process: {found}"
            )

    def test_a_symlink_target_containing_an_arrow_is_not_truncated(
        self, runner_home: Path, tmp_path: Path
    ) -> None:
        """A path that contains " -> " must survive the collection.

        "ls -l" prints "name -> target", and the target was taken after
        the FIRST " -> " in the line. A target that itself contains
        " -> " was truncated, and a truncated path matches nothing — a
        silently broken liveness check.
        """
        weird = tmp_path / "weird -> dir"
        weird.mkdir()

        proc = _fake_proc(tmp_path)
        pid = _fake_pid(proc, "4242")
        (pid / "fd").mkdir()
        (pid / "fd" / "3").symlink_to(weird)

        env = _env(runner_home, tmp_path)
        env.update({"CLEANUP_SCRIPT": str(SCRIPT), "PROC_ROOT": str(proc)})
        script = (
            'source "$CLEANUP_SCRIPT"; INUSE_PATHS=(); EXEC_PATHS=(); '
            'collect_inuse_proc_ls; printf "%s\\n" "${INUSE_PATHS[@]:-}"'
        )
        result = subprocess.run(
            ["bash", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"collector failed: {result.stderr}"
        collected = [line for line in result.stdout.splitlines() if line]
        assert str(weird) in collected, (
            f"a target that contains ' -> ' must not be truncated: {collected}"
        )

    def test_a_running_interpreter_survives_the_toolchain_sweep(
        self, runner_home: Path, tmp_path: Path
    ) -> None:
        """The same rule, with a REAL process.

        The synthetic /proc proves the rule is implemented; this proves
        it end to end: the kernel points /proc/<pid>/exe at the binary
        the process is running, and the sweep has to leave that binary
        alone.
        """
        tree = _managed_interpreter(tmp_path)
        target = tree / "bin" / "python3"
        # A real binary, so the kernel's /proc/<pid>/exe names it. The
        # content is copied and the mode set explicitly: copy2 also
        # copies the extended attributes, which a copy onto another
        # volume refuses.
        shutil.copyfile("/bin/sleep", target)
        target.chmod(0o755)
        _make_runnable(target)
        # The tree is aged AFTER the copy: a fresh file would survive the
        # sweep on its mtime alone and the test would prove nothing.
        _age(tree, 60)

        holder = subprocess.Popen([str(target), "30"], cwd=tmp_path)
        try:
            time.sleep(0.5)
            assert holder.poll() is None, (
                "the fixture binary did not start, so the test would prove "
                f"nothing (exit {holder.poll()})"
            )
            output = _sweep_caches(runner_home, tmp_path, tier=3)
        finally:
            holder.terminate()
            holder.wait()

        assert tree.exists(), (
            f"the interpreter a live process is running must never be swept: {output}"
        )

    def test_a_toolchain_used_recently_is_not_swept(
        self, runner_home: Path, tmp_path: Path
    ) -> None:
        """The second line of defence: a recently RUN interpreter is kept.

        The tree's mtime is the sweep's staleness signal and it says
        nothing about use. A toolchain that a job uses every day was
        installed months ago, so its mtime is old — and it is exactly
        what the sweep deletes. The interpreter's access time is the
        signal that says "it was run", and it is the only one that
        survives the process that ran it exiting.
        """
        tree = _managed_interpreter(tmp_path)
        interpreter = tree / "bin" / "python3"
        _age(tree, 60)
        # Executed just now: the access time is fresh, the mtime is not.
        now = time.time()
        os.utime(interpreter, (now, now - 60 * DAY))

        output = _sweep_caches(runner_home, tmp_path, tier=3)

        assert tree.exists(), (
            f"a toolchain whose interpreter was run today must not be reclaimed: {output}"
        )


class TestOwnerHomeSweep:
    """MEDIUM 1: every owner home is swept, not just the first one.

    The knob takes a LIST. A host with two runner homes owned by two
    different users has two owner homes, and sweeping only the first one
    left the second user's toolchains growing without bound while the
    routine reported green.
    """

    def test_the_toolchains_of_every_owner_home_are_swept(self, tmp_path: Path) -> None:
        owner_a = tmp_path / "owner-a"
        owner_b = tmp_path / "owner-b"
        home_a = owner_a / "actions-runner-linux"
        home_b = owner_b / "actions-runner-linux"
        home_a.mkdir(parents=True)
        home_b.mkdir(parents=True)

        tree_b = _managed_interpreter(owner_b)
        _age(tree_b, 60)

        env = os.environ.copy()
        env.update(
            {
                "RUNNER_HOMES": f"{home_a} {home_b}",
                "HOME_DIR": str(owner_a),
                "DATA_MOUNT": str(tmp_path / "no-data-mount"),
                "TMP_DIR": str(tmp_path / "tmp"),
                "LOCK_FILE": str(tmp_path / "cleanup.lock"),
                "WATCH_PCT": "0",
                "ELEVATED_PCT": "0",
                "CRITICAL_PCT": "0",
                "TARGET_PCT": "0",
                "MAX_PASSES": "1",
                "CLEANUP_SCRIPT": str(SCRIPT),
                "TIER": "3",
            }
        )
        result = subprocess.run(
            ["bash", "-c", 'source "$CLEANUP_SCRIPT"; collect_inuse; prune_caches "$TIER"'],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"cache sweep failed: {result.stderr}"

        assert not tree_b.exists(), (
            f"the owner home of the SECOND runner home must be swept too: {result.stdout}"
        )


class TestSystemPruneGuards:
    """MEDIUM 2: the system prune is guarded by activity, not by hope.

    The apt package index and the crash dumps live outside every watched
    root, so the in-use scan says nothing about them. They used to be
    dropped unconditionally, while the documentation promised that
    every destructive step is guarded.
    """

    @staticmethod
    def _prune_system(tmp_path: Path, comm: str | None) -> list[str]:
        """Run prune_system with recording stand-ins for the host tools.

        `rm`, `journalctl`, `apt-get` and `docker` record their arguments
        instead of doing anything, so the test never touches the machine
        it runs on.
        """
        fake_bin = tmp_path / "fake-bin"
        fake_bin.mkdir()
        record = tmp_path / "calls.log"
        for name in ("docker", "rm", "journalctl", "apt-get"):
            stand_in = fake_bin / name
            stand_in.write_text(
                f'#!/bin/sh\nprintf "%s %s\\n" "{name}" "$*" >> "$CALL_RECORD"\nexit 0\n'
            )
            stand_in.chmod(0o755)

        proc = _fake_proc(tmp_path)
        if comm is not None:
            _fake_pid(proc, "4242", comm)

        env = _env(tmp_path / "runner", tmp_path)
        env.update(
            {
                "CLEANUP_SCRIPT": str(SCRIPT),
                "PATH": f"{fake_bin}:{env['PATH']}",
                "CALL_RECORD": str(record),
                "PROC_ROOT": str(proc),
            }
        )
        result = subprocess.run(
            ["bash", "-c", 'source "$CLEANUP_SCRIPT"; prune_system 2 0'],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"prune_system failed: {result.stderr}"
        if not record.exists():
            return []
        return record.read_text().splitlines()

    def test_the_apt_index_is_kept_while_a_package_manager_runs(self, tmp_path: Path) -> None:
        """A job that installs a package needs the index it installs FROM."""
        calls = self._prune_system(tmp_path, "apt-get")

        assert not [c for c in calls if "/var/lib/apt/lists" in c], (
            f"the apt index must not be dropped while apt is running: {calls}"
        )

    def test_the_apt_index_is_dropped_when_no_package_manager_runs(self, tmp_path: Path) -> None:
        """The guard is an activity check, not a permanent exemption."""
        calls = self._prune_system(tmp_path, "bash")

        assert [c for c in calls if "/var/lib/apt/lists" in c], (
            f"the apt index has to be reclaimed when nothing is installing: {calls}"
        )


class TestRunnerHomePaths:
    """LOW: a runner home is always an absolute path.

    A relative value makes every path derived from it relative as well,
    and the cache sweep would then delete paths relative to the unit's
    working directory (/).
    """

    def test_a_relative_runner_home_is_made_absolute(self, tmp_path: Path) -> None:
        env = _env(tmp_path / "actions-runner-linux", tmp_path)
        env.update({"CLEANUP_SCRIPT": str(SCRIPT), "RUNNER_HOMES": "relative/actions-runner"})
        result = subprocess.run(
            ["bash", "-c", 'source "$CLEANUP_SCRIPT"; printf "%s\\n" "${RUNNER_HOME_LIST[@]}"'],
            env=env,
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        entries = [line for line in result.stdout.splitlines() if line]
        assert entries, "the runner home must be listed"
        for entry in entries:
            assert entry.startswith("/"), (
                f"a relative runner home would make the sweep delete paths relative to /: {entry}"
            )


class TestUnitFileWrite:
    """LOW: the unit files are replaced, not truncated in place.

    `tee` truncates the live unit in place, and a daemon-reload that
    races the write loads a half-written unit. A rename(2) within the
    directory is atomic, so systemd only ever sees the complete unit.
    """

    def test_the_unit_files_are_replaced_atomically(self, tmp_path: Path) -> None:
        unit_dir = tmp_path / "units"
        unit_dir.mkdir()
        service = unit_dir / "runner-disk-cleanup.service"
        service.write_text("old content\n")

        env = os.environ.copy()
        env.update(
            {
                "RUNNER_HOMES": str(tmp_path / "runner"),
                "INSTALL_PATH": str(tmp_path / "verify" / "runner-disk-cleanup.sh"),
                "UNIT_DIR": str(unit_dir),
                "UNIT_FILE": str(service),
                "INSTALLER": str(INSTALLER),
            }
        )
        script = (
            # SUDO is emptied so the test can never block on a password
            # prompt: the unit files are written into a sandbox
            # directory, so no privilege is needed.
            'exec 9<"$UNIT_FILE"; source "$INSTALLER"; SUDO=""; install_units; '
            'printf "READER:"; cat <&9'
        )
        result = subprocess.run(
            ["bash", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"install_units failed: {result.stderr}"

        reader_output = result.stdout.split("READER:", 1)[-1]
        assert "old content" in reader_output, (
            "the unit file was truncated in place instead of replaced: a "
            f"reader that holds it open sees the new content: {reader_output!r}"
        )
        assert not list(unit_dir.glob(".runner-disk-cleanup.*")), (
            "the temporary unit files must be renamed away, not left behind"
        )
