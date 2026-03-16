"""CLI entry point for pipeline-runner."""

from __future__ import annotations

import argparse
import sys

from . import lint, test, coverage, build


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run CI pipeline stages")
    parser.add_argument(
        "--project-dir",
        default=".",
        help="Root directory of the project (default: current directory)",
    )

    subparsers = parser.add_subparsers(dest="stage", required=True)

    subparsers.add_parser("lint", help="Check formatting and lint rules")
    subparsers.add_parser("test", help="Run test suite with coverage")
    cov_parser = subparsers.add_parser("coverage", help="Run tests and enforce coverage threshold")
    cov_parser.add_argument(
        "--threshold",
        type=int,
        default=coverage.DEFAULT_THRESHOLD,
        help=f"Minimum coverage percentage (default: {coverage.DEFAULT_THRESHOLD})",
    )
    subparsers.add_parser("build", help="Build and verify the package")
    subparsers.add_parser("all", help="Run all stages in sequence")

    args = parser.parse_args(argv)
    project_dir = args.project_dir

    stages = {
        "lint": lambda d: lint.run(d),
        "test": lambda d: test.run(d),
        "coverage": lambda d: coverage.run(
            d, threshold=args.threshold if args.stage == "coverage" else coverage.DEFAULT_THRESHOLD
        ),
        "build": lambda d: build.run(d),
    }

    if args.stage == "all":
        for name, stage_fn in stages.items():
            print(f"\n{'=' * 60}")
            print(f"  Stage: {name}")
            print(f"{'=' * 60}\n")
            if not stage_fn(project_dir):
                print(f"\nStage '{name}' failed.")
                sys.exit(1)
        print(f"\n{'=' * 60}")
        print("  All stages passed.")
        print(f"{'=' * 60}\n")
    else:
        if not stages[args.stage](project_dir):
            sys.exit(1)


if __name__ == "__main__":
    main()
