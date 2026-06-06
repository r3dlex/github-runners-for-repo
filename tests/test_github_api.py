"""Tests for GitHub API interactions."""

from unittest.mock import patch, MagicMock

import pytest

from github_runners_for_repo.config import RunnerConfig
from github_runners_for_repo.github_api import (
    GitHubAPIError,
    _check_access,
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


@pytest.fixture
def org_config():
    return RunnerConfig(
        github_access_token="ghp_test123",
        github_org="r3dlex",
    )


class TestHeaders:
    def test_creates_auth_header(self):
        h = _headers("ghp_abc")
        assert h["Authorization"] == "token ghp_abc"
        assert "application/vnd.github.v3+json" in h["Accept"]


class TestCheckAccess:
    @patch("github_runners_for_repo.github_api.requests.get")
    def test_success(self, mock_get, config):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp
        _check_access(config)  # should not raise

    @patch("github_runners_for_repo.github_api.requests.get")
    def test_repo_not_found(self, mock_get, config):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_get.return_value = mock_resp
        with pytest.raises(GitHubAPIError, match="not found"):
            _check_access(config)

    @patch("github_runners_for_repo.github_api.requests.get")
    def test_auth_failure(self, mock_get, config):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_get.return_value = mock_resp
        with pytest.raises(GitHubAPIError, match="Authentication failed"):
            _check_access(config)

    @patch("github_runners_for_repo.github_api.requests.get")
    def test_org_check_access_success(self, mock_get, org_config):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp
        _check_access(org_config)
        called_url = mock_get.call_args[0][0]
        assert called_url == "https://api.github.com/orgs/r3dlex"

    @patch("github_runners_for_repo.github_api.requests.get")
    def test_org_check_access_not_found(self, mock_get, org_config):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_get.return_value = mock_resp
        with pytest.raises(GitHubAPIError) as excinfo:
            _check_access(org_config)
        assert "admin:org" in str(excinfo.value)

    @patch("github_runners_for_repo.github_api.requests.get")
    def test_org_check_access_auth_failure(self, mock_get, org_config):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_get.return_value = mock_resp
        with pytest.raises(GitHubAPIError, match="Authentication failed"):
            _check_access(org_config)

    @patch("github_runners_for_repo.github_api.requests.get")
    def test_repo_check_access_uses_repo_url(self, mock_get, config):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp
        _check_access(config)
        called_url = mock_get.call_args[0][0]
        assert called_url == "https://api.github.com/repos/owner/repo"


class TestGetRegistrationToken:
    @patch("github_runners_for_repo.github_api.requests.post")
    @patch("github_runners_for_repo.github_api._check_access")
    def test_success(self, mock_check, mock_post, config):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"token": "AABBC123"}
        mock_post.return_value = mock_resp

        token = get_registration_token(config)
        assert token == "AABBC123"
        mock_check.assert_called_once_with(config)
        mock_post.assert_called_once()
        called_url = mock_post.call_args[0][0]
        assert (
            called_url
            == "https://api.github.com/repos/owner/repo/actions/runners/registration-token"
        )

    @patch("github_runners_for_repo.github_api.requests.post")
    @patch("github_runners_for_repo.github_api._check_access")
    def test_api_failure(self, mock_check, mock_post, config):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = "Forbidden"
        mock_post.return_value = mock_resp

        with pytest.raises(GitHubAPIError, match="403"):
            get_registration_token(config)

    @patch("github_runners_for_repo.github_api.requests.post")
    @patch("github_runners_for_repo.github_api._check_access")
    def test_missing_token_in_response(self, mock_check, mock_post, config):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {}
        mock_post.return_value = mock_resp

        with pytest.raises(GitHubAPIError, match="missing"):
            get_registration_token(config)

    @patch("github_runners_for_repo.github_api.requests.post")
    @patch("github_runners_for_repo.github_api._check_access")
    def test_org_success(self, mock_check, mock_post, org_config):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"token": "ORGTKN"}
        mock_post.return_value = mock_resp

        token = get_registration_token(org_config)
        assert token == "ORGTKN"
        mock_check.assert_called_once_with(org_config)
        called_url = mock_post.call_args[0][0]
        assert called_url == "https://api.github.com/orgs/r3dlex/actions/runners/registration-token"

    @patch("github_runners_for_repo.github_api.requests.post")
    @patch("github_runners_for_repo.github_api._check_access")
    def test_org_api_failure_403(self, mock_check, mock_post, org_config):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = "Forbidden"
        mock_post.return_value = mock_resp

        with pytest.raises(GitHubAPIError) as excinfo:
            get_registration_token(org_config)
        assert "admin:org" in str(excinfo.value)

    @patch("github_runners_for_repo.github_api.requests.post")
    @patch("github_runners_for_repo.github_api._check_access")
    def test_repo_api_failure_403_mentions_repo(self, mock_check, mock_post, config):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = "Forbidden"
        mock_post.return_value = mock_resp

        with pytest.raises(GitHubAPIError) as excinfo:
            get_registration_token(config)
        msg = str(excinfo.value)
        # Repo-scope hint should mention 'repo' scope (or fine-grained permission)
        assert "Administration" in msg or "repo" in msg


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
        called_url = mock_get.call_args[0][0]
        assert called_url == "https://api.github.com/repos/owner/repo/actions/runners"

    @patch("github_runners_for_repo.github_api.requests.get")
    def test_non_200_raises(self, mock_get, config):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "boom"
        mock_get.return_value = mock_resp

        with pytest.raises(GitHubAPIError, match="500"):
            list_runners(config)

    @patch("github_runners_for_repo.github_api.requests.get")
    def test_org_success(self, mock_get, org_config):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "runners": [{"id": 9, "name": "org-runner", "status": "online"}]
        }
        mock_get.return_value = mock_resp

        runners = list_runners(org_config)
        assert len(runners) == 1
        assert runners[0]["name"] == "org-runner"
        called_url = mock_get.call_args[0][0]
        assert called_url == "https://api.github.com/orgs/r3dlex/actions/runners"


class TestRemoveRunner:
    @patch("github_runners_for_repo.github_api.requests.delete")
    def test_success(self, mock_delete, config):
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_delete.return_value = mock_resp

        remove_runner(config, runner_id=42)
        mock_delete.assert_called_once()
        called_url = mock_delete.call_args[0][0]
        assert called_url == "https://api.github.com/repos/owner/repo/actions/runners/42"

    @patch("github_runners_for_repo.github_api.requests.delete")
    def test_failure(self, mock_delete, config):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "Not Found"
        mock_delete.return_value = mock_resp

        with pytest.raises(GitHubAPIError, match="404"):
            remove_runner(config, runner_id=999)

    @patch("github_runners_for_repo.github_api.requests.delete")
    def test_org_success(self, mock_delete, org_config):
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_delete.return_value = mock_resp

        remove_runner(org_config, runner_id=77)
        mock_delete.assert_called_once()
        called_url = mock_delete.call_args[0][0]
        assert called_url == "https://api.github.com/orgs/r3dlex/actions/runners/77"
