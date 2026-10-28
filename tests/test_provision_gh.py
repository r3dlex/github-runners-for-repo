"""Tests for the GitHub CLI install in the host provisioning script.

scripts/provision-host.sh has to put `gh` on the self-hosted Linux runners:
the GitHub-hosted `ubuntu-24.04` images ship it, a bare Colima VM does not,
and a workflow that calls `gh api` then dies with "gh: command not found"
(exit code 127) while the runner itself looks healthy.

The script is SOURCED by these tests, not executed, so the install can be
driven against a sandbox with a stubbed `curl` instead of against the host:
the release API and the release tarball are served from the sandbox, and
the install lands in a temporary directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "github_runners_for_repo"
    / "scripts"
    / "provision-host.sh"
)

VERSION = "2.103.3"
ARCH = "arm64"
ASSET = f"gh_{VERSION}_linux_{ARCH}.tar.gz"

# The release the script installs when nothing else is asked for, and the
# digest upstream publishes for its arm64 asset. The script records that
# digest, so the download is checked against a constant the repository
# controls instead of against a checksum file that arrives from the same
# origin as the tarball.
PINNED_VERSION = "v2.103.3"
PINNED_ARM64_SHA256 = "ee32df2323c8847f5fac6cb6b4b69ca75828e9329a8f22f677021f7b3306ce63"
PINNED_AMD64_SHA256 = "4c806978ffce1cec70853302a56d70ac6da4f19bae7adca4e1a8ab00633dd91d"

# The release payload the GitHub API returns for cli/cli/releases/latest.
RELEASE_API = {
    "tag_name": f"v{VERSION}",
    "name": VERSION,
    "assets": [{"name": ASSET}],
}

# A `curl` stub: it serves the release payload for the API call and copies
# the release tarball for the download. Every call is logged so a test can
# assert that a second run does not download anything again.
CURL_STUB = """#!/usr/bin/env bash
set -euo pipefail
url=""
out=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
printf '%s\\n' "$url" >> "$STUB_CALL_LOG"
case "$url" in
  *api.github.com*)
    if [ -n "${STUB_API_FAILS:-}" ]; then
      printf 'api unreachable\\n' >&2
      exit 22
    fi
    cat "$STUB_API_JSON"
    ;;
  *checksums.txt*)
    if [ -z "${STUB_CHECKSUMS:-}" ]; then
      printf 'not found\\n' >&2
      exit 22
    fi
    cat "$STUB_CHECKSUMS" > "$out"
    ;;
  *) cat "$STUB_TARBALL" > "$out" ;;
