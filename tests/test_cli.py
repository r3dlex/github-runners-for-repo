"""Tests for CLI entry point."""

from unittest.mock import patch

import pytest

from github_runners_for_repo.cli import main


class TestCLI:
    def test_no_args_exits(self):
        with pytest.raises(SystemExit):
            main([])

    def test_help_exits(self):
        with pytest.raises(SystemExit):
            main(["--help"])

    @patch("github_runners_for_repo.cli.build_image")
    @patch("github_runners_for_repo.cli.load_config")
    def test_build_command(self, mock_load, mock_build):
        mock_load.return_value = "config"
        main(["build"])
        mock_build.assert_called_once_with("config")

    @patch("github_runners_for_repo.cli.start_runners")
    @patch("github_runners_for_repo.cli.load_config")
    def test_start_command(self, mock_load, mock_start):
        mock_load.return_value = "config"
        main(["start"])
        mock_start.assert_called_once_with("config")

    @patch("github_runners_for_repo.cli.stop_runners")
    @patch("github_runners_for_repo.cli.load_config")
    def test_stop_command(self, mock_load, mock_stop):
        mock_load.return_value = "config"
        main(["stop"])
        mock_stop.assert_called_once_with("config")

    @patch("github_runners_for_repo.cli.status")
    @patch("github_runners_for_repo.cli.load_config")
    def test_status_command(self, mock_load, mock_status):
        mock_load.return_value = "config"
        main(["status"])
        mock_status.assert_called_once_with("config")

    @patch("github_runners_for_repo.cli.build_image")
    @patch("github_runners_for_repo.cli.load_config")
    def test_env_file_option(self, mock_load, mock_build):
        mock_load.return_value = "config"
        main(["--env-file", "/tmp/custom.env", "build"])
        mock_load.assert_called_once_with("/tmp/custom.env")
