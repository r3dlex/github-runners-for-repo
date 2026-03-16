"""Subprocess helper for running pipeline commands."""

from __future__ import annotations

import subprocess
import sys


def run_cmd(args: list[str], cwd: str | None = None) -> bool:
    """Run a command, streaming output. Returns True on success."""
    print(f"$ {' '.join(args)}")
    result = subprocess.run(args, cwd=cwd)
    if result.returncode != 0:
        print(f"Command failed with exit code {result.returncode}", file=sys.stderr)
        return False
    return True
