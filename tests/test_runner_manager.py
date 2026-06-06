"""Tests for runner_manager module — covers Docker subprocess flows."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from github_runners_for_repo.config import RunnerConfig
from github_runners_for_repo.runner_manager import (
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


class TestBuildImage:
    @patch("github_runners_for_repo.runner_manager.subprocess.run")
    def test_invokes_docker_compose_build(self, mock_run, config, capsys):
        mock_run.return_value = MagicMock(returncode=0)
        build_image(config)
        mock_run.assert_called_once_with(
            ["docker", "compose", "build"],
            check=True,
        )
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
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args[0][0] == [
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
        assert "2 runner(s) started" in captured.out

    @patch("github_runners_for_repo.runner_manager.subprocess.run")
    @patch("github_runners_for_repo.runner_manager.get_registration_token")
    def test_invalid_config_exits(self, mock_token, mock_run, config, capsys):
        bad = RunnerConfig(github_access_token="", github_repository="owner/repo")
        with pytest.raises(SystemExit) as excinfo:
            start_runners(bad)
        assert excinfo.value.code == 1
        mock_token.assert_not_called()
        mock_run.assert_not_called()
        captured = capsys.readouterr()
        assert "Configuration errors" in captured.err
        assert "GITHUB_ACCESS_TOKEN is required" in captured.err

    @patch("github_runners_for_repo.runner_manager.subprocess.run")
    @patch("github_runners_for_repo.runner_manager.get_registration_token")
    def test_repo_format_invalid_exits(
        self, mock_token, mock_run, config, capsys
    ):
        bad = RunnerConfig(github_access_token="x", github_repository="noslash")
        with pytest.raises(SystemExit):
            start_runners(bad)
        mock_token.assert_not_called()

    @patch("github_runners_for_repo.runner_manager.subprocess.run")
    @patch("github_runners_for_repo.runner_manager.get_registration_token")
    def test_zero_runner_count_exits(self, mock_token, mock_run, config, capsys):
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
        mock_run.assert_called_once_with(
            ["docker", "compose", "down"],
            check=True,
        )
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
