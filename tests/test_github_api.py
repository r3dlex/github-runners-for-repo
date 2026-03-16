"""Tests for GitHub API interactions."""

from unittest.mock import patch, MagicMock

import pytest

from github_runners_for_repo.config import RunnerConfig
from github_runners_for_repo.github_api import (
    GitHubAPIError,
    _check_repo_access,
    get_registration_token,
    list_runners,
    remove_runner,
    _headers,
)


@pytest.fixture
def config():
    return RunnerConfig(
        github_access_token="ghp_test123",
        github_repository="owner/repo",
    )


class TestHeaders:
    def test_creates_auth_header(self):
        h = _headers("ghp_abc")
        assert h["Authorization"] == "token ghp_abc"
        assert "application/vnd.github.v3+json" in h["Accept"]


class TestCheckRepoAccess:
    @patch("github_runners_for_repo.github_api.requests.get")
    def test_success(self, mock_get, config):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp
        _check_repo_access(config)  # should not raise

    @patch("github_runners_for_repo.github_api.requests.get")
    def test_repo_not_found(self, mock_get, config):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_get.return_value = mock_resp
        with pytest.raises(GitHubAPIError, match="not found"):
            _check_repo_access(config)

    @patch("github_runners_for_repo.github_api.requests.get")
    def test_auth_failure(self, mock_get, config):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_get.return_value = mock_resp
        with pytest.raises(GitHubAPIError, match="Authentication failed"):
            _check_repo_access(config)


class TestGetRegistrationToken:
    @patch("github_runners_for_repo.github_api.requests.post")
    @patch("github_runners_for_repo.github_api._check_repo_access")
    def test_success(self, mock_check, mock_post, config):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"token": "AABBC123"}
        mock_post.return_value = mock_resp

        token = get_registration_token(config)
        assert token == "AABBC123"
        mock_check.assert_called_once_with(config)
        mock_post.assert_called_once()

    @patch("github_runners_for_repo.github_api.requests.post")
    @patch("github_runners_for_repo.github_api._check_repo_access")
    def test_api_failure(self, mock_check, mock_post, config):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = "Forbidden"
        mock_post.return_value = mock_resp

        with pytest.raises(GitHubAPIError, match="403"):
            get_registration_token(config)

    @patch("github_runners_for_repo.github_api.requests.post")
    @patch("github_runners_for_repo.github_api._check_repo_access")
    def test_missing_token_in_response(self, mock_check, mock_post, config):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {}
        mock_post.return_value = mock_resp

        with pytest.raises(GitHubAPIError, match="missing"):
            get_registration_token(config)


class TestListRunners:
    @patch("github_runners_for_repo.github_api.requests.get")
    def test_success(self, mock_get, config):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "runners": [{"id": 1, "name": "runner-1", "status": "online"}]
        }
        mock_get.return_value = mock_resp

        runners = list_runners(config)
        assert len(runners) == 1
        assert runners[0]["name"] == "runner-1"


class TestRemoveRunner:
    @patch("github_runners_for_repo.github_api.requests.delete")
    def test_success(self, mock_delete, config):
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_delete.return_value = mock_resp

        remove_runner(config, runner_id=42)
        mock_delete.assert_called_once()

    @patch("github_runners_for_repo.github_api.requests.delete")
    def test_failure(self, mock_delete, config):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "Not Found"
        mock_delete.return_value = mock_resp

        with pytest.raises(GitHubAPIError, match="404"):
            remove_runner(config, runner_id=999)