esac
"""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_release_tarball(tmp_path: Path, arch: str = ARCH, version: str = VERSION) -> Path:
    """Build a fake `gh` release tarball with the upstream layout."""
    root = tmp_path / "release" / f"gh_{version}_linux_{arch}"
    bindir = root / "bin"
    bindir.mkdir(parents=True)
    binary = bindir / "gh"
    binary.write_text(f"#!/bin/sh\necho 'gh version {version} (2026-01-01)'\n")
    binary.chmod(0o755)
    tarball = tmp_path / f"gh_{version}_linux_{arch}.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(root, arcname=root.name)
    return tarball


def _serve_release(env: dict[str, str], tmp_path: Path, version: str, arch: str = ARCH) -> str:
    """Point the sandbox at a release the script has no recorded digest for.

    The tarball and the checksum file that describes it are the real
    thing as far as the script is concerned: the digest in the checksum
    file is the digest of the tarball the sandbox serves.
    """
    tarball = _write_release_tarball(tmp_path, arch, version)
    digest = _sha256(tarball)
    checksums = tmp_path / f"checksums-{version}.txt"
    checksums.write_text(f"{digest}  gh_{version}_linux_{arch}.tar.gz\n")
    env["STUB_TARBALL"] = str(tarball)
    env["STUB_CHECKSUMS"] = str(checksums)
    return digest


# The system binaries the provisioning script shells out to. The sandbox
# links exactly these into a private bin dir: whether the host happens to
# have node, gh, uv or python3 installed must not decide which branch the
# script takes or which binary it runs.
REQUIRED_BINARIES = (
    "bash",
    "cat",
    "chmod",
    "chown",
    "dirname",
    "env",
    "grep",
    # GNU tar shells out to a standalone gzip for `-z`; the bsdtar on macOS
    # has gzip built in, so a missing gzip is invisible on a laptop and
    # only shows up on the Linux runner as
    # "tar (child): gzip: Cannot exec: No such file or directory".
    "gzip",
    "head",
    "id",
    "install",
    "mkdir",
    "mktemp",
    "rm",
    "sed",
    "sh",
    "stat",
    "tar",
    "uname",
)

# The digest of the download has to be computed with a real sha256
# implementation. GNU coreutils ships sha256sum, the BSD tools ship
# shasum; the sandbox links whichever the host has.
SHA256_BINARIES = ("sha256sum", "shasum")

# Not every test needs them, but they are cheap when the host has them.
OPTIONAL_BINARIES = ("cp", "sudo", "tail")


def _curated_bin(tmp_path: Path) -> Path:
    """A bin dir that holds only the binaries the script needs.

    The host's own toolchain is left out on purpose. A runner with
    /usr/bin/node on PATH would otherwise make `command -v node` succeed,
    the provisioning would take its "node already present" branch, and
    the test would quietly stop testing the install at all.
    """
    sysbin = tmp_path / "sysbin"
    sysbin.mkdir(exist_ok=True)
    host_path = os.environ.get("PATH", "")
    for name in REQUIRED_BINARIES:
        target = shutil.which(name, path=host_path)
        assert target is not None, f"the sandbox needs {name} and this host does not have it"
        link = sysbin / name
        if not link.exists():
            link.symlink_to(target)
    sha256 = [name for name in SHA256_BINARIES if shutil.which(name, path=host_path)]
    assert sha256, "the sandbox needs sha256sum or shasum and this host has neither"
    for name in sha256:
        link = sysbin / name
        if not link.exists():
            link.symlink_to(shutil.which(name, path=host_path))
    for name in OPTIONAL_BINARIES:
        target = shutil.which(name, path=host_path)
        if target is None:
            continue
        link = sysbin / name
        if not link.exists():
            link.symlink_to(target)
    return sysbin


def _assert_sandbox_shape(
    env: dict[str, str], *, stubbed: tuple[str, ...], absent: tuple[str, ...]
) -> None:
    """Fail loudly when the sandbox can reach the host's toolchain.

    The stubs have to win, and the tools the tests must NOT see have to be
    out of reach. A host that breaks that shape would make the test
    silently test nothing, so it is an assertion and not a comment.
    """
    stub_dir = env["STUB_DIR"]
    for command in absent:
        found = shutil.which(command, path=env["PATH"])
        assert found is None, f"{command} must not be reachable in the sandbox, found {found}"
    for command in stubbed:
        found = shutil.which(command, path=env["PATH"])
        assert found is not None, f"the {command} stub is missing from the sandbox"
        assert found.startswith(stub_dir), f"{command} must resolve to the stub, found {found}"


# `tar` and `install` are wrapped so a test can prove that the install
# stops before the archive is unpacked and before the binary is written.
TAR_STUB = """#!/usr/bin/env bash
printf 'tar %s\\n' "$*" >> "$STUB_TAR_LOG"
exec "{real_tar}" "$@"
"""

INSTALL_STUB = """#!/usr/bin/env bash
printf 'install %s\\n' "$*" >> "$STUB_INSTALL_LOG"
exec "{real_install}" "$@"
"""


def _sandbox(tmp_path: Path, arch: str = ARCH) -> dict[str, str]:
    """Environment that pins the install to the sandbox.

    `curl` is stubbed, `SUDO` is pinned to "env" so the install runs as the
    test user instead of asking for a password, and `HOME` is empty so the
    runner user falls back to the current one. The PATH holds only the
    binaries the script shells out to, so what the host happens to have
    installed cannot change what the script does.
    """
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir(exist_ok=True)
    curl = stub_dir / "curl"
    curl.write_text(CURL_STUB)
    curl.chmod(0o755)

    api_json = tmp_path / "release.json"
    api_json.write_text(json.dumps(RELEASE_API))

    sysbin = _curated_bin(tmp_path)
    host_path = os.environ.get("PATH", "")

    tar = stub_dir / "tar"
    tar.write_text(TAR_STUB.format(real_tar=shutil.which("tar", path=host_path)))
    tar.chmod(0o755)
    install = stub_dir / "install"
    install.write_text(INSTALL_STUB.format(real_install=shutil.which("install", path=host_path)))
    install.chmod(0o755)

    tarball = _write_release_tarball(tmp_path, arch)
    digest = _sha256(tarball)
    checksums = tmp_path / "checksums.txt"
    checksums.write_text(f"{digest}  gh_{VERSION}_linux_{arch}.tar.gz\n")

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{stub_dir}{os.pathsep}{sysbin}",
            "HOME": str(tmp_path / "home"),
            "SUDO": "env",
            "GH_INSTALL_PATH": str(tmp_path / "usr-local-bin" / "gh"),
            "STUB_API_JSON": str(api_json),
            "STUB_TARBALL": str(tarball),
            "STUB_CHECKSUMS": str(checksums),
            "STUB_PINNED_SHA256": digest,
            "STUB_CALL_LOG": str(tmp_path / "curl-calls.log"),
            "STUB_TAR_LOG": str(tmp_path / "tar-calls.log"),
            "STUB_INSTALL_LOG": str(tmp_path / "install-calls.log"),
            "STUB_DIR": str(stub_dir),
        }
    )
    (tmp_path / "home").mkdir(exist_ok=True)
    _assert_sandbox_shape(
        env,
        stubbed=("curl", "tar", "install"),
        absent=("node", "npm", "gh", "uv", "python3"),
    )
    return env


def _install_script(env: dict[str, str], *, pin: bool = True) -> str:
    """The script that installs gh against the sandbox.

    The recorded digest is replaced by the digest of the fake release
    tarball: the sandbox serves its own tarball, and the digest of that
    tarball is of course not the one upstream publishes. Everything else
    is the production path — the comparison, the failure and the order in
    which the check runs.

    With `pin=False` the production digest table is left alone: the
    release under test then is not in it, which is what the fail-closed
    behaviour is about.
    """
    override = 'gh_expected_sha256() { printf "%s" "$STUB_PINNED_SHA256"; }; ' if pin else ""
    return f'source "{SCRIPT}"; {override}uname_m=aarch64; ensure_gh_linux'


def _run(script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _calls(env: dict[str, str]) -> list[str]:
    log = Path(env["STUB_CALL_LOG"])
    if not log.exists():
        return []
    return [line for line in log.read_text().splitlines() if line]


def _downloads(env: dict[str, str], asset: str = ASSET) -> list[str]:
    """The release downloads of one run — the asset itself."""
    return [c for c in _calls(env) if c.endswith(asset)]


# A `sudo` stub: it records the command it was asked to run and then runs
# it, dropping the flags sudo handles itself.
#
# The user sudo was asked to switch to is handed on in $STUB_SUDO_USER:
# the sandbox cannot change the user itself, so the identity the
# escalated command runs as has to be passed to it instead.
SUDO_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$STUB_SUDO_LOG"
sudo_user=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    -E|-n|-H) shift ;;
    -u) sudo_user="$2"; shift 2 ;;
    --) shift; break ;;
    *) break ;;
  esac
done
export STUB_SUDO_USER="$sudo_user"
exec "$@"
"""


