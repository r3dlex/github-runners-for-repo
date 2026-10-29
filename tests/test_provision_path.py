"""Sandbox tests for the runner .path update in provision-host.sh.

The runner's `.path` file is what puts uv, bun and archgate on the PATH of
every job the runner service starts. The function that maintains it used to
enumerate the runner homes with a `"$HOME"/actions-runner*` glob, which
matches NOTHING when the provision runs as root: HOME is /root, while the
fleet's Linux runners live in the guest user's home
(/home/<user>/actions-runner*). The `.path` file was therefore never
updated on the fleet.

provision-host.sh installs packages when it runs, so it cannot be sourced
the way the disk cleanup tests source their script. The function under
test is extracted from the file and driven on its own against a
throw-away runner home.
"""

from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

PROVISION = (
    Path(__file__).resolve().parents[1]
    / "github_runners_for_repo"
    / "scripts"
    / "provision-host.sh"
)


def _function_source(*names: str) -> str:
    """Extract the named shell functions from provision-host.sh.

    The functions are copied verbatim out of the script, so the test drives
    the shipped code and not a copy of it.
    """
    lines = PROVISION.read_text().splitlines()
    chunks = []
    for name in names:
        start = next(i for i, line in enumerate(lines) if line.startswith(f"{name}() {{"))
        end = next(i for i in range(start, len(lines)) if lines[i] == "}")
        chunks.append("\n".join(lines[start : end + 1]))
    return "\n".join(chunks)


def _update_path_file(
    home: Path,
    env_extra: dict[str, str] | None = None,
    parents: Path | None = None,
) -> str:
    """Run ensure_user_path_in_runner with $HOME pointing at `home`.

    $RUNNER_HOME_PARENTS is ALWAYS pinned to a sandbox directory. The
    shipped default scans the platform's own home parent, and the real
    fleet runners live under exactly that path — a test that let the
    default through would rewrite the live runners' .path file (and, on
    the self-hosted runner used by this repo's CI, the running runner's
    own file).
    """
    script = "\n".join(
        [
            "log() { printf '[provision] %s\\n' \"$*\"; }",
            _function_source("path_file_has", "home_parents", "ensure_user_path_in_runner"),
            "ensure_user_path_in_runner",
        ]
    )
    env = os.environ.copy()
    env.update({"HOME": str(home)})
    env.update(
        {"RUNNER_HOME_PARENTS": str(parents if parents is not None else home / "no-home-parents")}
    )
    env.update(env_extra or {})
    result = subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"failed: {result.stderr}"
    return result.stdout


def _home_parents(env_extra: dict[str, str] | None = None) -> list[str]:
    """The home parents the shipped code scans, with its own default."""
    script = "\n".join(
        [
            _function_source("home_parents"),
            "home_parents",
        ]
    )
    env = os.environ.copy()
    env.pop("RUNNER_HOME_PARENTS", None)
    env.update(env_extra or {})
    result = subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"failed: {result.stderr}"
    return result.stdout.split()


