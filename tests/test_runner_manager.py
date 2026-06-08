"""Tests for runner_manager — covers the native actions/runner binary flow."""

from __future__ import annotations

import tarfile
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from github_runners_for_repo.config import RunnerConfig
from github_runners_for_repo.runner_manager import (
    DEFAULT_RUNNER_VERSION,
    GITHUB_RUNNER_RELEASES,
    PLATFORM_PACKAGES,
    PROVISION_SCRIPT,
    RunnerError,
    configure,
    detect_platform,
    download_url,
    install,
    package_name,
    provision,
    remove,
    start,
    status,
    stop,
)


@pytest.fixture
def config():
    return RunnerConfig(
        github_access_token="ghp_test",
        github_repository="owner/repo",
        runner_count=2,
    )


@pytest.fixture
def org_config():
    return RunnerConfig(github_access_token="ghp_test", github_org="r3dlex")


@pytest.fixture
def installed_runner(tmp_path):
    """A tmp_path with a fake config.sh and run.sh and svc.sh present."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "config.sh").write_text("#!/bin/sh\n")
    (tmp_path / "config.sh").chmod(0o755)
    (tmp_path / "run.sh").write_text("#!/bin/sh\n")
    (tmp_path / "run.sh").chmod(0o755)
    (tmp_path / "svc.sh").write_text("#!/bin/sh\n")
    (tmp_path / "svc.sh").chmod(0o755)
    return tmp_path


class TestPlatformHelpers:
    def test_package_name_known_platform(self):
        assert (
            package_name("Linux", "x86_64", "2.334.0") == "actions-runner-linux-x64-2.334.0.tar.gz"
        )
        assert (
            package_name("Darwin", "arm64", "2.334.0") == "actions-runner-osx-arm64-2.334.0.tar.gz"
        )
        assert (
            package_name("Linux", "arm64", "2.334.0") == "actions-runner-linux-arm64-2.334.0.tar.gz"
        )
        assert (
            package_name("Linux", "aarch64", "2.334.0")
            == "actions-runner-linux-arm64-2.334.0.tar.gz"
        )
        assert package_name("Windows", "AMD64", "2.334.0") == "actions-runner-win-x64-2.334.0.zip"

    def test_package_name_unknown_platform_raises(self):
        with pytest.raises(RunnerError, match="Unsupported platform"):
            package_name("Plan9", "mips", "2.334.0")

    def test_download_url_format(self):
        url = download_url("2.334.0")
        assert url.startswith(GITHUB_RUNNER_RELEASES)
        assert "v2.334.0" in url
        # Filename should match what the current host expects
        os_name, arch = detect_platform()
        if (os_name, arch) in PLATFORM_PACKAGES:
            assert package_name(os_name, arch, "2.334.0") in url


class TestProvision:
    def test_provision_script_is_bundled(self):
        assert PROVISION_SCRIPT.exists()
        assert PROVISION_SCRIPT.name == "provision-host.sh"

    @patch("github_runners_for_repo.runner_manager.subprocess.run")
    def test_provision_runs_bundled_script(self, mock_run, config, capsys):
        mock_run.return_value = MagicMock(returncode=0)
        provision(config)
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "bash"
        assert cmd[1] == str(PROVISION_SCRIPT)
        assert mock_run.call_args[1]["check"] is True
        captured = capsys.readouterr()
        assert "Provisioning host" in captured.out
        assert "complete" in captured.out

    @patch("github_runners_for_repo.runner_manager.subprocess.run")
    def test_provision_missing_script_raises(self, mock_run, config):
        with patch("github_runners_for_repo.runner_manager.PROVISION_SCRIPT") as fake:
            fake.exists.return_value = False
            with pytest.raises(RunnerError, match="Provisioning script not found"):
                provision(config)
        mock_run.assert_not_called()


class TestInstall:
    @patch("github_runners_for_repo.runner_manager.urllib.request.urlretrieve")
    @patch("github_runners_for_repo.runner_manager.tarfile.open")
    def test_install_downloads_and_extracts(self, mock_tar, mock_urlretrieve, config, tmp_path):
        mock_tar_ctx = MagicMock()
        mock_tar.return_value.__enter__.return_value = mock_tar_ctx
        target = tmp_path / "runner"

        result = install(config, target, version="2.334.0")

        assert result == target.resolve()
        mock_urlretrieve.assert_called_once()
        called_url = mock_urlretrieve.call_args[0][0]
        assert called_url.startswith(GITHUB_RUNNER_RELEASES)
        assert "v2.334.0" in called_url
        mock_tar_ctx.extractall.assert_called_once_with(target.resolve())

    @patch("github_runners_for_repo.runner_manager.urllib.request.urlretrieve")
    def test_install_uses_default_version(self, mock_urlretrieve, config, tmp_path):
        mock_urlretrieve.return_value = None
        mock_tar = MagicMock()
        with patch("github_runners_for_repo.runner_manager.tarfile.open", mock_tar):
            install(config, tmp_path / "r")
        called_url = mock_urlretrieve.call_args[0][0]
        assert f"v{DEFAULT_RUNNER_VERSION}" in called_url

    @patch("github_runners_for_repo.runner_manager.urllib.request.urlretrieve")
    def test_install_propagates_url_error(self, mock_urlretrieve, config, tmp_path):
        mock_urlretrieve.side_effect = urllib.error.URLError("net down")
        with pytest.raises(RunnerError, match="Failed to download"):
            install(config, tmp_path / "r", version="2.334.0")

    @patch("github_runners_for_repo.runner_manager.urllib.request.urlretrieve")
    def test_install_propagates_tar_error(self, mock_urlretrieve, config, tmp_path):
        mock_urlretrieve.return_value = None
        mock_tar = MagicMock()
        mock_tar.return_value.__enter__.side_effect = tarfile.TarError("bad")
        with patch("github_runners_for_repo.runner_manager.tarfile.open", mock_tar):
            with pytest.raises(RunnerError, match="Failed to extract"):
                install(config, tmp_path / "r", version="2.334.0")

    def test_install_creates_target_dir(self, config, tmp_path):
        target = tmp_path / "deep" / "nested" / "runner"
        with (
            patch("github_runners_for_repo.runner_manager.urllib.request.urlretrieve"),
            patch("github_runners_for_repo.runner_manager.tarfile.open", MagicMock()),
        ):
            install(config, target, version="2.334.0")
        assert target.exists()


class TestConfigure:
    @patch("github_runners_for_repo.runner_manager.subprocess.run")
    @patch("github_runners_for_repo.runner_manager.get_registration_token")
    def test_configure_runs_config_sh(self, mock_token, mock_run, config, installed_runner, capsys):
        mock_token.return_value = "TKN123"
        mock_run.return_value = MagicMock(returncode=0)

        configure(
            config,
            installed_runner,
            name="my-runner",
            labels="self-hosted,linux,x64,r3dlex-org",
            runner_group="Default",
        )

        mock_token.assert_called_once_with(config)
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        cwd = mock_run.call_args[1].get("cwd")
        assert cmd[0] == str(installed_runner / "config.sh")
        assert "--url" in cmd
        assert config.runner_url in cmd
        assert "--token" in cmd
        assert "TKN123" in cmd
        assert "--name" in cmd
        assert "my-runner" in cmd
        assert "--labels" in cmd
        assert "self-hosted,linux,x64,r3dlex-org" in cmd
        assert "--runnergroup" in cmd
        assert "Default" in cmd
        assert "--unattended" in cmd
        assert "--replace" in cmd
        assert cwd == installed_runner

    @patch("github_runners_for_repo.runner_manager.subprocess.run")
    @patch("github_runners_for_repo.runner_manager.get_registration_token")
    def test_configure_no_replace_omits_flag(self, mock_token, mock_run, config, installed_runner):
        mock_token.return_value = "TKN"
        mock_run.return_value = MagicMock(returncode=0)

        configure(
            config,
            installed_runner,
            name="r",
            labels="a,b",
            runner_group="Default",
            replace=False,
        )

        cmd = mock_run.call_args[0][0]
        assert "--replace" not in cmd

    @patch("github_runners_for_repo.runner_manager.subprocess.run")
    @patch("github_runners_for_repo.runner_manager.get_registration_token")
    def test_configure_with_work_dir(self, mock_token, mock_run, config, installed_runner):
        mock_token.return_value = "TKN"
        mock_run.return_value = MagicMock(returncode=0)

        configure(
            config,
            installed_runner,
            name="r",
            labels="a,b",
            runner_group="Default",
            work_dir="/tmp/runner-work",
        )

        cmd = mock_run.call_args[0][0]
        assert "--work" in cmd
        assert "/tmp/runner-work" in cmd

    @patch("github_runners_for_repo.runner_manager.subprocess.run")
    @patch("github_runners_for_repo.runner_manager.get_registration_token")
    def test_configure_org_uses_org_url(self, mock_token, mock_run, org_config, installed_runner):
        mock_token.return_value = "TKN"
        mock_run.return_value = MagicMock(returncode=0)

        configure(
            org_config,
            installed_runner,
            name="r",
            labels="a,b",
            runner_group="Default",
        )

        cmd = mock_run.call_args[0][0]
        assert "https://github.com/r3dlex" in cmd

    @patch("github_runners_for_repo.runner_manager.subprocess.run")
    @patch("github_runners_for_repo.runner_manager.get_registration_token")
    def test_configure_uses_config_defaults(self, mock_token, mock_run, config, installed_runner):
        mock_token.return_value = "TKN"
        mock_run.return_value = MagicMock(returncode=0)

        configure(config, installed_runner, name="r")

        cmd = mock_run.call_args[0][0]
        assert config.runner_labels in cmd
        assert config.runner_group in cmd

    def test_configure_requires_installed_runner(self, config, tmp_path):
        with pytest.raises(RunnerError, match="config.sh missing"):
            configure(config, tmp_path, name="r")


class TestStart:
    @patch("github_runners_for_repo.runner_manager.os.execv")
    @patch("github_runners_for_repo.runner_manager.subprocess.run")
    def test_start_foreground_uses_run_sh(
        self, mock_run, mock_execv, config, installed_runner, capsys
    ):
        start(config, installed_runner, as_service=False)
        mock_execv.assert_called_once()
        assert str(installed_runner / "run.sh") in mock_execv.call_args[0][0]
        mock_run.assert_not_called()
        captured = capsys.readouterr()
        assert "Starting runner in foreground" in captured.out

    @patch("github_runners_for_repo.runner_manager.subprocess.run")
    def test_start_as_service_uses_svc_sh(self, mock_run, config, installed_runner, capsys):
        start(config, installed_runner, as_service=True)
        assert mock_run.call_count == 2
        first = mock_run.call_args_list[0][0][0]
        second = mock_run.call_args_list[1][0][0]
        assert first[0].endswith("svc.sh") and first[1] == "install"
        assert second[0].endswith("svc.sh") and second[1] == "start"
        assert mock_run.call_args_list[0][1]["cwd"] == installed_runner
        captured = capsys.readouterr()
        assert "Runner service installed and started" in captured.out

    def test_start_as_service_without_svc_raises(self, config, tmp_path):
        (tmp_path / "config.sh").write_text("#!/bin/sh\n")
        (tmp_path / "run.sh").write_text("#!/bin/sh\n")
        with pytest.raises(RunnerError, match="svc.sh not found"):
            start(config, tmp_path, as_service=True)

    def test_start_foreground_without_run_sh_raises(self, config, tmp_path):
        (tmp_path / "config.sh").write_text("#!/bin/sh\n")
        with pytest.raises(RunnerError, match="run.sh not found"):
            start(config, tmp_path, as_service=False)


class TestStop:
    @patch("github_runners_for_repo.runner_manager.subprocess.run")
    def test_stop_with_svc_runs_stop_and_uninstall(
        self, mock_run, config, installed_runner, capsys
    ):
        stop(config, installed_runner)
        assert mock_run.call_count == 2
        assert mock_run.call_args_list[0][0][0][-1] == "stop"
        assert mock_run.call_args_list[1][0][0][-1] == "uninstall"
        captured = capsys.readouterr()
        assert "stopped and uninstalled" in captured.out

    def test_stop_without_svc_prints_hint(self, config, tmp_path, capsys):
        stop(config, tmp_path)
        captured = capsys.readouterr()
        assert "No svc.sh found" in captured.out


class TestStatus:
    @patch("github_runners_for_repo.runner_manager.list_runners")
    def test_status_with_runners(self, mock_list, config, capsys):
        mock_list.return_value = [
            {
                "id": 7,
                "name": "runner-1",
                "status": "online",
                "busy": False,
                "os": "linux",
                "labels": [{"name": "self-hosted"}, {"name": "linux"}],
            }
        ]
        status(config)
        captured = capsys.readouterr()
        assert "https://github.com/owner/repo" in captured.out
        assert "scope=repo" in captured.out
        assert "runner-1" in captured.out
        assert "id=7" in captured.out
        assert "self-hosted" in captured.out

    @patch("github_runners_for_repo.runner_manager.list_runners")
    def test_status_org_scope(self, mock_list, org_config, capsys):
        mock_list.return_value = []
        status(org_config)
        captured = capsys.readouterr()
        assert "https://github.com/r3dlex" in captured.out
        assert "scope=org" in captured.out
        assert "No runners registered" in captured.out

    @patch("github_runners_for_repo.runner_manager.list_runners")
    def test_status_handles_api_failure(self, mock_list, config, capsys):
        mock_list.side_effect = RuntimeError("boom")
        status(config)
        captured = capsys.readouterr()
        assert "Could not fetch runners" in captured.err
        assert "boom" in captured.err


class TestRemove:
    @patch("github_runners_for_repo.runner_manager.remove_runner")
    @patch("github_runners_for_repo.runner_manager.list_runners")
    def test_remove_finds_and_deletes(self, mock_list, mock_delete, config, capsys):
        mock_list.return_value = [{"id": 42, "name": "r1"}, {"id": 43, "name": "r2"}]
        remove(config, "r2")
        mock_delete.assert_called_once_with(config, 43)
        captured = capsys.readouterr()
        assert "r2" in captured.out
        assert "id=43" in captured.out
        assert "removed" in captured.out

    @patch("github_runners_for_repo.runner_manager.remove_runner")
    @patch("github_runners_for_repo.runner_manager.list_runners")
    def test_remove_missing_name_is_noop(self, mock_list, mock_delete, config, capsys):
        mock_list.return_value = [{"id": 1, "name": "r1"}]
        remove(config, "ghost")
        mock_delete.assert_not_called()
        captured = capsys.readouterr()
        assert "not found" in captured.out


# Imported here (not at top) to keep the test header tidy and to avoid
# clashing with the pytest fixtures above.
