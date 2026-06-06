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


def _check_access(config: RunnerConfig) -> None:
    """Verify the token can access the org or repo, raising a clear error if not."""
    if config.runner_scope == "org":
        url = f"{GITHUB_API_BASE}/orgs/{config.github_org}"
    else:
        url = f"{GITHUB_API_BASE}/repos/{config.github_repository}"
    resp = requests.get(url, headers=_headers(config.github_access_token), timeout=30)
    if resp.status_code == 404:
        if config.runner_scope == "org":
            raise GitHubAPIError(
                f"Organization '{config.github_org}' not found. Check that:\n"
                "  1. GITHUB_ORG is correct (org slug, case-sensitive)\n"
                "  2. Your token has the 'admin:org' scope (classic PAT) or\n"
                "     'Administration: Read and write' for the org (fine-grained PAT)\n"
                "  3. The token's SSO has been authorized for the org (if applicable)",
                status_code=404,
            )
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
    _check_access(config)
    url = f"{GITHUB_API_BASE}/{config.api_path}/registration-token"
    resp = requests.post(url, headers=_headers(config.github_access_token), timeout=30)
    if resp.status_code != 201:
        if config.runner_scope == "org":
            hint = (
                "Your token is missing the 'admin:org' scope (classic PAT) or "
                "the 'Administration: Read and write' permission for the org "
                "(fine-grained PAT)"
            )
        else:
            hint = (
                "Your token may be missing the 'repo' scope (classic PAT) or the "
                "'Administration: Read and write' permission for the repo "
                "(fine-grained PAT)"
            )
        raise GitHubAPIError(
            f"Failed to get registration token: {resp.status_code} {resp.text}\n  Hint: {hint}",
            status_code=resp.status_code,
        )
    token = resp.json().get("token")
    if not token:
        raise GitHubAPIError("Registration token missing from response")
    return token


def list_runners(config: RunnerConfig) -> list[dict]:
    """List all self-hosted runners for the org or repository."""
    url = f"{GITHUB_API_BASE}/{config.api_path}"
    resp = requests.get(url, headers=_headers(config.github_access_token), timeout=30)
    if resp.status_code != 200:
        raise GitHubAPIError(
            f"Failed to list runners: {resp.status_code} {resp.text}",
            status_code=resp.status_code,
        )
    return resp.json().get("runners", [])


def remove_runner(config: RunnerConfig, runner_id: int) -> None:
    """Remove a self-hosted runner by ID."""
    url = f"{GITHUB_API_BASE}/{config.api_path}/{runner_id}"
    resp = requests.delete(url, headers=_headers(config.github_access_token), timeout=30)
    if resp.status_code != 204:
        raise GitHubAPIError(
            f"Failed to remove runner {runner_id}: {resp.status_code} {resp.text}",
            status_code=resp.status_code,
        )
