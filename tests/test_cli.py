"""Tests for the CLI subcommand dispatch."""

from pathlib import Path
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

    @patch("github_runners_for_repo.cli.provision")
    @patch("github_runners_for_repo.cli.load_config")
    def test_provision_command(self, mock_load, mock_provision):
        mock_load.return_value = "config"
        main(["provision"])
        mock_provision.assert_called_once_with("config")

    @patch("github_runners_for_repo.cli.install")
    @patch("github_runners_for_repo.cli.load_config")
    def test_install_command(self, mock_load, mock_install):
        mock_load.return_value = "config"
        main(["install"])
        mock_install.assert_called_once()
        args, _ = mock_install.call_args
        assert args[0] == "config"
        assert isinstance(args[1], Path)

    @patch("github_runners_for_repo.cli.install")
    @patch("github_runners_for_repo.cli.load_config")
    def test_install_with_version_and_target(self, mock_load, mock_install):
        mock_load.return_value = "config"
        main(["install", "--target-dir", "/tmp/r", "--version", "2.321.0"])
        mock_install.assert_called_once_with("config", Path("/tmp/r"), version="2.321.0")

    @patch("github_runners_for_repo.cli.configure")
    @patch("github_runners_for_repo.cli.load_config")
    def test_configure_command(self, mock_load, mock_configure):
        mock_load.return_value = "config"
        main(
            [
                "configure",
                "--target-dir",
                "/tmp/r",
                "--name",
                "my-runner",
                "--labels",
                "self-hosted,linux,r3dlex-org",
                "--runner-group",
                "Default",
                "--work-dir",
                "/tmp/work",
            ]
        )
        mock_configure.assert_called_once_with(
            "config",
            Path("/tmp/r"),
            name="my-runner",
            labels="self-hosted,linux,r3dlex-org",
            runner_group="Default",
            work_dir="/tmp/work",
            replace=True,
        )

    @patch("github_runners_for_repo.cli.configure")
    @patch("github_runners_for_repo.cli.load_config")
    def test_configure_no_replace_flag(self, mock_load, mock_configure):
        mock_load.return_value = "config"
        main(["configure", "--name", "r", "--no-replace"])
        assert mock_configure.call_args[1]["replace"] is False

    @patch("github_runners_for_repo.cli.start")
    @patch("github_runners_for_repo.cli.load_config")
    def test_start_command_default_foreground(self, mock_load, mock_start):
        mock_load.return_value = "config"
        main(["start"])
        mock_start.assert_called_once_with("config", Path("./actions-runner"), as_service=False)

    @patch("github_runners_for_repo.cli.start")
    @patch("github_runners_for_repo.cli.load_config")
    def test_start_command_with_service(self, mock_load, mock_start):
        mock_load.return_value = "config"
        main(["start", "--service", "--target-dir", "/var/runners/1"])
        mock_start.assert_called_once_with("config", Path("/var/runners/1"), as_service=True)

    @patch("github_runners_for_repo.cli.stop")
    @patch("github_runners_for_repo.cli.load_config")
    def test_stop_command(self, mock_load, mock_stop):
        mock_load.return_value = "config"
        main(["stop"])
        mock_stop.assert_called_once_with("config", Path("./actions-runner"))

    @patch("github_runners_for_repo.cli.status")
    @patch("github_runners_for_repo.cli.load_config")
    def test_status_command(self, mock_load, mock_status):
        mock_load.return_value = "config"
        main(["status"])
        mock_status.assert_called_once_with("config")

    @patch("github_runners_for_repo.cli.remove")
    @patch("github_runners_for_repo.cli.load_config")
    def test_remove_command(self, mock_load, mock_remove):
        mock_load.return_value = "config"
        main(["remove", "--name", "stale-runner"])
        mock_remove.assert_called_once_with("config", "stale-runner")

    @patch("github_runners_for_repo.cli.install")
    @patch("github_runners_for_repo.cli.load_config")
    def test_env_file_option(self, mock_load, mock_install):
        mock_load.return_value = "config"
        main(["--env-file", "/tmp/custom.env", "install"])
        mock_load.assert_called_once_with("/tmp/custom.env")