def _sudo_stub(env: dict[str, str]) -> None:
    """Put a recording `sudo` into the sandbox and point $SUDO at it.

    The sandbox pins $SUDO to "env" so the install runs as the test
    user; that pin cannot switch to another user, so a check that has to
    run AS the runner user needs a real escalation command here.
    """
    stub_dir = Path(env["STUB_DIR"])
    path = stub_dir / "sudo"
    path.write_text(SUDO_STUB)
    path.chmod(0o755)
    env["SUDO"] = "sudo"
    env["STUB_SUDO_LOG"] = str(stub_dir.parent / "sudo-calls.log")


# An `id` stub that claims to be root. Only `-u` is faked, so the script
# derives an empty escalation command; every other query is answered by
# the real id. The real id is looked up on the host instead of being
# hardcoded, so the stub works wherever the test runs.
def _id_stub() -> str:
    real_id = shutil.which("id", path=os.environ.get("PATH", ""))
    assert real_id is not None, "the sandbox needs id and this host does not have it"
    return f"""#!/usr/bin/env bash
if [ "${{1:-}}" = "-u" ]; then
  printf '0\\n'
  exit 0
fi
exec "{real_id}" "$@"
"""


# A `stat` stub that reports the runner user as the owner of a runner
# home. The real `stat` reports whoever created the directory — the test
# user — and the point of the check is that the runner user comes from the
# runner home and not from whoever runs the script.
def _stat_stub() -> str:
    real_stat = shutil.which("stat", path=os.environ.get("PATH", ""))
    assert real_stat is not None, "the sandbox needs stat and this host does not have it"
    return f"""#!/usr/bin/env bash
for arg in "$@"; do
  case "$arg" in
    *actions-runner*)
      printf '%s\\n' "$STUB_RUNNER_OWNER"
      exit 0
      ;;
  esac
done
exec "{real_stat}" "$@"
"""


# A `curl` stub for the piped remote install: the NodeSource script is
# replaced by a marker so the test can see that it really ran.
CURL_PIPE_STUB = """#!/usr/bin/env bash
printf 'echo remote-install-script-ran\\n'
"""

# `apt-get` and `npm` stubs: the provisioning only has to get past them.
NOOP_STUB = """#!/usr/bin/env bash
exit 0
"""


def _node_sandbox(tmp_path: Path, *, as_root: bool, with_sudo: bool) -> dict[str, str]:
    """Environment for driving `ensure_node_linux` without touching the host.

    `as_root` fakes `id -u` so the script derives an empty escalation
    command — the way `gh-runners provision` is normally run. `with_sudo`
    installs a `sudo` stub that records what it was asked to run.
    """
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir(exist_ok=True)

    def _stub(name: str, body: str) -> None:
        path = stub_dir / name
        path.write_text(body)
        path.chmod(0o755)

    _stub("curl", CURL_PIPE_STUB)
    _stub("apt-get", NOOP_STUB)
    _stub("npm", NOOP_STUB)
    if as_root:
        _stub("id", _id_stub())
    if with_sudo:
        _stub("sudo", SUDO_STUB)

    sysbin = _curated_bin(tmp_path)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{stub_dir}{os.pathsep}{sysbin}",
            "HOME": str(tmp_path / "home"),
            "NPM_PREFIX": str(tmp_path / "npm-prefix"),
            "STUB_SUDO_LOG": str(tmp_path / "sudo-calls.log"),
            "STUB_DIR": str(stub_dir),
        }
    )
    env.pop("SUDO", None)
    (tmp_path / "home").mkdir(exist_ok=True)
    _assert_sandbox_shape(
        env,
        stubbed=("curl", "apt-get", "npm"),
        absent=("node", "gh", "uv", "python3"),
    )
    return env


