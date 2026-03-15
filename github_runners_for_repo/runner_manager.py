"""Docker-based runner lifecycle management."""

from __future__ import annotations

import subprocess
import sys

from .config import RunnerConfig
from .github_api import get_registration_token, list_runners


def build_image(config: RunnerConfig) -> None:
    """Build the runner Docker image."""
    print(f"Building runner image: {config.runner_image}")
    subprocess.run(
        ["docker", "compose", "build"],
        check=True,
    )


def start_runners(config: RunnerConfig) -> None:
    """Start runner containers using docker compose."""
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
    print(f"Starting {count} runner(s) for {config.github_repository}...")

    subprocess.run(
        ["docker", "compose", "up", "--scale", f"runner={count}", "-d", "--build"],
        check=True,
    )
    print(f"{count} runner(s) started.")


def stop_runners(config: RunnerConfig) -> None:
    """Stop and remove all runner containers."""
    print("Stopping runners...")
    subprocess.run(
        ["docker", "compose", "down"],
        check=True,
    )
    print("Runners stopped.")


def status(config: RunnerConfig) -> None:
    """Show status of runner containers and registered runners."""
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
