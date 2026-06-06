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
        assert config.runner_count == 4
        assert config.runner_labels == "self-hosted,linux,x64"
        assert config.runner_group == "Default"


class TestOrgScopeProperties:
    def test_org_scope_property(self):
        config = RunnerConfig(github_access_token="ghp_t", github_org="r3dlex")
        assert config.runner_scope == "org"

    def test_repo_scope_property(self):
        config = RunnerConfig(github_access_token="ghp_t", github_repository="owner/repo")
        assert config.runner_scope == "repo"

    def test_org_runner_url(self):
        config = RunnerConfig(github_access_token="ghp_t", github_org="r3dlex")
        assert config.runner_url == "https://github.com/r3dlex"

    def test_repo_runner_url(self):
        config = RunnerConfig(github_access_token="ghp_t", github_repository="owner/repo")
        assert config.runner_url == "https://github.com/owner/repo"

    def test_org_api_path(self):
        config = RunnerConfig(github_access_token="ghp_t", github_org="r3dlex")
        assert config.api_path == "orgs/r3dlex/actions/runners"

    def test_repo_api_path(self):
        config = RunnerConfig(github_access_token="ghp_t", github_repository="owner/repo")
        assert config.api_path == "repos/owner/repo/actions/runners"

    def test_org_default_values(self):
        config = RunnerConfig(github_access_token="ghp_t", github_org="r3dlex")
        assert config.runner_name_prefix == "runner"
        assert config.runner_count == 4
        assert config.runner_labels == "self-hosted,linux,x64"
        assert config.runner_group == "Default"


class TestValidateXor:
    def test_validate_both_org_and_repo_errors(self):
        config = RunnerConfig(
            github_access_token="ghp_t",
            github_repository="owner/repo",
            github_org="r3dlex",
        )
        errors = config.validate()
        assert any("mutually exclusive" in e for e in errors)

    def test_validate_neither_errors(self):
        config = RunnerConfig(github_access_token="ghp_t")
        errors = config.validate()
        assert any("GITHUB_ORG or GITHUB_REPOSITORY" in e for e in errors)

    def test_validate_org_only_ok(self):
        config = RunnerConfig(github_access_token="ghp_t", github_org="r3dlex")
        assert config.validate() == []

    def test_validate_repo_only_ok(self):
        config = RunnerConfig(github_access_token="ghp_t", github_repository="owner/repo")
        assert config.validate() == []


class TestLoadConfig:
    def test_load_from_env(self, monkeypatch):
        monkeypatch.setenv("GITHUB_ACCESS_TOKEN", "ghp_envtest")
        monkeypatch.setenv("GITHUB_REPOSITORY", "test/repo")
        monkeypatch.setenv("GITHUB_ORG", "")
        monkeypatch.setenv("RUNNER_COUNT", "3")

        config = load_config(env_file=None)
        assert config.github_access_token == "ghp_envtest"
        assert config.github_repository == "test/repo"
        assert config.github_org is None
        assert config.runner_count == 3

    def test_load_from_env_with_org(self, monkeypatch):
        monkeypatch.setenv("GITHUB_ACCESS_TOKEN", "ghp_envtest")
        monkeypatch.setenv("GITHUB_ORG", "r3dlex")
        # GITHUB_REPOSITORY may be populated from a host .env; we only assert
        # the org takes precedence and runner_scope reflects it.
        config = load_config(env_file=None)
        assert config.github_org == "r3dlex"
        assert config.runner_scope == "org"
        assert config.api_path == "orgs/r3dlex/actions/runners"

    def test_runner_count_default_is_4(self):
        # The dataclass default for runner_count is 4 (per the lead's update).
        config = RunnerConfig(github_access_token="ghp_t", github_repository="owner/repo")
        assert config.runner_count == 4

    def test_runner_count_env_overrides_default(self, monkeypatch, tmp_path):
        # Explicit RUNNER_COUNT=2 in env overrides the new default of 4.
        # Clear any host-leaked vars first; load_dotenv uses override=False
        # so test isolation depends on a clean os.environ.
        for var in ("GITHUB_ACCESS_TOKEN", "GITHUB_REPOSITORY", "GITHUB_ORG", "RUNNER_COUNT"):
            monkeypatch.delenv(var, raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text(
            "GITHUB_ACCESS_TOKEN=ghp_envtest\nGITHUB_REPOSITORY=test/repo\nRUNNER_COUNT=2\n"
        )
        config = load_config(str(env_file))
        assert config.runner_count == 2

    def test_load_from_env_file(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GITHUB_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        monkeypatch.delenv("GITHUB_ORG", raising=False)
        monkeypatch.delenv("RUNNER_COUNT", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text(
            "GITHUB_ACCESS_TOKEN=ghp_filetest\nGITHUB_REPOSITORY=file/repo\nRUNNER_COUNT=2\n"
        )
        config = load_config(str(env_file))
        assert config.github_access_token == "ghp_filetest"
        assert config.github_repository == "file/repo"
        assert config.runner_count == 2
