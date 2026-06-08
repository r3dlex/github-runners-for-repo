"""Native actions/runner binary lifecycle management.

The official ``actions/runner`` package is downloaded, configured, and run
on the host directly. Each runner lives in its own directory; the CLI is
invoked once per runner directory to manage that single instance. To run
N runners, create N directories and run the CLI in each.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from .config import RunnerConfig
from .github_api import get_registration_token, list_runners, remove_runner

DEFAULT_RUNNER_VERSION = "2.334.0"
GITHUB_RUNNER_RELEASES = "https://github.com/actions/runner/releases/download"

PROVISION_SCRIPT = Path(__file__).parent / "scripts" / "provision-host.sh"

PLATFORM_PACKAGES: dict[tuple[str, str], str] = {
    ("Darwin", "arm64"): "actions-runner-osx-arm64-{ver}.tar.gz",
    ("Darwin", "x86_64"): "actions-runner-osx-x64-{ver}.tar.gz",
    ("Linux", "x86_64"): "actions-runner-linux-x64-{ver}.tar.gz",
    ("Linux", "arm64"): "actions-runner-linux-arm64-{ver}.tar.gz",
    ("Linux", "aarch64"): "actions-runner-linux-arm64-{ver}.tar.gz",
    ("Windows", "x86_64"): "actions-runner-win-x64-{ver}.zip",
    ("Windows", "AMD64"): "actions-runner-win-x64-{ver}.zip",
}


class RunnerError(Exception):
    """Raised when runner management fails."""


def detect_platform() -> tuple[str, str]:
    """Return the (os, arch) tuple for the current host.

    ``arch`` is normalized to lowercase so ``aarch64`` and ``arm64`` are
    treated equivalently.
    """
    return platform.system(), platform.machine().lower()


def package_name(os_name: str, arch: str, version: str) -> str:
    """Return the official release tarball filename for (os, arch)."""
    key = (os_name, arch)
    template = PLATFORM_PACKAGES.get(key)
    if template is None:
        raise RunnerError(
            f"Unsupported platform: {os_name}/{arch}. Known: {sorted(PLATFORM_PACKAGES.keys())}"
        )
    return template.format(ver=version)


def download_url(version: str) -> str:
    """Return the official release URL for the current host."""
    os_name, arch = detect_platform()
    pkg = package_name(os_name, arch, version)
    return f"{GITHUB_RUNNER_RELEASES}/v{version}/{pkg}"


def _validate_target_dir(target_dir: Path) -> Path:
    target_dir = target_dir.expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir


def install(
    config: RunnerConfig,
    target_dir: Path,
    version: str = DEFAULT_RUNNER_VERSION,
) -> Path:
    """Download and extract the official actions/runner binary.

    ``target_dir`` is created if missing. Returns the resolved target path.
    """
    target_dir = _validate_target_dir(target_dir)
    os_name, arch = detect_platform()
    pkg = package_name(os_name, arch, version)
    url = f"{GITHUB_RUNNER_RELEASES}/v{version}/{pkg}"

    print(f"Downloading {url}...")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / pkg
            urllib.request.urlretrieve(url, archive)  # noqa: S310 (verified URL)
            print(f"Extracting to {target_dir}...")
            with tarfile.open(archive, "r:gz") as tf:
                tf.extractall(target_dir)
    except urllib.error.URLError as e:
        raise RunnerError(f"Failed to download runner {version}: {e}") from e
    except tarfile.TarError as e:
        raise RunnerError(f"Failed to extract runner archive: {e}") from e

    print(f"Runner {version} installed at {target_dir}")
    return target_dir


def provision(config: RunnerConfig) -> None:
    """Provision the runner host with the toolchain jobs expect.

    A bare self-hosted host lacks the toolchain a GitHub-hosted runner ships
    with (Node/npm, pipx). Without it, jobs that call ``npm`` or ``node``
    fail with "command not found" and silently fall back to the
    GitHub-hosted leg. This runs the bundled idempotent provisioning script
    so re-running is safe.
    """
    del config  # provisioning is host-scoped, not runner-scoped
    if not PROVISION_SCRIPT.exists():
        raise RunnerError(f"Provisioning script not found at {PROVISION_SCRIPT}")
    print(f"Provisioning host via {PROVISION_SCRIPT}...")
    subprocess.run(["bash", str(PROVISION_SCRIPT)], check=True)  # noqa: S603, S607
    print("Host provisioning complete.")


def _require_config_sh(target_dir: Path) -> Path:
    config_sh = target_dir / "config.sh"
    if not config_sh.exists():
        raise RunnerError(
            f"Runner not installed in {target_dir} (config.sh missing). "
            f"Run `gh-runners install --target-dir {target_dir}` first."
        )
    return config_sh


def configure(
    config: RunnerConfig,
    target_dir: Path,
    name: str,
    labels: str | None = None,
    runner_group: str | None = None,
    work_dir: str | None = None,
    replace: bool = True,
) -> None:
    """Configure the runner in ``target_dir`` against the org/repo scope.

    Fetches a JIT registration token from the GitHub API, then invokes
    ``config.sh`` with the resolved ``--url``, ``--labels``, and
    ``--runnergroup`` values. With ``replace=True`` (default), an existing
    runner with the same name is replaced in place.
    """
    target_dir = target_dir.expanduser().resolve()
    config_sh = _require_config_sh(target_dir)

    effective_labels = labels if labels is not None else config.runner_labels
    effective_group = runner_group if runner_group is not None else config.runner_group

    print(f"Requesting registration token for {config.runner_url}...")
    token = get_registration_token(config)

    cmd: list[str] = [
        str(config_sh),
        "--url",
        config.runner_url,
        "--token",
        token,
        "--name",
        name,
        "--labels",
        effective_labels,
        "--runnergroup",
        effective_group,
        "--unattended",
    ]
    if replace:
        cmd.append("--replace")
    if work_dir:
        cmd.extend(["--work", work_dir])

    print(f"Configuring runner '{name}' with labels=[{effective_labels}] in {effective_group}...")
    subprocess.run(cmd, cwd=target_dir, check=True)  # noqa: S603
    print(f"Runner '{name}' configured.")


def _svc(target_dir: Path) -> Path | None:
    svc = target_dir / "svc.sh"
    return svc if svc.exists() else None


def start(
    config: RunnerConfig,
    target_dir: Path,
    as_service: bool = False,
) -> None:
    """Start the runner in ``target_dir``.

    With ``as_service=True`` (recommended for persistent hosts), installs
    and starts a launchd (macOS) or systemd (Linux) service. Otherwise
    replaces the current process with ``run.sh`` in the foreground.
    """
    del config  # scope is captured at configure time
    target_dir = target_dir.expanduser().resolve()
    config_sh = _require_config_sh(target_dir)
    _ = config_sh

    if as_service:
        svc = _svc(target_dir)
        if svc is None:
            raise RunnerError(f"svc.sh not found in {target_dir}; cannot install as service.")
        print(f"Installing runner service from {target_dir}...")
        subprocess.run([str(svc), "install"], cwd=target_dir, check=True)  # noqa: S603
        subprocess.run([str(svc), "start"], cwd=target_dir, check=True)  # noqa: S603
        print("Runner service installed and started.")
        return

    run_sh = target_dir / "run.sh"
    if not run_sh.exists():
        raise RunnerError(f"run.sh not found in {target_dir}")
    print(f"Starting runner in foreground ({target_dir})...")
    os.execv(str(run_sh), [str(run_sh)])


def stop(
    config: RunnerConfig,
    target_dir: Path,
) -> None:
    """Stop the runner in ``target_dir``.

    For service-mode runners, runs ``svc.sh stop`` and ``svc.sh uninstall``.
    For foreground runners, prints a hint (Ctrl-C is the only way to stop).
    """
    del config
    target_dir = target_dir.expanduser().resolve()
    svc = _svc(target_dir)
    if svc is None:
        print(
            "No svc.sh found in target_dir. If the runner is in the foreground, "
            "use Ctrl-C. Otherwise, the runner is not installed or was removed."
        )
        return
    subprocess.run([str(svc), "stop"], cwd=target_dir, check=False)  # noqa: S603
    subprocess.run([str(svc), "uninstall"], cwd=target_dir, check=False)  # noqa: S603
    print("Runner service stopped and uninstalled.")


def status(config: RunnerConfig) -> None:
    """List registered runners at the configured scope."""
    print(f"=== Runners for {config.runner_url} (scope={config.runner_scope}) ===")
    try:
        runners = list_runners(config)
    except Exception as e:  # noqa: BLE001
        print(f"Could not fetch runners: {e}", file=sys.stderr)
        return
    if not runners:
        print("No runners registered.")
        return
    for r in runners:
        labels = ", ".join(label["name"] for label in r.get("labels", []))
        busy = r.get("busy", False)
        os_name = r.get("os", "?")
        print(
            f"  {r['name']} (id={r['id']}) status={r['status']} "
            f"busy={busy} os={os_name} labels=[{labels}]"
        )


def remove(config: RunnerConfig, name: str) -> None:
    """Deregister a runner by name from the configured scope.

    The runner process should be stopped first (via ``stop``) so the server
    allows the deregistration.
    """
    runners = list_runners(config)
    target = next((r for r in runners if r.get("name") == name), None)
    if target is None:
        print(f"Runner '{name}' not found in {config.runner_url}.")
        return
    remove_runner(config, int(target["id"]))
    print(f"Runner '{name}' (id={target['id']}) removed from {config.runner_url}.")