class TestRunnerHomeEnumeration:
    """NEW-6: the runner homes must be found wherever they actually are."""

    def test_root_run_updates_a_runner_home_outside_home(self, tmp_path: Path) -> None:
        """The fleet layout: runner home under a guest user's home.

        The provision runs as root, so $HOME=/root and the old glob found
        nothing. The runner home is one level below a home parent.
        """
        fleet = tmp_path / "homes" / "guest"
        runner = fleet / "actions-runner-linux"
        runner.mkdir(parents=True)
        path_file = runner / ".path"
        path_file.write_text("/usr/local/bin:/usr/bin\n")

        root_home = tmp_path / "root"
        root_home.mkdir()

        _update_path_file(root_home, parents=tmp_path / "homes")

        content = path_file.read_text()
        assert str(fleet / ".local/bin") in content, (
            f"the runner's .path file was not updated: {content!r}"
        )
        assert str(fleet / ".bun/bin") in content
        assert str(fleet / ".archgate/bin") in content

    def test_entries_point_at_the_runner_owner_not_the_caller(self, tmp_path: Path) -> None:
        """The bin dirs belong to the user the runner runs as.

        A root-run provision has HOME=/root, so writing /root/.local/bin
        into the guest runner's .path would update the file and still leave
        the job without uv.
        """
        fleet = tmp_path / "homes" / "guest"
        runner = fleet / "actions-runner-linux"
        runner.mkdir(parents=True)
        path_file = runner / ".path"
        path_file.write_text("/usr/local/bin:/usr/bin\n")

        root_home = tmp_path / "root"
        root_home.mkdir()

        _update_path_file(root_home, parents=tmp_path / "homes")

        content = path_file.read_text()
        assert str(root_home / ".local/bin") not in content, (
            f"the caller's home must not leak into the runner's .path: {content!r}"
        )

    def test_runner_home_under_home_is_still_updated(self, tmp_path: Path) -> None:
        """The macOS layout: the runner and its owner share a home."""
        user = tmp_path / "Users" / "me"
        runner = user / "actions-runner"
        runner.mkdir(parents=True)
        path_file = runner / ".path"
        path_file.write_text("/usr/local/bin:/usr/bin\n")

        _update_path_file(user)

        content = path_file.read_text()
        assert str(user / ".local/bin") in content
        assert str(user / ".bun/bin") in content

    def test_runners_homes_knob_is_honoured(self, tmp_path: Path) -> None:
        """An explicit runner home is used even when nothing matches."""
        runner = tmp_path / "elsewhere" / "actions-runner-linux"
        runner.mkdir(parents=True)
        path_file = runner / ".path"
        path_file.write_text("/usr/local/bin:/usr/bin\n")

        _update_path_file(tmp_path, {"RUNNER_HOMES": str(runner)})

        content = path_file.read_text()
        assert str(runner.parent / ".local/bin") in content

    def test_no_runner_home_is_a_no_op(self, tmp_path: Path) -> None:
        """Nothing to update must not fail the provision."""
        output = _update_path_file(tmp_path)
        assert "updated" not in output

    def test_running_twice_does_not_duplicate_the_entries(self, tmp_path: Path) -> None:
        """The .path file must not grow on every run."""
        fleet = tmp_path / "homes" / "guest"
        runner = fleet / "actions-runner-linux"
        runner.mkdir(parents=True)
        path_file = runner / ".path"
        path_file.write_text("/usr/local/bin:/usr/bin\n")

        _update_path_file(tmp_path, parents=tmp_path / "homes")
        _update_path_file(tmp_path, parents=tmp_path / "homes")

        content = path_file.read_text()
        assert content.count(str(fleet / ".local/bin")) == 1, (
            f"the entry must be added exactly once: {content!r}"
        )
        assert len(content.splitlines()) == 2, (
            f"the .path file must not grow on every run: {content!r}"
        )

    def test_a_fragment_that_ends_a_line_is_not_re_added(self, tmp_path: Path) -> None:
        """A fragment at the end of a line is already in the file.

        The .path file holds one PATH fragment per line, so the fragment
        that ends a line has no colon after it. A whole-file membership
        test cannot see it and re-adds it on every run, so the file grows
        by a line per provision.
        """
        fleet = tmp_path / "homes" / "guest"
        runner = fleet / "actions-runner-linux"
        runner.mkdir(parents=True)
        path_file = runner / ".path"
        path_file.write_text(
            "/usr/local/bin:/usr/bin\n"
            f"{fleet / '.local/bin'}:{fleet / '.bun/bin'}\n"
            f"{fleet / '.archgate/bin'}\n"
        )

        _update_path_file(tmp_path, parents=tmp_path / "homes")

        content = path_file.read_text()
        assert content.count(str(fleet / ".local/bin")) == 1, content
        assert content.count(str(fleet / ".bun/bin")) == 1, content
        assert content.count(str(fleet / ".archgate/bin")) == 1, content
        assert len(content.splitlines()) == 3, (
            f"the .path file must not grow when every dir is already there: {content!r}"
        )

    def test_a_symlinked_home_does_not_duplicate_the_entries(self, tmp_path: Path) -> None:
        """The same runner found twice must not double the fragments.

        On the Linux fleet /home/<user>.linux is a symlink to
        /home/<user>.guest, so the scan finds the SAME runner twice —
        once through each name. With the symlinked name the bin dirs are
        spelled differently, the membership test does not find what the
        first pass wrote, and every fragment is prepended a second time.
        """
        homes = tmp_path / "homes"
        guest = homes / "guest"
        runner = guest / "actions-runner-linux"
        runner.mkdir(parents=True)
        path_file = runner / ".path"
        path_file.write_text("/usr/local/bin:/usr/bin\n")
        (homes / "guest.linux").symlink_to(guest)

        _update_path_file(tmp_path, parents=homes)

        content = path_file.read_text()
        for fragment in (".local/bin", ".bun/bin", ".archgate/bin"):
            assert content.count(fragment) == 1, (
                f"{fragment} must be written exactly once: {content!r}"
            )


class TestHomeParentSelection:
    """Only the home parent of THIS platform may be scanned.

    On macOS /home is an autofs mount. A glob under it blocks until the
    automounter gives up, so a provision that scans both /home and /Users
    hangs on the macOS runners — it never finishes, so it never updates
    the .path file either.
    """

    def test_only_the_platform_parent_is_scanned_by_default(self) -> None:
        parents = _home_parents()
        expected = "/Users" if platform.system() == "Darwin" else "/home"
        assert parents == [expected], f"exactly one home parent may be scanned, got {parents}"

    def test_the_other_platforms_parent_is_never_scanned(self) -> None:
        other = "/home" if platform.system() == "Darwin" else "/Users"
        assert other not in _home_parents()

    def test_an_explicit_parent_overrides_the_default(self) -> None:
        parents = _home_parents({"RUNNER_HOME_PARENTS": "/srv/homes"})
        assert parents == ["/srv/homes"]
