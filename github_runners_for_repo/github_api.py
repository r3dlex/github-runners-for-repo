"""GitHub API interactions for runner management."""

from __future__ import annotations

import requests

from .config import RunnerConfig

GITHUB_API_BASE = "https://api.github.com"


class GitHubAPIError(Exception):
    """Raised when a GitHub API call fails."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }


def get_registration_token(config: RunnerConfig) -> str:
    """Obtain a runner registration token from the GitHub API."""
    url = f"{GITHUB_API_BASE}/repos/{config.github_repository}/actions/runners/registration-token"
    resp = requests.post(url, headers=_headers(config.github_access_token), timeout=30)
    if resp.status_code != 201:
        raise GitHubAPIError(
            f"Failed to get registration token: {resp.status_code} {resp.text}",
            status_code=resp.status_code,
        )
    token = resp.json().get("token")
    if not token:
        raise GitHubAPIError("Registration token missing from response")
    return token


def list_runners(config: RunnerConfig) -> list[dict]:
    """List all self-hosted runners for the repository."""
    url = f"{GITHUB_API_BASE}/repos/{config.github_repository}/actions/runners"
    resp = requests.get(url, headers=_headers(config.github_access_token), timeout=30)
    if resp.status_code != 200:
        raise GitHubAPIError(
            f"Failed to list runners: {resp.status_code} {resp.text}",
            status_code=resp.status_code,
        )
    return resp.json().get("runners", [])


def remove_runner(config: RunnerConfig, runner_id: int) -> None:
    """Remove a self-hosted runner by ID."""
    url = f"{GITHUB_API_BASE}/repos/{config.github_repository}/actions/runners/{runner_id}"
    resp = requests.delete(url, headers=_headers(config.github_access_token), timeout=30)
    if resp.status_code != 204:
        raise GitHubAPIError(
            f"Failed to remove runner {runner_id}: {resp.status_code} {resp.text}",
            status_code=resp.status_code,
        )
