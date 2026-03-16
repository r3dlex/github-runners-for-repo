"""Tests for configuration management."""

from github_runners_for_repo.config import RunnerConfig, load_config


class TestRunnerConfig:
    def test_valid_config(self):
        config = RunnerConfig(
            github_access_token="ghp_test123",
            github_repository="owner/repo",
        )
        assert config.validate() == []

    def test_missing_token(self):
        config = RunnerConfig(github_access_token="", github_repository="owner/repo")
        errors = config.validate()
        assert any("GITHUB_ACCESS_TOKEN" in e for e in errors)

    def test_missing_repository(self):
        config = RunnerConfig(github_access_token="ghp_test", github_repository="")
        errors = config.validate()
        assert any("GITHUB_REPOSITORY" in e for e in errors)

    def test_invalid_repository_format(self):
        config = RunnerConfig(github_access_token="ghp_test", github_repository="noslash")
        errors = config.validate()
        assert any("owner/repo" in e for e in errors)

    def test_invalid_runner_count(self):
        config = RunnerConfig(
            github_access_token="ghp_test",
            github_repository="owner/repo",
            runner_count=0,
        )
        errors = config.validate()
        assert any("RUNNER_COUNT" in e for e in errors)

    def test_repo_owner_and_name(self):
        config = RunnerConfig(
            github_access_token="ghp_test",
            github_repository="myorg/myrepo",
        )
        assert config.repo_owner == "myorg"
        assert config.repo_name == "myrepo"

    def test_default_values(self):
        config = RunnerConfig(
            github_access_token="ghp_test",
            github_repository="owner/repo",
        )
        assert config.runner_name_prefix == "runner"
        assert config.runner_count == 1
        assert config.runner_labels == "self-hosted,linux,x64"
        assert config.runner_group == "Default"


class TestLoadConfig:
    def test_load_from_env(self, monkeypatch):
        monkeypatch.setenv("GITHUB_ACCESS_TOKEN", "ghp_envtest")
        monkeypatch.setenv("GITHUB_REPOSITORY", "test/repo")
        monkeypatch.setenv("RUNNER_COUNT", "3")

        config = load_config(env_file=None)
        assert config.github_access_token == "ghp_envtest"
        assert config.github_repository == "test/repo"
        assert config.runner_count == 3

    def test_load_from_env_file(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "GITHUB_ACCESS_TOKEN=ghp_filetest\n" "GITHUB_REPOSITORY=file/repo\n" "RUNNER_COUNT=2\n"
        )
        config = load_config(str(env_file))
        assert config.github_access_token == "ghp_filetest"
        assert config.github_repository == "file/repo"
        assert config.runner_count == 2
