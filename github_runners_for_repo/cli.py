"""CLI entry point for managing GitHub Actions self-hosted runners."""

from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_config
from .runner_manager import (
    DEFAULT_RUNNER_VERSION,
    configure,
    install,
    provision,
    remove,
    start,
    status,
    stop,
)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="gh-runners",
        description=(
            "Manage GitHub Actions self-hosted runners via the official "
            "actions/runner binary on the host. Each runner lives in its "
            "own directory; run the CLI once per directory."
        ),
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="Path to .env file (defaults to .env in current directory)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "provision",
        help="Provision the host with the toolchain jobs expect (Node/npm, pipx)",
    )

    install_p = subparsers.add_parser(
        "install",
        help="Download and extract the actions/runner binary to a target directory",
    )
    install_p.add_argument(
        "--target-dir",
        type=Path,
        default=Path("./actions-runner"),
        help="Directory to install the runner into (default: ./actions-runner)",
    )
    install_p.add_argument(
        "--version",
        default=DEFAULT_RUNNER_VERSION,
        help=f"Runner version to install (default: {DEFAULT_RUNNER_VERSION})",
    )

    configure_p = subparsers.add_parser(
        "configure",
        help="Configure the runner against the org/repo (fetches a JIT token, runs config.sh)",
    )
    configure_p.add_argument(
        "--target-dir",
        type=Path,
        default=Path("./actions-runner"),
        help="Directory containing the installed runner (default: ./actions-runner)",
    )
    configure_p.add_argument(
        "--name",
        required=True,
        help="Runner name as it will appear in the GitHub UI",
    )
    configure_p.add_argument(
        "--labels",
        default=None,
        help="Comma-separated labels (defaults to RUNNER_LABELS env)",
    )
    configure_p.add_argument(
        "--runner-group",
        default=None,
        help="Runner group (defaults to RUNNER_GROUP env)",
    )
    configure_p.add_argument(
        "--work-dir",
        default=None,
        help="Runner _work directory (default: <target_dir>/_work)",
    )
    configure_p.add_argument(
        "--no-replace",
        action="store_true",
        help="Do not pass --replace to config.sh (fail if name is taken)",
    )

    start_p = subparsers.add_parser(
        "start",
        help="Start the runner (foreground by default, or as a service with --service)",
    )
    start_p.add_argument(
        "--target-dir",
        type=Path,
        default=Path("./actions-runner"),
        help="Directory containing the configured runner (default: ./actions-runner)",
    )
    start_p.add_argument(
        "--service",
        action="store_true",
        help="Install and start as a launchd (macOS) or systemd (Linux) service",
    )

    stop_p = subparsers.add_parser(
        "stop",
        help="Stop the runner (only meaningful for service-mode runners)",
    )
    stop_p.add_argument(
        "--target-dir",
        type=Path,
        default=Path("./actions-runner"),
        help="Directory containing the configured runner (default: ./actions-runner)",
    )

    subparsers.add_parser("status", help="List runners registered at the configured scope")

    remove_p = subparsers.add_parser(
        "remove",
        help="Deregister a runner by name from the configured scope",
    )
    remove_p.add_argument("--name", required=True, help="Runner name to deregister")

    args = parser.parse_args(argv)
    config = load_config(args.env_file)

    if args.command == "provision":
        provision(config)
    elif args.command == "install":
        install(config, args.target_dir, version=args.version)
    elif args.command == "configure":
        configure(
            config,
            args.target_dir,
            name=args.name,
            labels=args.labels,
            runner_group=args.runner_group,
            work_dir=args.work_dir,
            replace=not args.no_replace,
        )
    elif args.command == "start":
        start(config, args.target_dir, as_service=args.service)
    elif args.command == "stop":
        stop(config, args.target_dir)
    elif args.command == "status":
        status(config)
    elif args.command == "remove":
        remove(config, args.name)


if __name__ == "__main__":
    main()
