"""Tests for the security/correctness hardening from the multi-agent review.

Covers: input validation (task.id, repo_commit), gateway upstream SSRF guard,
OpenAI Responses usage parsing, bun test parsing, symlink-safe diff extraction,
CSV formula-injection sanitization, and budget-on-resume accounting.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from heval.gateway.models import TokenUsage
from heval.gateway.parsers.openai import _usage_from_openai_dict, parse_sse_chunk
from heval.gateway.proxy import _validate_upstream_url
from heval.orchestrator.config import TaskSpec, TaskTrack


class TestTaskSpecValidation:
    def test_task_id_traversal_rejected(self) -> None:
        for bad in ["../../etc", "a/b", "with space", "semi;colon"]:
            with pytest.raises(ValueError):
                TaskSpec(id=bad, name="x", track=TaskTrack.SWE, task_prompt="p")

    def test_task_id_valid_accepted(self) -> None:
        t = TaskSpec(id="swe-bugfix-001", name="x", track=TaskTrack.SWE, task_prompt="p")
        assert t.id == "swe-bugfix-001"

    def test_repo_commit_option_injection_rejected(self) -> None:
        for bad in ["-b evil", "--force", "a b", "$(rm -rf)"]:
            with pytest.raises(ValueError):
                TaskSpec(
                    id="ok", name="x", track=TaskTrack.SWE, task_prompt="p", repo_commit=bad
                )

    def test_repo_commit_valid_accepted(self) -> None:
        t = TaskSpec(
            id="ok", name="x", track=TaskTrack.SWE, task_prompt="p",
            repo_commit="38776ebf76a8d753e9dbca21f10836ab558fc997",
        )
        assert t.repo_commit is not None

    def test_repo_commit_none_allowed(self) -> None:
        t = TaskSpec(id="ok", name="x", track=TaskTrack.SWE, task_prompt="p")
        assert t.repo_commit is None


class TestHarnessImageResolution:
    def _harness(self, **kw):  # type: ignore[no-untyped-def]
        from heval.orchestrator.config import HarnessSpec

        return HarnessSpec(name="claude-code", adapter="claude-code", **kw)

    def test_defaults_to_run_image(self) -> None:
        h = self._harness()
        assert h.resolve_image("ghcr.io/yorch/harnessbench-runner:0.1.0") == (
            "ghcr.io/yorch/harnessbench-runner:0.1.0"
        )

    def test_explicit_docker_image_wins(self) -> None:
        h = self._harness(docker_image="myrepo/custom:tag", version="cc-2.0.0")
        assert h.resolve_image("ghcr.io/yorch/harnessbench-runner:0.1.0") == "myrepo/custom:tag"

    def test_version_becomes_tag_on_run_repo(self) -> None:
        h = self._harness(version="cc-2.0.0")
        assert h.resolve_image("ghcr.io/yorch/harnessbench-runner:0.1.0") == (
            "ghcr.io/yorch/harnessbench-runner:cc-2.0.0"
        )

    def test_version_preserves_registry_port(self) -> None:
        h = self._harness(version="v9")
        assert h.resolve_image("localhost:5000/harnessbench-runner:0.1.0") == (
            "localhost:5000/harnessbench-runner:v9"
        )

    def test_invalid_version_rejected(self) -> None:
        with pytest.raises(ValueError):
            self._harness(version="bad tag")

    def test_docker_image_leading_dash_rejected(self) -> None:
        with pytest.raises(ValueError):
            self._harness(docker_image="--privileged")


class TestUpstreamUrlValidation:
    def test_metadata_endpoint_blocked(self) -> None:
        with pytest.raises(ValueError):
            _validate_upstream_url("https://169.254.169.254/latest/meta-data/")

    def test_gce_metadata_blocked(self) -> None:
        with pytest.raises(ValueError):
            _validate_upstream_url("http://metadata.google.internal/")

    def test_non_http_scheme_blocked(self) -> None:
        with pytest.raises(ValueError):
            _validate_upstream_url("file:///etc/passwd")

    def test_loopback_allowed_for_local_testing(self) -> None:
        assert _validate_upstream_url("http://127.0.0.1:8877") == "http://127.0.0.1:8877"

    def test_provider_https_allowed(self) -> None:
        assert _validate_upstream_url("https://api.openai.com") == "https://api.openai.com"


class TestOpenAIResponsesUsage:
    def test_responses_api_usage_parsed(self) -> None:
        usage = {
            "input_tokens": 100,
            "output_tokens": 40,
            "input_tokens_details": {"cached_tokens": 20},
            "output_tokens_details": {"reasoning_tokens": 10},
        }
        result = _usage_from_openai_dict(usage)
        assert result.input_tokens == 80  # 100 - 20 cached
        assert result.output_tokens == 30  # 40 - 10 reasoning
        assert result.cache_read_tokens == 20
        assert result.reasoning_tokens == 10

    def test_chat_completions_usage_still_parsed(self) -> None:
        usage = {
            "prompt_tokens": 100,
            "completion_tokens": 40,
            "prompt_tokens_details": {"cached_tokens": 20},
            "completion_tokens_details": {"reasoning_tokens": 10},
        }
        result = _usage_from_openai_dict(usage)
        assert result.input_tokens == 80
        assert result.output_tokens == 30

    def test_responses_streaming_chunk_nested_usage(self) -> None:
        chunk = {"response": {"usage": {"input_tokens": 50, "output_tokens": 25}}}
        acc = parse_sse_chunk(chunk, TokenUsage())
        assert acc.input_tokens == 50
        assert acc.output_tokens == 25


class TestBunTestParsing:
    def _evaluator(self):  # type: ignore[no-untyped-def]
        from heval.evaluator.swe import SWEEvaluator

        return SWEEvaluator.__new__(SWEEvaluator)

    def test_bun_all_pass(self) -> None:
        passed, total, _ = self._evaluator()._parse_test_output(
            " 6 pass\n 0 fail\nRan 6 tests", 0
        )
        assert (passed, total) == (6, 6)

    def test_bun_with_failures(self) -> None:
        passed, total, _ = self._evaluator()._parse_test_output(
            " 4 pass\n 2 fail\nRan 6 tests", 1
        )
        assert (passed, total) == (4, 6)

    def test_unittest_counts_errors(self) -> None:
        passed, total, _ = self._evaluator()._parse_test_output(
            "Ran 5 tests in 0.1s\n\nFAILED (failures=1, errors=2)", 1
        )
        assert (passed, total) == (2, 5)


class TestSymlinkSafeDiff:
    def test_untracked_symlink_skipped(self, tmp_path: Path) -> None:
        from heval.evaluator.utils import get_workdir_diff

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        (repo / "README").write_text("hi\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

        # Secret file outside the repo, and an untracked symlink pointing to it.
        secret = tmp_path / "secret.txt"
        secret.write_text("TOP SECRET HOST FILE\n")
        (repo / "leak").symlink_to(secret)

        diff = get_workdir_diff(repo)
        assert "TOP SECRET HOST FILE" not in diff


class TestCsvInjectionSanitization:
    def test_formula_prefixes_neutralized(self) -> None:
        from heval.reporting.static_report import sanitize_csv_field

        assert sanitize_csv_field("=cmd|'/C calc'!A0").startswith("'=")
        assert sanitize_csv_field("+1").startswith("'+")
        assert sanitize_csv_field("-2").startswith("'-")
        assert sanitize_csv_field("@x").startswith("'@")

    def test_normal_values_untouched(self) -> None:
        from heval.reporting.static_report import sanitize_csv_field

        assert sanitize_csv_field("hello") == "hello"
        assert sanitize_csv_field(42) == 42
        assert sanitize_csv_field(None) is None