def _fake_gh(path: Path, version: str) -> None:
    """Put a gh at the install path, the way a hand-provisioned host has."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\necho 'gh version {version} (2026-01-01)'\n")
    path.chmod(0o755)


def _root_host_with_runner_home(tmp_path: Path, env: dict[str, str], owner: str) -> Path:
    """Make the sandbox look like a root host that has a runner home.

    `id -u` reports 0 — provisioning runs as root — and `stat` reports the
    runner user as the owner of the runner home. The runner home itself
    is a plain directory: what matters is who owns it.

    The runner home lives under $HOME, the way the provisioning script
    finds it: the script looks for the runner dirs in the home of the
    user that runs it, so a runner home anywhere else would not be found
    at all.
    """
    stub_dir = Path(env["STUB_DIR"])
    for name, body in (("id", _id_stub()), ("stat", _stat_stub())):
        path = stub_dir / name
        path.write_text(body)
        path.chmod(0o755)
    env["STUB_RUNNER_OWNER"] = owner

    runner_home = Path(env["HOME"]) / "actions-runner-linux"
    runner_home.mkdir(parents=True)
    return runner_home


class TestScript:
    def test_script_parses(self) -> None:
        result = subprocess.run(
            ["bash", "-n", str(SCRIPT)], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, result.stderr

    def test_install_target_is_on_the_default_job_path(self, tmp_path: Path) -> None:
        """gh has to resolve from the PATH a GitHub Actions job gets."""
        env = _sandbox(tmp_path)
        # The defaults, not the sandbox overrides.
        del env["GH_INSTALL_PATH"]
        script = f'source "{SCRIPT}"; printf "%s\\n" "$GH_INSTALL_PATH" "$DEFAULT_JOB_PATH"'
        result = _run(script, env)
        assert result.returncode == 0, result.stderr
        install_path, job_path = result.stdout.split("\n")[:2]
        assert install_path == "/usr/local/bin/gh"
        assert "/usr/local/bin" in job_path.split(":")


class TestArchitecture:
    """The wrong asset name is a 404, so the mapping has to be exact."""

    def _arch(self, tmp_path: Path, machine: str) -> str:
        script = f'source "{SCRIPT}"; uname_m={machine}; gh_linux_arch'
        result = _run(script, _sandbox(tmp_path))
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    def test_aarch64_maps_to_arm64(self, tmp_path: Path) -> None:
        assert self._arch(tmp_path, "aarch64") == "arm64"

    def test_x86_64_maps_to_amd64(self, tmp_path: Path) -> None:
        assert self._arch(tmp_path, "x86_64") == "amd64"

    def test_unsupported_architecture_fails(self, tmp_path: Path) -> None:
        script = f'source "{SCRIPT}"; uname_m=sparc64; gh_linux_arch'
        result = _run(script, _sandbox(tmp_path))
        assert result.returncode != 0
        assert "unsupported architecture" in result.stdout


class TestVersion:
    """The release is pinned: a moving target is not an install."""

    def test_the_default_version_is_pinned(self, tmp_path: Path) -> None:
        env = _sandbox(tmp_path)
        script = f'source "{SCRIPT}"; printf "%s\\n" "$GH_VERSION"; gh_version'
        result = _run(script, env)
        assert result.returncode == 0, result.stderr
        pinned, resolved = result.stdout.split("\n")[:2]
        assert pinned == PINNED_VERSION
        assert resolved == VERSION

    def test_the_pinned_release_is_not_looked_up(self, tmp_path: Path) -> None:
        """A pinned release must not depend on the release API."""
        env = _sandbox(tmp_path)
        script = f'source "{SCRIPT}"; gh_version'
        result = _run(script, env)
        assert result.returncode == 0, result.stderr
        assert "api.github.com" not in "\n".join(_calls(env))

    def test_the_release_api_is_used_only_for_latest(self, tmp_path: Path) -> None:
        env = _sandbox(tmp_path)
        env["GH_VERSION"] = "latest"
        script = f'source "{SCRIPT}"; gh_version'
        result = _run(script, env)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == VERSION
        assert "https://api.github.com/repos/cli/cli/releases/latest" in _calls(env)

    def test_pinned_version_wins(self, tmp_path: Path) -> None:
        env = _sandbox(tmp_path)
        env["GH_VERSION"] = "v2.100.0"
        script = f'source "{SCRIPT}"; gh_version'
        result = _run(script, env)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "2.100.0"


class TestPinnedDigest:
    """The digest has to be recorded here, not fetched with the asset."""

    def test_the_pinned_release_has_a_recorded_digest(self, tmp_path: Path) -> None:
        """The recorded digests are the ones upstream publishes.

        Both of them are checked by value: a format check would pass for
        any 64 hex characters, so a typo or a swapped constant would
        reach the fleet unnoticed.
        """
        env = _sandbox(tmp_path)
        script = (
            f'source "{SCRIPT}"; gh_expected_sha256 2.103.3 arm64; '
            'printf "\\n"; gh_expected_sha256 2.103.3 amd64'
        )
        result = _run(script, env)
        assert result.returncode == 0, result.stderr
        arm64, amd64 = result.stdout.split("\n")[:2]
        assert arm64 == PINNED_ARM64_SHA256
        assert amd64 == PINNED_AMD64_SHA256

    def test_an_unpinned_release_has_no_recorded_digest(self, tmp_path: Path) -> None:
        """An unknown release must not be installed on trust."""
        env = _sandbox(tmp_path)
        script = f'source "{SCRIPT}"; gh_expected_sha256 9.9.9 arm64 && echo found || echo missing'
        result = _run(script, env)
        assert result.returncode == 0, result.stderr
        assert "missing" in result.stdout

    def test_the_upstream_checksum_file_is_fetched_as_corroboration(self, tmp_path: Path) -> None:
        env = _sandbox(tmp_path)
        _run(_install_script(env), env)
        checksums = (
            f"https://github.com/cli/cli/releases/download/v{VERSION}/gh_{VERSION}_checksums.txt"
        )
        assert checksums in _calls(env)


class TestUnpinnedRelease:
    """A pinned release without a recorded digest has to fail closed.

    The upstream checksum file arrives from the same origin as the
    tarball, so it catches a broken download but not a compromised
    release. A release that was pinned in advance therefore has to have
    its digest recorded in the script; only a "latest" request — which
    names no release in advance — may fall back to the upstream file.
    """

    def test_a_pinned_release_without_a_recorded_digest_fails_closed(self, tmp_path: Path) -> None:
        env = _sandbox(tmp_path)
        _serve_release(env, tmp_path, "9.9.9")
        env["GH_VERSION"] = "v9.9.9"

        result = _run(_install_script(env, pin=False), env)

        assert result.returncode != 0, "a release without a recorded digest must not install"
        assert "no pinned sha256 for the GitHub CLI 9.9.9 arm64 asset" in result.stdout
        assert "add the digest to gh_expected_sha256 for 9.9.9 arm64" in result.stdout
        assert not Path(env["STUB_TAR_LOG"]).exists(), "the archive must not be unpacked"
        assert not Path(env["STUB_INSTALL_LOG"]).exists(), "nothing must be installed"
        assert not Path(env["GH_INSTALL_PATH"]).exists(), "nothing must be installed"

    def test_the_upstream_checksum_file_does_not_replace_the_recorded_digest(
        self, tmp_path: Path
    ) -> None:
        """The upstream file corroborates a recorded digest, it never replaces one."""
        env = _sandbox(tmp_path)
        # The upstream file describes the tarball perfectly. It still
        # must not be the anchor for a pinned release.
        _serve_release(env, tmp_path, "9.9.9")
        env["GH_VERSION"] = "v9.9.9"

        result = _run(_install_script(env, pin=False), env)

        assert result.returncode != 0
        assert "not pinned; the digest comes from the upstream" not in result.stdout

    def test_latest_may_fall_back_to_the_upstream_checksum_file(self, tmp_path: Path) -> None:
        """A release that is named in advance has no recorded digest yet."""
        env = _sandbox(tmp_path)
        _serve_release(env, tmp_path, "9.9.9")
        api = tmp_path / "release-9.json"
        api.write_text(json.dumps({"tag_name": "v9.9.9", "assets": []}))
        env["STUB_API_JSON"] = str(api)
        env["GH_VERSION"] = "latest"

        result = _run(_install_script(env, pin=False), env)

        assert result.returncode == 0, result.stderr
        assert "not pinned; the digest comes from the upstream" in result.stdout
        assert Path(env["GH_INSTALL_PATH"]).exists(), "the release has to be installed"
        installed = subprocess.run(
            [env["GH_INSTALL_PATH"], "--version"], capture_output=True, text=True, check=True
        ).stdout
        assert "gh version 9.9.9" in installed

    def test_a_pinned_release_with_a_recorded_digest_is_installed(self, tmp_path: Path) -> None:
        """The fail-closed rule must not stop the release that is recorded."""
        env = _sandbox(tmp_path)
        env["GH_VERSION"] = PINNED_VERSION

        result = _run(_install_script(env), env)

        assert result.returncode == 0, result.stderr
        assert Path(env["GH_INSTALL_PATH"]).exists()


class TestInstall:
    def test_install_places_an_executable_gh_at_the_install_path(self, tmp_path: Path) -> None:
        env = _sandbox(tmp_path)
        install_path = Path(env["GH_INSTALL_PATH"])

        result = _run(_install_script(env), env)

        assert result.returncode == 0, result.stderr
        assert install_path.exists(), "gh was not installed"
        assert stat.S_IMODE(install_path.stat().st_mode) == 0o755
        assert os.access(install_path, os.X_OK)
        assert (
            f"gh version {VERSION}"
            in subprocess.run(
                [str(install_path), "--version"], capture_output=True, text=True, check=True
            ).stdout
        )

    def test_install_downloads_the_asset_for_the_host_architecture(self, tmp_path: Path) -> None:
        env = _sandbox(tmp_path)

        _run(_install_script(env), env)

        assert f"https://github.com/cli/cli/releases/download/v{VERSION}/{ASSET}" in _calls(env)

    def test_install_is_idempotent(self, tmp_path: Path) -> None:
        """A second run must not fail and must not download anything again."""
        env = _sandbox(tmp_path)
        install_path = Path(env["GH_INSTALL_PATH"])

        first = _run(_install_script(env), env)
        assert first.returncode == 0, first.stderr

        second = _run(_install_script(env), env)

        assert second.returncode == 0, second.stderr
        assert "already present" in second.stdout
        assert _downloads(env) == [
            f"https://github.com/cli/cli/releases/download/v{VERSION}/{ASSET}"
        ], "a second run must not download again"
        assert install_path.exists()

    def test_a_tampered_asset_is_not_installed(self, tmp_path: Path) -> None:
        """A digest that does not match has to stop the install.

        The check runs before the archive is unpacked and before anything
        is written to the install path: a tampered tarball must never
        reach /usr/local/bin, and the binary that is already there must
        be left alone.
        """
        env = _sandbox(tmp_path)
        install_path = Path(env["GH_INSTALL_PATH"])
        _fake_gh(install_path, "2.100.0")
        env["STUB_PINNED_SHA256"] = "0" * 64

        result = _run(_install_script(env), env)

        assert result.returncode != 0, "a tampered asset must fail the install"
        assert "sha256 mismatch" in result.stdout
        assert "refusing to install an unverified GitHub CLI" in result.stdout
        assert not Path(env["STUB_TAR_LOG"]).exists(), "the archive must not be unpacked"
        assert not Path(env["STUB_INSTALL_LOG"]).exists(), "nothing must be installed"
        installed = subprocess.run(
            [str(install_path), "--version"], capture_output=True, text=True, check=True
        ).stdout
        assert "gh version 2.100.0" in installed, "the old gh must be untouched"

    def test_a_tampered_asset_leaves_the_target_path_untouched(self, tmp_path: Path) -> None:
        """The install path must not even be created for a bad download."""
        env = _sandbox(tmp_path)
        install_path = Path(env["GH_INSTALL_PATH"])
        env["STUB_PINNED_SHA256"] = "0" * 64

        result = _run(_install_script(env), env)

        assert result.returncode != 0
        assert not install_path.exists(), "the install path must be left untouched"
        assert not install_path.parent.exists(), "the install directory must not be created"

    def test_an_upstream_checksum_that_disagrees_is_fatal(self, tmp_path: Path) -> None:
        """The checksum file corroborates the recorded digest — it cannot
        replace it, and a file that disagrees is a red flag."""
        env = _sandbox(tmp_path)
        install_path = Path(env["GH_INSTALL_PATH"])
        Path(env["STUB_CHECKSUMS"]).write_text(f"{'1' * 64}  {ASSET}\n")

        result = _run(_install_script(env), env)

        assert result.returncode != 0
        assert "upstream checksum file disagrees" in result.stdout
        assert not install_path.exists()

    def test_the_install_does_not_swallow_the_callers_cleanup(self, tmp_path: Path) -> None:
        """Only the trap the install installed may be removed again.

        `trap - EXIT` clears every EXIT trap, so a caller that registered
        a cleanup of its own would lose it.
        """
        env = _sandbox(tmp_path)
        script = (
            'trap "echo caller-cleanup" EXIT; '
            f'source "{SCRIPT}"; '
            'gh_expected_sha256() { printf "%s" "$STUB_PINNED_SHA256"; }; '
            "uname_m=aarch64; ensure_gh_linux"
        )

        result = _run(script, env)

        assert result.returncode == 0, result.stderr
        assert "caller-cleanup" in result.stdout, "the caller's EXIT trap was swallowed"

    def test_a_failed_install_keeps_the_callers_cleanup(self, tmp_path: Path) -> None:
        """The caller's cleanup has to run when the install fails too.

        The trap the install installs replaces the caller's own, and the
        failure path leaves the script through `exit` — so the caller's
        trap has to be put back before that exit, not from inside the
        EXIT handler (a trap registered there is never run).
        """
        env = _sandbox(tmp_path)
        env["STUB_PINNED_SHA256"] = "0" * 64
        script = (
            'trap "echo caller-cleanup" EXIT; '
            f'source "{SCRIPT}"; '
            'gh_expected_sha256() { printf "%s" "$STUB_PINNED_SHA256"; }; '
            "uname_m=aarch64; ensure_gh_linux"
        )

        result = _run(script, env)

        assert result.returncode != 0, "the tampered asset has to fail the install"
        assert "caller-cleanup" in result.stdout, "the caller's EXIT trap was swallowed"


class TestInstalledVersion:
    """The installed binary is compared against the requested release."""

    def test_a_different_installed_version_is_replaced(self, tmp_path: Path) -> None:
        env = _sandbox(tmp_path)
        install_path = Path(env["GH_INSTALL_PATH"])
        _fake_gh(install_path, "2.100.0")

        result = _run(_install_script(env), env)

        assert result.returncode == 0, result.stderr
        assert f"gh_{VERSION}_linux_{ARCH}.tar.gz" in result.stdout
        installed = subprocess.run(
            [str(install_path), "--version"], capture_output=True, text=True, check=True
        ).stdout
        assert f"gh version {VERSION}" in installed

    def test_the_installed_version_is_kept_when_it_matches(self, tmp_path: Path) -> None:
        env = _sandbox(tmp_path)
        install_path = Path(env["GH_INSTALL_PATH"])
        _fake_gh(install_path, VERSION)

        result = _run(_install_script(env), env)

        assert result.returncode == 0, result.stderr
        assert "already present" in result.stdout
        assert _downloads(env) == [], "an up-to-date gh must not be reinstalled"

    def test_an_unreachable_release_api_keeps_the_installed_gh(self, tmp_path: Path) -> None:
        env = _sandbox(tmp_path)
        env["GH_VERSION"] = "latest"
        env["STUB_API_FAILS"] = "1"
        install_path = Path(env["GH_INSTALL_PATH"])
        _fake_gh(install_path, VERSION)

        result = _run(_install_script(env), env)

        assert result.returncode == 0, result.stderr
        assert "keeping gh" in result.stdout
        assert _downloads(env) == [], "a kept gh must not be reinstalled"

    def test_an_unreachable_release_api_fails_without_an_installed_gh(self, tmp_path: Path) -> None:
        env = _sandbox(tmp_path)
        env["GH_VERSION"] = "latest"
        env["STUB_API_FAILS"] = "1"

        result = _run(_install_script(env), env)

        assert result.returncode != 0, "the install must fail loudly"
        assert "could not read the latest GitHub CLI release" in result.stdout


class TestRunnerUser:
    """The runner user is the OWNER of the runner home, not the home's name.

    The runner service runs as the owner of its home, so that owner is
    the user a job runs as. Deriving the user from the owner — and not
    from the name of the home — is what makes the check meaningful: root
    bypasses the mode bits, so a check that ran as root would pass for a
    binary the runner user cannot execute.
    """

    def test_the_runner_user_is_the_owner_of_the_runner_home(self, tmp_path: Path) -> None:
        env = _sandbox(tmp_path)
        _root_host_with_runner_home(tmp_path, env, "redlexgilgamesh.guest")
        script = f'source "{SCRIPT}"; runner_user'
        result = _run(script, env)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "redlexgilgamesh.guest"

    def test_without_a_runner_home_the_invoking_user_is_used(self, tmp_path: Path) -> None:
        env = _sandbox(tmp_path)
        _root_host_with_runner_home(tmp_path, env, "redlexgilgamesh.guest")
        # A home with no runner dir in it: the script finds no runner
        # home and falls back to the user it runs as.
        empty_home = tmp_path / "empty-home"
        empty_home.mkdir()
        env["HOME"] = str(empty_home)
        script = f'source "{SCRIPT}"; runner_user'
        result = _run(script, env)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() != "redlexgilgamesh.guest"
        assert result.stdout.strip() != "root"

    def test_the_check_runs_as_the_runner_user(self, tmp_path: Path) -> None:
        """The check has to run AS the runner user, not as root.

        Root bypasses the mode bits, so a check that ran as root would
        pass for a binary the runner user cannot execute. The escalation
        is recorded, and the gh on the job path reports the user it was
        started as — so a regression that stops using sudo is caught
        here, not only in a review.
        """
        env = _sandbox(tmp_path)
        _root_host_with_runner_home(tmp_path, env, "redlexgilgamesh.guest")
        _sudo_stub(env)
        identity = tmp_path / "gh-identity"
        env["STUB_GH_IDENTITY_FILE"] = str(identity)
        real_id = shutil.which("id", path=os.environ.get("PATH", ""))
        assert real_id is not None, "the sandbox needs id and this host does not have it"
        bindir = tmp_path / "job-path"
        bindir.mkdir()
        gh = bindir / "gh"
        gh.write_text(
            "#!/bin/sh\n"
            'who="${STUB_SUDO_USER:-'
            f'"{real_id}" -un' + '}"\n'
            'printf "%s\\n" "$who" > "$STUB_GH_IDENTITY_FILE"\n'
            "echo 'gh version 2.103.3'\n"
        )
        gh.chmod(0o755)
        script = (
            f'source "{SCRIPT}"; uname_s=Linux; DEFAULT_JOB_PATH="{bindir}"; verify_gh_runnable'
        )

        result = _run(script, env)

        assert result.returncode == 0, result.stderr
        assert "verified: redlexgilgamesh.guest can run gh" in result.stdout
        sudo_log = Path(env["STUB_SUDO_LOG"])
        assert sudo_log.exists(), "the check did not escalate to the runner user at all"
        sudo_calls = sudo_log.read_text().splitlines()
        assert f"-n -u redlexgilgamesh.guest -- env PATH={bindir} gh --version" in sudo_calls, (
            f"the check has to escalate to the runner user, recorded: {sudo_calls}"
        )
        assert identity.exists(), "gh did not run"
        assert identity.read_text().strip() == "redlexgilgamesh.guest", (
            "gh ran as the invoking user, not as the runner user"
        )

    def test_the_check_names_the_runner_user_when_it_fails(self, tmp_path: Path) -> None:
        env = _sandbox(tmp_path)
        _root_host_with_runner_home(tmp_path, env, "redlexgilgamesh.guest")
        empty = tmp_path / "empty-path"
        empty.mkdir()
        script = f'source "{SCRIPT}"; uname_s=Linux; DEFAULT_JOB_PATH="{empty}"; verify_gh_runnable'

        result = _run(script, env)

        assert result.returncode != 0, "the check must fail the install"
        assert "redlexgilgamesh.guest cannot run gh" in result.stdout
        assert "gh: command not found" in result.stdout


class TestRunnerUserCheck:
    """The install has to end with a check the runner user can run gh."""

    def test_check_passes_when_gh_is_on_the_job_path(self, tmp_path: Path) -> None:
        env = _sandbox(tmp_path)
        bindir = tmp_path / "job-path"
        bindir.mkdir()
        gh = bindir / "gh"
        gh.write_text("#!/bin/sh\necho 'gh version 2.103.3'\n")
        gh.chmod(0o755)
        script = (
            f'source "{SCRIPT}"; uname_s=Linux; DEFAULT_JOB_PATH="{bindir}"; verify_gh_runnable'
        )

        result = _run(script, env)

        assert result.returncode == 0, result.stderr
        assert "can run gh" in result.stdout

    def test_check_fails_loudly_when_gh_is_not_runnable(self, tmp_path: Path) -> None:
        env = _sandbox(tmp_path)
        empty = tmp_path / "empty-path"
        empty.mkdir()
        script = f'source "{SCRIPT}"; uname_s=Linux; DEFAULT_JOB_PATH="{empty}"; verify_gh_runnable'

        result = _run(script, env)

        assert result.returncode != 0, "the check must fail the install"
        assert "cannot run gh" in result.stdout
        assert "gh: command not found" in result.stdout


class TestSudoExpansion:
    """The escalation command must not leak its flags into the command.

    `gh-runners provision` runs as root, where the escalation command is
    empty. The remote install used to be spelled `$SUDO -E bash -`, which
    became `-E bash -` and failed with "-E: command not found".
    """

    def test_remote_install_runs_as_root_without_escalating(self, tmp_path: Path) -> None:
        env = _node_sandbox(tmp_path, as_root=True, with_sudo=False)
        script = f'source "{SCRIPT}"; ensure_node_linux'

        result = _run(script, env)

        assert result.returncode == 0, result.stderr
        assert "remote-install-script-ran" in result.stdout
        assert "command not found" not in result.stderr
        assert not Path(env["STUB_SUDO_LOG"]).exists(), "root must not escalate"

    def test_remote_install_escalates_with_sudo_when_set(self, tmp_path: Path) -> None:
        env = _node_sandbox(tmp_path, as_root=False, with_sudo=True)
        env["SUDO"] = "sudo"
        script = f'source "{SCRIPT}"; ensure_node_linux'

        result = _run(script, env)

        assert result.returncode == 0, result.stderr
        assert "remote-install-script-ran" in result.stdout
        sudo_calls = Path(env["STUB_SUDO_LOG"]).read_text().splitlines()
        assert "-n -E bash -" in sudo_calls, (
            f"the escalation must not prompt, recorded: {sudo_calls}"
        )

    def test_the_escalation_is_never_interactive(self, tmp_path: Path) -> None:
        """A non-interactive provision must not hang on a password prompt."""
        env = _node_sandbox(tmp_path, as_root=False, with_sudo=True)
        env["SUDO"] = "sudo"
        script = f'source "{SCRIPT}"; run_privileged id -u'

        result = _run(script, env)

        assert result.returncode == 0, result.stderr
        sudo_calls = Path(env["STUB_SUDO_LOG"]).read_text().splitlines()
        assert "-n id -u" in sudo_calls

    def test_the_remote_install_never_escalates_interactively(self, tmp_path: Path) -> None:
        """The remote install escalates too, and it must not prompt."""
        env = _node_sandbox(tmp_path, as_root=False, with_sudo=True)
        env["SUDO"] = "sudo"
        script = f'source "{SCRIPT}"; echo true | run_remote_script'

        result = _run(script, env)

        assert result.returncode == 0, result.stderr
        sudo_calls = Path(env["STUB_SUDO_LOG"]).read_text().splitlines()
        assert "-n -E bash -" in sudo_calls, (
            f"the escalation must not prompt, recorded: {sudo_calls}"
        )

    def test_no_unquoted_sudo_expansion_is_left_in_the_script(self) -> None:
        """A bare `$SUDO` in a command position is the bug this guards."""
        source = SCRIPT.read_text()
        code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
        assert re.search(r'(?<!["{])\$SUDO', code) is None


class TestSandboxHygiene:
    """The sandbox must not depend on what the host has installed.

    The self-hosted runner has /usr/bin/node; a laptop usually does not.
    When the sandbox inherits the host's PATH, `command -v node` succeeds
    there, the provisioning takes its "node already present" branch, and
    the test stops testing the install without saying so.
    """

    def test_node_is_not_reachable_in_the_node_sandbox(self, tmp_path: Path) -> None:
        env = _node_sandbox(tmp_path, as_root=True, with_sudo=False)
        result = _run("command -v node || echo no-node", env)
        assert result.stdout.strip() == "no-node"

    def test_npm_resolves_to_the_stub_in_the_node_sandbox(self, tmp_path: Path) -> None:
        env = _node_sandbox(tmp_path, as_root=True, with_sudo=False)
        result = _run("command -v npm", env)
        assert result.stdout.strip() == str(Path(env["STUB_DIR"]) / "npm")

    def test_the_gh_sandbox_cannot_reach_the_host_toolchain(self, tmp_path: Path) -> None:
        env = _sandbox(tmp_path)
        result = _run(
            "for c in node npm gh uv python3; do "
            'printf "%s=%s\\n" "$c" "$(command -v "$c" || echo none)"; done',
            env,
        )
        assert result.stdout.split() == [
            "node=none",
            "npm=none",
            "gh=none",
            "uv=none",
            "python3=none",
        ]

    def test_the_sandbox_has_a_standalone_gzip(self, tmp_path: Path) -> None:
        """GNU tar needs a gzip binary; the bsdtar on macOS does not.

        Without it `tar -xzf` dies with "tar (child): gzip: Cannot exec",
        and that only shows up on the Linux runner — a laptop unpacks the
        release tarball with the gzip built into its tar.
        """
        env = _sandbox(tmp_path)
        found = shutil.which("gzip", path=env["PATH"])
        assert found is not None, "the sandbox needs a standalone gzip for `tar -z`"

    def test_the_sandbox_has_a_sha256_tool(self, tmp_path: Path) -> None:
        """The download is verified with a real sha256 implementation."""
        env = _sandbox(tmp_path)
        found = [name for name in SHA256_BINARIES if shutil.which(name, path=env["PATH"])]
        assert found, "the sandbox needs sha256sum or shasum to verify the download"

    def test_the_sandbox_can_unpack_a_gzipped_tarball(self, tmp_path: Path) -> None:
        """The install unpacks the release tarball; the sandbox must allow it."""
        env = _sandbox(tmp_path)
        tarball = Path(env["STUB_TARBALL"])
        outdir = tmp_path / "unpacked"
        outdir.mkdir()
        result = _run(f'tar -xzf "{tarball}" -C "{outdir}"', env)
        assert result.returncode == 0, result.stderr
        assert (outdir / f"gh_{VERSION}_linux_{ARCH}" / "bin" / "gh").is_file()
