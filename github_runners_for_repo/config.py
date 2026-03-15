"""Configuration management via environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass
class RunnerConfig:
    """Configuration for GitHub Actions runner deployment."""

    github_access_token: str
    github_repository: str
    runner_name_prefix: str = "runner"
    runner_count: int = 1
    runner_labels: str = "self-hosted,linux,x64"
    runner_group: str = "Default"
    runner_image: str = "github-runner:latest"

    @property
    def repo_owner(self) -> str:
        return self.github_repository.split("/")[0]

    @property
    def repo_name(self) -> str:
        return self.github_repository.split("/")[1]

    def validate(self) -> list[str]:
        """Return a list of validation errors, empty if valid."""
        errors: list[str] = []
        if not self.github_access_token:
            errors.append("GITHUB_ACCESS_TOKEN is required")
        if not self.github_repository:
            errors.append("GITHUB_REPOSITORY is required")
        if "/" not in self.github_repository:
            errors.append("GITHUB_REPOSITORY must be in owner/repo format")
        if self.runner_count < 1:
            errors.append("RUNNER_COUNT must be at least 1")
        return errors


def load_config(env_file: str | None = None) -> RunnerConfig:
    """Load configuration from environment variables and optional .env file."""
    if env_file:
        load_dotenv(env_file)
    else:
        load_dotenv()

    return RunnerConfig(
        github_access_token=os.getenv("GITHUB_ACCESS_TOKEN", ""),
        github_repository=os.getenv("GITHUB_REPOSITORY", ""),
        runner_name_prefix=os.getenv("RUNNER_NAME_PREFIX", "runner"),
        runner_count=int(os.getenv("RUNNER_COUNT", "1")),
        runner_labels=os.getenv("RUNNER_LABELS", "self-hosted,linux,x64"),
        runner_group=os.getenv("RUNNER_GROUP", "Default"),
        runner_image=os.getenv("RUNNER_IMAGE", "github-runner:latest"),
    )
