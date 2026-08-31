"""Tests for the subscription/OAuth auth mode feature.

Covers: AuthMode enum, ModelSpec auth fields, RunConfig YAML loading,
adapter get_env() branching on auth_mode, Codex get_command() routing,
gateway proxy provider detection for /codex/ paths, and Docker runner
credential mount resolution.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

from harness_evaluator.adapters.codex import CodexAdapter
from harness_evaluator.gateway.models import Provider
from harness_evaluator.gateway.proxy import GatewayProxy
from harness_evaluator.gateway.store import CallStore
from harness_evaluator.orchestrator.config import AuthMode, ModelSpec, RunConfig
from harness_evaluator.runner.docker import DockerRunner

# ---------------------------------------------------------------------------
# AuthMode enum
# ---------------------------------------------------------------------------


class TestAuthModeEnum:
    def test_api_key_value(self) -> None:
        assert AuthMode.API_KEY == "api_key"

    def test_claude_oauth_value(self) -> None:
        assert AuthMode.CLAUDE_OAUTH == "claude_oauth"

    def test_codex_chatgpt_value(self) -> None:
        assert AuthMode.CODEX_CHATGPT == "codex_chatgpt"

    def test_enum_has_three_members(self) -> None:
        assert len(list(AuthMode)) == 3


# ---------------------------------------------------------------------------
# ModelSpec auth fields
# ---------------------------------------------------------------------------


class TestModelSpecAuthFields:
    def test_auth_mode_defaults_to_api_key(self) -> None:
        m = ModelSpec(name="m", provider="anthropic", api_key_env="KEY")
        assert m.auth_mode == AuthMode.API_KEY

    def test_credentials_path_defaults_to_none(self) -> None:
        m = ModelSpec(name="m", provider="anthropic", api_key_env="KEY")
        assert m.credentials_path is None

    def test_cost_mode_defaults_to_platform(self) -> None:
        m = ModelSpec(name="m", provider="anthropic", api_key_env="KEY")
        assert m.cost_mode == "platform"

    def test_claude_oauth_fields(self) -> None:
        m = ModelSpec(
            name="claude-sonnet-4-20250514",
            provider="anthropic",
            api_key_env="ANTHROPIC_API_KEY",
            auth_mode=AuthMode.CLAUDE_OAUTH,
            credentials_path="~/.claude/.credentials.json",
        )
        assert m.auth_mode == AuthMode.CLAUDE_OAUTH
        assert m.credentials_path == "~/.claude/.credentials.json"

    def test_codex_chatgpt_fields(self) -> None:
        m = ModelSpec(
            name="gpt-4o",
            provider="openai",
            api_key_env="OPENAI_API_KEY",
            auth_mode=AuthMode.CODEX_CHATGPT,
            credentials_path="~/.codex/auth.json",
            cost_mode="subscription",
        )
        assert m.auth_mode == AuthMode.CODEX_CHATGPT
        assert m.credentials_path == "~/.codex/auth.json"
        assert m.cost_mode == "subscription"

    def test_auth_mode_from_string(self) -> None:
        m = ModelSpec(
            name="m",
            provider="anthropic",
            api_key_env="KEY",
            auth_mode="claude_oauth",
        )
        assert m.auth_mode == AuthMode.CLAUDE_OAUTH


# ---------------------------------------------------------------------------
# RunConfig.from_yaml with auth_mode fields
# ---------------------------------------------------------------------------


class TestRunConfigAuthYaml:
    def _write_run_yaml(self, path: Path, models: list[dict[str, Any]]) -> None:
        data = {
            "name": "auth-test",
            "harnesses": [{"name": "claude-code", "adapter": "claude-code"}],
            "models": models,
            "tasks": ["*"],
            "task_library_path": "./tasks",
        }
        path.write_text(yaml.dump(data))

    def test_claude_oauth_loads_from_yaml(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "run.yaml"
        self._write_run_yaml(
            yaml_path,
            [
                {
                    "name": "claude-sonnet-4-20250514",
                    "provider": "anthropic",
                    "api_key_env": "ANTHROPIC_API_KEY",
                    "auth_mode": "claude_oauth",
                    "credentials_path": "~/.claude/.credentials.json",
                }
            ],
        )
        config = RunConfig.from_yaml(yaml_path)
        assert config.models[0].auth_mode == AuthMode.CLAUDE_OAUTH
        assert config.models[0].credentials_path == "~/.claude/.credentials.json"

    def test_codex_chatgpt_loads_from_yaml(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "run.yaml"
        self._write_run_yaml(
            yaml_path,
            [
                {
                    "name": "gpt-4o",
                    "provider": "openai",
                    "api_key_env": "OPENAI_API_KEY",
                    "auth_mode": "codex_chatgpt",
                    "credentials_path": "~/.codex/auth.json",
                    "cost_mode": "subscription",
                }
            ],
        )
        config = RunConfig.from_yaml(yaml_path)
        assert config.models[0].auth_mode == AuthMode.CODEX_CHATGPT
        assert config.models[0].credentials_path == "~/.codex/auth.json"
        assert config.models[0].cost_mode == "subscription"

    def test_api_key_default_from_yaml(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "run.yaml"
        self._write_run_yaml(
            yaml_path,
            [
                {
                    "name": "claude-sonnet-4-20250514",
                    "provider": "anthropic",
                    "api_key_env": "ANTHROPIC_API_KEY",
                }
            ],
        )
        config = RunConfig.from_yaml(yaml_path)
        assert config.models[0].auth_mode == AuthMode.API_KEY
        assert config.models[0].cost_mode == "platform"


# ---------------------------------------------------------------------------
# Adapter get_env tests
# ---------------------------------------------------------------------------


class TestAdapterGetEnv:
    def _make_adapter(
        self, model: ModelSpec, tmp_path: Path
    ) -> CodexAdapter:
        return CodexAdapter(
            workdir=str(tmp_path),
            model=model,
            gateway_url="http://host.docker.internal:8877",
            trace_id="cell-1",
        )

    def test_api_key_mode_sets_anthropic_key_and_base_url(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("PATH", "/usr/bin")
        model = ModelSpec(
            name="claude-sonnet-4-20250514",
            provider="anthropic",
            api_key_env="ANTHROPIC_API_KEY",
        )
        adapter = self._make_adapter(model, tmp_path)
        env = adapter.get_env()
        assert env["ANTHROPIC_API_KEY"] == "sk-test"
        assert "ANTHROPIC_BASE_URL" in env

    def test_api_key_mode_sets_openai_key_and_base_url(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("PATH", "/usr/bin")
        model = ModelSpec(
            name="gpt-4o",
            provider="openai",
            api_key_env="OPENAI_API_KEY",
        )
        adapter = self._make_adapter(model, tmp_path)
        env = adapter.get_env()
        assert env["OPENAI_API_KEY"] == "sk-test"
        assert "OPENAI_BASE_URL" in env

    def test_claude_oauth_sets_base_url_not_api_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("PATH", "/usr/bin")
        model = ModelSpec(
            name="claude-sonnet-4-20250514",
            provider="anthropic",
            api_key_env="ANTHROPIC_API_KEY",
            auth_mode=AuthMode.CLAUDE_OAUTH,
        )
        adapter = self._make_adapter(model, tmp_path)
        env = adapter.get_env()
        assert "ANTHROPIC_BASE_URL" in env
        assert "ANTHROPIC_API_KEY" not in env

    def test_claude_oauth_passes_oauth_token_from_host(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-token-123")
        monkeypatch.setenv("PATH", "/usr/bin")
        model = ModelSpec(
            name="claude-sonnet-4-20250514",
            provider="anthropic",
            api_key_env="ANTHROPIC_API_KEY",
            auth_mode=AuthMode.CLAUDE_OAUTH,
        )
        adapter = self._make_adapter(model, tmp_path)
        env = adapter.get_env()
        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth-token-123"

    def test_claude_oauth_no_token_when_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        monkeypatch.setenv("PATH", "/usr/bin")
        model = ModelSpec(
            name="claude-sonnet-4-20250514",
            provider="anthropic",
            api_key_env="ANTHROPIC_API_KEY",
            auth_mode=AuthMode.CLAUDE_OAUTH,
        )
        adapter = self._make_adapter(model, tmp_path)
        env = adapter.get_env()
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in env

    def test_codex_chatgpt_does_not_set_openai_key_or_base_url(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("PATH", "/usr/bin")
        model = ModelSpec(
            name="gpt-4o",
            provider="openai",
            api_key_env="OPENAI_API_KEY",
            auth_mode=AuthMode.CODEX_CHATGPT,
        )
        adapter = self._make_adapter(model, tmp_path)
        env = adapter.get_env()
        assert "OPENAI_API_KEY" not in env
        assert "OPENAI_BASE_URL" not in env


# ---------------------------------------------------------------------------
# Codex adapter get_command tests
# ---------------------------------------------------------------------------


class TestCodexGetCommand:
    def test_api_key_mode_uses_openai_base_url(self, tmp_path: Path) -> None:
        model = ModelSpec(
            name="gpt-4o",
            provider="openai",
            api_key_env="OPENAI_API_KEY",
        )
        adapter = CodexAdapter(
            workdir=str(tmp_path),
            model=model,
            gateway_url="http://host.docker.internal:8877",
            trace_id="cell-1",
        )
        cmd = adapter.get_command("do thing")
        config_flags = [
            cmd[i + 1] for i, c in enumerate(cmd) if c == "-c"
        ]
        assert any("openai_base_url=" in f for f in config_flags)
        assert not any("chatgpt_base_url=" in f for f in config_flags)

    def test_codex_chatgpt_mode_uses_chatgpt_base_url(
        self, tmp_path: Path
    ) -> None:
        model = ModelSpec(
            name="gpt-4o",
            provider="openai",
            api_key_env="OPENAI_API_KEY",
            auth_mode=AuthMode.CODEX_CHATGPT,
        )
        adapter = CodexAdapter(
            workdir=str(tmp_path),
            model=model,
            gateway_url="http://host.docker.internal:8877",
            trace_id="cell-1",
        )
        cmd = adapter.get_command("do thing")
        config_flags = [
            cmd[i + 1] for i, c in enumerate(cmd) if c == "-c"
        ]
        assert any("chatgpt_base_url=" in f for f in config_flags)
        chatgpt_flag = next(
            f for f in config_flags if "chatgpt_base_url=" in f
        )
        assert "/codex" in chatgpt_flag
        assert not any("openai_base_url=" in f for f in config_flags)


# ---------------------------------------------------------------------------
# Gateway proxy _detect_provider tests
# ---------------------------------------------------------------------------


class TestDetectProvider:
    def _proxy(self, tmp_path: Path) -> GatewayProxy:
        store = CallStore(str(tmp_path / "test.db"))
        return GatewayProxy(store)

    def test_codex_responses_returns_openai_chatgpt(
        self, tmp_path: Path
    ) -> None:
        proxy = self._proxy(tmp_path)
        assert proxy._detect_provider("/codex/responses") == (
            Provider.OPENAI_CHATGPT
        )

    def test_codex_non_responses_path_returns_none(
        self, tmp_path: Path
    ) -> None:
        proxy = self._proxy(tmp_path)
        assert proxy._detect_provider("/codex/something") is None

    def test_v1_messages_returns_anthropic(self, tmp_path: Path) -> None:
        proxy = self._proxy(tmp_path)
        assert proxy._detect_provider("/v1/messages") == Provider.ANTHROPIC

    def test_v1_chat_completions_returns_openai(
        self, tmp_path: Path
    ) -> None:
        proxy = self._proxy(tmp_path)
        assert (
            proxy._detect_provider("/v1/chat/completions")
            == Provider.OPENAI
        )

    def test_unknown_path_returns_none(self, tmp_path: Path) -> None:
        proxy = self._proxy(tmp_path)
        assert proxy._detect_provider("/unknown") is None

    def test_codex_responses_upstream_url_not_doubled(
        self, tmp_path: Path
    ) -> None:
        """The upstream URL for /codex/responses must not double the
        /codex path (upstream is chatgpt.com/backend-api, path is
        /codex/responses → chatgpt.com/backend-api/codex/responses).
        """
        from harness_evaluator.gateway.proxy import UPSTREAM_URLS
        upstream = UPSTREAM_URLS[Provider.OPENAI_CHATGPT]
        assert upstream == "https://chatgpt.com/backend-api"
        forwarded = f"{upstream}/codex/responses"
        assert forwarded == (
            "https://chatgpt.com/backend-api/codex/responses"
        )


# ---------------------------------------------------------------------------
# Docker runner _resolve_credential_mounts tests
# ---------------------------------------------------------------------------


class TestResolveCredentialMounts:
    @pytest.fixture
    def runner(self, tmp_path: Any) -> DockerRunner:
        return DockerRunner(
            workdir_base=str(tmp_path / "workdir"),
            gateway_db=str(tmp_path / "gateway.db"),
        )

    def test_api_key_returns_empty(
        self, runner: DockerRunner
    ) -> None:
        mounts, env, excludes = runner._resolve_credential_mounts(
            AuthMode.API_KEY, None
        )
        assert mounts == []
        assert env == {}
        assert excludes == []

    def test_claude_oauth_with_credentials_path(
        self, runner: DockerRunner, tmp_path: Path
    ) -> None:
        cred_dir = tmp_path / ".claude"
        cred_dir.mkdir()
        cred_file = cred_dir / ".credentials.json"
        cred_file.write_text("{}")
        mounts, env, excludes = runner._resolve_credential_mounts(
            AuthMode.CLAUDE_OAUTH, str(cred_file)
        )
        assert len(mounts) == 1
        assert mounts[0][1] == "/workspace/.claude"
        assert env["CLAUDE_CONFIG_DIR"] == "/workspace/.claude"
        assert ".claude" in excludes

    def test_codex_chatgpt_with_credentials_path(
        self, runner: DockerRunner, tmp_path: Path
    ) -> None:
        cred_dir = tmp_path / ".codex"
        cred_dir.mkdir()
        cred_file = cred_dir / "auth.json"
        cred_file.write_text("{}")
        mounts, env, excludes = runner._resolve_credential_mounts(
            AuthMode.CODEX_CHATGPT, str(cred_file)
        )
        assert len(mounts) == 1
        assert mounts[0][1] == "/workspace/.codex"
        assert env["CODEX_HOME"] == "/workspace/.codex"
        assert ".codex" in excludes

    def test_none_credentials_path_returns_empty(
        self, runner: DockerRunner
    ) -> None:
        mounts, env, excludes = runner._resolve_credential_mounts(
            AuthMode.CLAUDE_OAUTH, None
        )
        assert mounts == []
        assert env == {}
        assert excludes == []

    def test_missing_file_returns_empty_and_warns(
        self,
        runner: DockerRunner,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        import logging

        missing = tmp_path / "nonexistent" / "auth.json"
        with caplog.at_level(logging.WARNING):
            mounts, env, excludes = runner._resolve_credential_mounts(
                AuthMode.CODEX_CHATGPT, str(missing)
            )
        assert mounts == []
        assert env == {}
        assert excludes == []
        assert any("not found" in r.message.lower() for r in caplog.records)

    def test_claude_oauth_with_dangling_symlink(
        self, runner: DockerRunner, tmp_path: Path
    ) -> None:
        cred_dir = tmp_path / ".claude"
        cred_dir.mkdir()
        (cred_dir / ".credentials.json").write_text("{}")
        (cred_dir / "debug").mkdir()
        (cred_dir / "debug" / "latest").symlink_to(
            cred_dir / "debug" / "missing.txt"
        )
        mounts, env, excludes = runner._resolve_credential_mounts(
            AuthMode.CLAUDE_OAUTH, str(cred_dir / ".credentials.json")
        )
        assert len(mounts) == 1
        assert mounts[0][1] == "/workspace/.claude"
        assert env["CLAUDE_CONFIG_DIR"] == "/workspace/.claude"
        assert ".claude" in excludes

    def test_credential_files_are_world_readable(
        self, runner: DockerRunner, tmp_path: Path
    ) -> None:
        """Credential files must be readable by the non-root container user.

        The Docker container runs as uid=999 (harness-evaluator), not root.
        The temp dir from mkdtemp is 0700 and copied files retain source
        permissions (typically 0600). Without chmod, the container user
        gets "Permission denied" and the harness fails with "Not logged in".
        """
        cred_dir = tmp_path / ".claude"
        cred_dir.mkdir(mode=0o700)
        cred_file = cred_dir / ".credentials.json"
        cred_file.write_text('{"token": "test"}')
        cred_file.chmod(0o600)
        # Add a subdirectory with a file to test recursive chmod.
        sub = cred_dir / "cache"
        sub.mkdir(mode=0o700)
        (sub / "data.json").write_text("{}")
        (sub / "data.json").chmod(0o600)

        mounts, env, excludes = runner._resolve_credential_mounts(
            AuthMode.CLAUDE_OAUTH, str(cred_file)
        )
        assert len(mounts) == 1
        host_path = mounts[0][0]

        # The credential directory must be traversable (0o755).
        assert (Path(host_path).stat().st_mode & 0o777) == 0o755
        # Files must be readable (0o644).
        assert (Path(host_path) / ".credentials.json").stat().st_mode & 0o777 == 0o644
        # Subdirectories must be traversable.
        assert (Path(host_path) / "cache").stat().st_mode & 0o777 == 0o755
        # Files in subdirectories must be readable.
        assert (Path(host_path) / "cache" / "data.json").stat().st_mode & 0o777 == 0o644

    def test_claude_json_copied_into_credential_dir(
        self, runner: DockerRunner, tmp_path: Path
    ) -> None:
        """claude-code's ~/.claude.json must be copied into the credential mount.

        claude-code stores its main config at ~/.claude.json (in the home
        directory root, NOT inside ~/.claude/). When CLAUDE_CONFIG_DIR is set,
        claude-code looks for .claude.json inside that directory. Without
        copying it, claude-code prints "Claude configuration file not found"
        and may fail to function properly.
        """
        # Simulate the host layout: ~/.claude/ dir and ~/.claude.json file
        home_dir = tmp_path / "home"
        home_dir.mkdir()
        cred_dir = home_dir / ".claude"
        cred_dir.mkdir()
        cred_file = cred_dir / ".credentials.json"
        cred_file.write_text('{"token": "test"}')
        # The main config file lives in the home dir root, NOT in .claude/
        home_config = home_dir / ".claude.json"
        home_config.write_text('{"numStartups": 1, "installMethod": "native"}')

        mounts, env, excludes = runner._resolve_credential_mounts(
            AuthMode.CLAUDE_OAUTH, str(cred_file)
        )
        assert len(mounts) == 1
        host_path = mounts[0][0]

        # .claude.json must be present inside the mounted credential dir
        mounted_config = Path(host_path) / ".claude.json"
        assert mounted_config.exists()
        assert mounted_config.read_text() == '{"numStartups": 1, "installMethod": "native"}'
        # Permissions must be readable by the container user
        assert mounted_config.stat().st_mode & 0o777 == 0o644

    def test_claude_json_missing_does_not_error(
        self, runner: DockerRunner, tmp_path: Path
    ) -> None:
        """Missing ~/.claude.json should not cause an error."""
        cred_dir = tmp_path / ".claude"
        cred_dir.mkdir()
        cred_file = cred_dir / ".credentials.json"
        cred_file.write_text('{"token": "test"}')
        # No .claude.json in the parent directory

        mounts, env, excludes = runner._resolve_credential_mounts(
            AuthMode.CLAUDE_OAUTH, str(cred_file)
        )
        assert len(mounts) == 1
        host_path = mounts[0][0]
        # .claude.json should not exist in the mount
        assert not (Path(host_path) / ".claude.json").exists()
        # But .credentials.json should still be there
        assert (Path(host_path) / ".credentials.json").exists()


# ---------------------------------------------------------------------------
# Docker runner _build_run_args with credential mounts
# ---------------------------------------------------------------------------


class TestBuildRunArgsCredentialMounts:
    def test_credential_mounts_appear_as_v_flags(
        self, tmp_path: Any
    ) -> None:
        runner = DockerRunner(workdir_base=str(tmp_path / "runner"))
        workdir = tmp_path / "wd"
        workdir.mkdir()
        mounts = [("/host/.claude", "/workspace/.claude")]
        args = runner._build_run_args(
            workdir, {}, timeout=60, container_name="c1",
            credential_mounts=mounts,
        )
        assert "-v" in args
        assert "/host/.claude:/workspace/.claude" in args

    def test_no_credential_mounts_no_extra_v(
        self, tmp_path: Any
    ) -> None:
        runner = DockerRunner(workdir_base=str(tmp_path / "runner"))
        workdir = tmp_path / "wd"
        workdir.mkdir()
        args = runner._build_run_args(
            workdir, {}, timeout=60, container_name="c1",
        )
        v_args = [
            args[i + 1] for i, a in enumerate(args) if a == "-v"
        ]
        assert len(v_args) == 1


# ---------------------------------------------------------------------------
# Docker runner _commit_changes excludes credential paths
# ---------------------------------------------------------------------------


class TestCommitChangesExcludesCredentials:
    @patch("harness_evaluator.runner.docker.subprocess.run")
    def test_exclude_paths_passed_to_git_add(
        self,
        mock_subprocess: MagicMock,
        tmp_path: Path,
    ) -> None:
        runner = DockerRunner(workdir_base=str(tmp_path / "wd"))
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        runner._commit_changes_sync(repo_dir, [".claude", ".codex"])
        add_calls = [
            c for c in mock_subprocess.call_args_list
            if c[0][0][0] == "git" and "add" in c[0][0]
        ]
        assert len(add_calls) == 1
        add_args = add_calls[0][0][0]
        assert ":(exclude).claude" in add_args
        assert ":(exclude).codex" in add_args

    @patch("harness_evaluator.runner.docker.subprocess.run")
    def test_no_exclude_paths_no_exclude_flags(
        self,
        mock_subprocess: MagicMock,
        tmp_path: Path,
    ) -> None:
        runner = DockerRunner(workdir_base=str(tmp_path / "wd"))
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        runner._commit_changes_sync(repo_dir, None)
        add_calls = [
            c for c in mock_subprocess.call_args_list
            if c[0][0][0] == "git" and "add" in c[0][0]
        ]
        add_args = add_calls[0][0][0]
        assert not any("exclude" in str(a) for a in add_args)


# ---------------------------------------------------------------------------
# Budget engine: subscription cost_mode tests
# ---------------------------------------------------------------------------


class TestBudgetEngineSubscriptionMode:
    """Subscription-mode cells should have zero estimated cost."""

    def _make_orchestrator(
        self, tmp_path: Path, model: ModelSpec
    ):  # type: ignore[no-untyped-def]
        from harness_evaluator.orchestrator.config import (
            HarnessSpec,
            RunConfig,
        )
        from harness_evaluator.orchestrator.engine import Orchestrator
        from harness_evaluator.orchestrator.results_store import ResultsStore

        harness = HarnessSpec(
            name="claude-code", adapter="claude-code",
            observability_tier="full",
        )
        config = RunConfig(
            name="test",
            harnesses=[harness],
            models=[model],
            tasks=["swe-bugfix-001"],
            task_library_path="./tasks",
            budget_usd=100.0,
        )
        store = ResultsStore(":memory:")
        return Orchestrator(config, store)

    def test_subscription_mode_zero_cost(
        self, tmp_path: Path
    ) -> None:
        from harness_evaluator.orchestrator.config import (
            AuthMode,
            CostMode,
            HarnessSpec,
            ModelSpec,
            RunCell,
            TaskSpec,
            TaskTrack,
        )

        model = ModelSpec(
            name="claude-sonnet-4-20250514",
            provider="anthropic",
            api_key_env="ANTHROPIC_API_KEY",
            auth_mode=AuthMode.CLAUDE_OAUTH,
            cost_mode=CostMode.SUBSCRIPTION,
        )
        orch = self._make_orchestrator(tmp_path, model)
        cell = RunCell(
            run_name="test",
            harness=HarnessSpec(
                name="claude-code", adapter="claude-code",
                observability_tier="full",
            ),
            model=model,
            task=TaskSpec(
                id="swe-bugfix-001",
                name="swe-bugfix-001",
                track=TaskTrack.SWE,
                task_prompt="Fix the bug",
                repo_url="tasks/repos/swe-bugfix-001",
                test_command="echo test",
            ),
            repeat=0,
        )
        assert orch._estimate_cell_cost(cell) == 0.0

    def test_platform_mode_nonzero_cost(
        self, tmp_path: Path
    ) -> None:
        from harness_evaluator.orchestrator.config import (
            AuthMode,
            CostMode,
            HarnessSpec,
            ModelSpec,
            RunCell,
            TaskSpec,
            TaskTrack,
        )

        model = ModelSpec(
            name="claude-sonnet-4-20250514",
            provider="anthropic",
            api_key_env="ANTHROPIC_API_KEY",
            auth_mode=AuthMode.API_KEY,
            cost_mode=CostMode.PLATFORM,
        )
        orch = self._make_orchestrator(tmp_path, model)
        cell = RunCell(
            run_name="test",
            harness=HarnessSpec(
                name="claude-code", adapter="claude-code",
                observability_tier="full",
            ),
            model=model,
            task=TaskSpec(
                id="swe-bugfix-001",
                name="swe-bugfix-001",
                track=TaskTrack.SWE,
                task_prompt="Fix the bug",
                repo_url="tasks/repos/swe-bugfix-001",
                test_command="echo test",
            ),
            repeat=0,
        )
        assert orch._estimate_cell_cost(cell) > 0.0
