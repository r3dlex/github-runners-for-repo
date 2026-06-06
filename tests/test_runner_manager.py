"""Tests for runner_manager module — covers Docker subprocess flows."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from github_runners_for_repo import runner_manager
from github_runners_for_repo.config import RunnerConfig
from github_runners_for_repo.runner_manager import (
    _require_docker_compose,
    build_image,
    start_runners,
    status,
    stop_runners,
)


@pytest.fixture
def config():
    return RunnerConfig(
        github_access_token="ghp_test",
        github_repository="owner/repo",
        runner_count=2,
    )


@pytest.fixture(autouse=True)
def _reset_docker_compose_cache():
    """Reset module-level cache so tests don't leak state between cases."""
    runner_manager._docker_compose_checked = False
    yield
    runner_manager._docker_compose_checked = False


class TestBuildImage:
    @patch("github_runners_for_repo.runner_manager.subprocess.run")
    def test_invokes_docker_compose_build(self, mock_run, config, capsys):
        mock_run.return_value = MagicMock(returncode=0)
        build_image(config)
        # Two calls expected: one for `docker compose version`, one for build
        assert mock_run.call_count == 2
        build_call = mock_run.call_args_list[1]
        assert build_call[0][0] == ["docker", "compose", "build"]
        assert build_call[1] == {"check": True}
        captured = capsys.readouterr()
        assert config.runner_image in captured.out


class TestStartRunners:
    @patch("github_runners_for_repo.runner_manager.subprocess.run")
    @patch("github_runners_for_repo.runner_manager.get_registration_token")
    def test_success(self, mock_token, mock_run, config, capsys):
        mock_token.return_value = "TOKEN123"
        mock_run.return_value = MagicMock(returncode=0)
        start_runners(config)
        mock_token.assert_called_once_with(config)
        # version check + docker compose up
        assert mock_run.call_count == 2
        up_call = mock_run.call_args_list[1]
        assert up_call[0][0] == [
            "docker",
            "compose",
            "up",
            "--scale",
            "runner=2",
            "-d",
            "--build",
        ]
        captured = capsys.readouterr()
        assert "Registration token obtained" in captured.out
        assert "Starting 2 runner(s)" in captured.out
        assert "owner/repo" in captured.out
        assert "2 runner(s) started" in captured.out

    @patch("github_runners_for_repo.runner_manager.subprocess.run")
    @patch("github_runners_for_repo.runner_manager.get_registration_token")
    def test_invalid_config_exits(self, mock_token, mock_run, capsys):
        bad = RunnerConfig(github_access_token="", github_repository="owner/repo")
        with pytest.raises(SystemExit) as excinfo:
            start_runners(bad)
        assert excinfo.value.code == 1
        mock_token.assert_not_called()
        captured = capsys.readouterr()
        assert "Configuration errors" in captured.err
        assert "GITHUB_ACCESS_TOKEN is required" in captured.err

    @patch("github_runners_for_repo.runner_manager.subprocess.run")
    @patch("github_runners_for_repo.runner_manager.get_registration_token")
    def test_repo_format_invalid_exits(self, mock_token, mock_run):
        bad = RunnerConfig(github_access_token="x", github_repository="noslash")
        with pytest.raises(SystemExit):
            start_runners(bad)
        mock_token.assert_not_called()

    @patch("github_runners_for_repo.runner_manager.subprocess.run")
    @patch("github_runners_for_repo.runner_manager.get_registration_token")
    def test_zero_runner_count_exits(self, mock_token, mock_run):
        bad = RunnerConfig(
            github_access_token="x",
            github_repository="owner/repo",
            runner_count=0,
        )
        with pytest.raises(SystemExit):
            start_runners(bad)
        mock_token.assert_not_called()


class TestStopRunners:
    @patch("github_runners_for_repo.runner_manager.subprocess.run")
    def test_invokes_docker_compose_down(self, mock_run, config, capsys):
        mock_run.return_value = MagicMock(returncode=0)
        stop_runners(config)
        assert mock_run.call_count == 2
        down_call = mock_run.call_args_list[1]
        assert down_call[0][0] == ["docker", "compose", "down"]
        captured = capsys.readouterr()
        assert "Stopping runners" in captured.out
        assert "Runners stopped" in captured.out


