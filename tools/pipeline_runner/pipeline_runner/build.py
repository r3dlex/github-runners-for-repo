"""Build stage: build package, install wheel, and verify entry point."""

from __future__ import annotations

import glob
import os

from .runner import run_cmd


def run(project_dir: str = ".") -> bool:
    """Build the package, install the wheel, and verify it works. Returns True on success."""
    if not run_cmd(["poetry", "build"], cwd=project_dir):
        return False

    dist_dir = os.path.join(project_dir, "dist")
    wheels = glob.glob(os.path.join(dist_dir, "*.whl"))
    if not wheels:
        print("No wheel found in dist/")
        return False

    wheel = wheels[-1]
    if not run_cmd(["pip", "install", wheel]):
        return False

    if not run_cmd(["gh-runners", "--help"]):
        return False

    return True
