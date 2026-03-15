"""CLI entry point for managing GitHub Actions runners."""

from __future__ import annotations

import argparse

from .config import load_config
from .runner_manager import build_image, start_runners, status, stop_runners


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Manage containerized GitHub Actions self-hosted runners",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="Path to .env file (defaults to .env in current directory)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("build", help="Build the runner Docker image")
    subparsers.add_parser("start", help="Start runner containers")
    subparsers.add_parser("stop", help="Stop runner containers")
    subparsers.add_parser("status", help="Show runner status")

    args = parser.parse_args(argv)
    config = load_config(args.env_file)

    commands = {
        "build": lambda: build_image(config),
        "start": lambda: start_runners(config),
        "stop": lambda: stop_runners(config),
        "status": lambda: status(config),
    }

    commands[args.command]()


if __name__ == "__main__":
    main()