class TestStatus:
    @patch("github_runners_for_repo.runner_manager.subprocess.run")
    @patch("github_runners_for_repo.runner_manager.list_runners")
    def test_with_runners(self, mock_list, mock_run, config, capsys):
        mock_run.return_value = MagicMock(returncode=0)
        mock_list.return_value = [
            {
                "id": 7,
                "name": "runner-1",
                "status": "online",
                "labels": [{"name": "self-hosted"}, {"name": "linux"}],
            }
        ]
        status(config)
        mock_list.assert_called_once_with(config)
        captured = capsys.readouterr()
        assert "Docker Containers" in captured.out
        assert "Registered GitHub Runners" in captured.out
        assert "runner-1" in captured.out
        assert "id=7" in captured.out
        assert "self-hosted" in captured.out
        assert "linux" in captured.out

    @patch("github_runners_for_repo.runner_manager.subprocess.run")
    @patch("github_runners_for_repo.runner_manager.list_runners")
    def test_with_no_runners(self, mock_list, mock_run, config, capsys):
        mock_run.return_value = MagicMock(returncode=0)
        mock_list.return_value = []
        status(config)
        captured = capsys.readouterr()
        assert "No runners registered" in captured.out

    @patch("github_runners_for_repo.runner_manager.subprocess.run")
    @patch("github_runners_for_repo.runner_manager.list_runners")
    def test_list_runners_raises(self, mock_list, mock_run, config, capsys):
        mock_run.return_value = MagicMock(returncode=0)
        mock_list.side_effect = RuntimeError("API down")
        status(config)
        captured = capsys.readouterr()
        assert "Could not fetch runners" in captured.out
        assert "API down" in captured.out


class TestDockerComposeDetection:
    @patch("github_runners_for_repo.runner_manager.subprocess.run")
    def test_missing_compose_exits(self, mock_run, config, capsys):
        mock_run.side_effect = FileNotFoundError("docker not found")
        with pytest.raises(SystemExit) as excinfo:
            build_image(config)
        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        assert "docker compose" in captured.err
        assert "brew install docker-compose" in captured.err

    @patch("github_runners_for_repo.runner_manager.subprocess.run")
    def test_compose_v1_fails_with_hint(self, mock_run, config, capsys):
        # v1 'docker-compose' is not the v2 plugin — Docker prints this error.
        err = subprocess.CalledProcessError(
            returncode=1,
            cmd=["docker", "compose", "version"],
            stderr="docker: 'compose' is not a docker command",
        )
        mock_run.side_effect = err
        with pytest.raises(SystemExit) as excinfo:
            build_image(config)
        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        assert "docker-compose" in captured.err

    @patch("github_runners_for_repo.runner_manager.subprocess.run")
    @patch("github_runners_for_repo.runner_manager.get_registration_token")
    def test_cached_after_first_call(self, mock_token, mock_run, config):
        mock_token.return_value = "TKN"
        mock_run.return_value = MagicMock(returncode=0, stdout="v2.x")
        build_image(config)
        start_runners(config)
        # First call: build_image does version-check + build (2 subprocess calls).
        # Second call: start_runners should reuse cache, so only `docker compose up` runs (1 subprocess call).
        assert mock_run.call_count == 3
        # The second-call arguments for start_runners should NOT include 'version'
        up_call = mock_run.call_args_list[2]
        assert up_call[0][0] == [
            "docker",
            "compose",
            "up",
            "--scale",
            "runner=2",
            "-d",
            "--build",
        ]

    @patch("github_runners_for_repo.runner_manager.subprocess.run")
    def test_compose_check_succeeds(self, mock_run, config):
        mock_run.return_value = MagicMock(returncode=0, stdout="v2.20.0")
        # Should not raise
        _require_docker_compose()
        # Calling again should be a no-op
        _require_docker_compose()
        # Only one subprocess call (for the first check)
        assert mock_run.call_count == 1
        assert mock_run.call_args[0][0] == ["docker", "compose", "version"]


# Imported here (not at top) to keep the test header tidy and to avoid
# clashing with the pytest fixture above.
import subprocess  # noqa: E402
