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


def _check_repo_access(config: RunnerConfig) -> None:
    """Verify the token can access the repository, raising a clear error if not."""
    url = f"{GITHUB_API_BASE}/repos/{config.github_repository}"
    resp = requests.get(url, headers=_headers(config.github_access_token), timeout=30)
    if resp.status_code == 404:
        raise GitHubAPIError(
            f"Repository '{config.github_repository}' not found. Check that:\n"
            "  1. GITHUB_REPOSITORY is correct (owner/repo, case-sensitive)\n"
            "  2. Your token has access to this repository\n"
            "  3. For fine-grained PATs: the token must be granted access to the specific repo\n"
            "     with 'Administration: Read and write' permission",
            status_code=404,
        )
    if resp.status_code == 401:
        raise GitHubAPIError(
            "Authentication failed. Your GITHUB_ACCESS_TOKEN is invalid or expired.",
            status_code=401,
        )


def get_registration_token(config: RunnerConfig) -> str:
    """Obtain a runner registration token from the GitHub API."""
    _check_repo_access(config)
    url = f"{GITHUB_API_BASE}/repos/{config.github_repository}/actions/runners/registration-token"
    resp = requests.post(url, headers=_headers(config.github_access_token), timeout=30)
    if resp.status_code != 201:
        raise GitHubAPIError(
            f"Failed to get registration token: {resp.status_code} {resp.text}\n"
            "  Hint: your token may be missing the 'Administration: Read and write' permission",
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
