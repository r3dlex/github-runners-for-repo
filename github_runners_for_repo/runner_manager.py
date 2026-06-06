"""Docker-based runner lifecycle management."""

from __future__ import annotations

import subprocess
import sys

from .config import RunnerConfig
from .github_api import get_registration_token, list_runners

_docker_compose_checked = False


def _require_docker_compose() -> None:
    """Exit with an install hint if the docker compose v2 plugin is missing.

    Cached per CLI invocation so the subprocess runs at most once.
    """
    global _docker_compose_checked
    if _docker_compose_checked:
        return
    try:
        result = subprocess.run(
            ["docker", "compose", "version"],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        print(
            "ERROR: 'docker compose' is not available on this host.\n"
            "  The 'docker-compose' v1 binary is not the v2 plugin and is not used here.\n"
            "  Install the v2 plugin, e.g.:\n"
            "    brew install docker-compose\n"
            "  Or enable the plugin in your Docker / Colima configuration.",
            file=sys.stderr,
        )
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").lower()
        if "not a docker command" in stderr or "unknown command" in stderr:
            print(
                "ERROR: 'docker compose' (v2 plugin) is not installed.\n"
                "  Hint: install the v2 plugin, e.g. 'brew install docker-compose',\n"
                "  or enable compose in your Docker / Colima configuration.",
                file=sys.stderr,
            )
            sys.exit(1)
        print(
            f"ERROR: 'docker compose version' failed: {e.stderr or e}",
            file=sys.stderr,
        )
        sys.exit(1)
    _docker_compose_checked = True
    del result


def build_image(config: RunnerConfig) -> None:
    """Build the runner Docker image."""
    _require_docker_compose()
    print(f"Building runner image: {config.runner_image}")
    subprocess.run(
        ["docker", "compose", "build"],
        check=True,
    )


def start_runners(config: RunnerConfig) -> None:
    """Start runner containers using docker compose."""
    _require_docker_compose()
    errors = config.validate()
    if errors:
        print("Configuration errors:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)

    # Verify we can get a registration token before starting containers
    get_registration_token(config)
    print("Registration token obtained successfully.")

    count = config.runner_count
    target = config.github_org or config.github_repository
    print(f"Starting {count} runner(s) for {target} (scope={config.runner_scope})...")

    subprocess.run(
        ["docker", "compose", "up", "--scale", f"runner={count}", "-d", "--build"],
        check=True,
    )
    print(f"{count} runner(s) started.")


def stop_runners(config: RunnerConfig) -> None:
    """Stop and remove all runner containers."""
    _require_docker_compose()
    print("Stopping runners...")
    subprocess.run(
        ["docker", "compose", "down"],
        check=True,
    )
    print("Runners stopped.")


def status(config: RunnerConfig) -> None:
    """Show status of runner containers and registered runners."""
    _require_docker_compose()
    print("=== Docker Containers ===")
    subprocess.run(["docker", "compose", "ps"], check=False)

    print("\n=== Registered GitHub Runners ===")
    try:
        runners = list_runners(config)
        if not runners:
            print("No runners registered.")
        for r in runners:
            labels = ", ".join(label["name"] for label in r.get("labels", []))
            print(f"  {r['name']} (id={r['id']}) status={r['status']} labels=[{labels}]")
    except Exception as e:
        print(f"  Could not fetch runners: {e}")
